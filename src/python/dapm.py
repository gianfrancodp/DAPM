

'''
Drone Aerial Photo Mapper (DAPM) - Version 1.1.2
Author: Gianfranco Di Pietro (@gianfrancodp)
Description:
Recursively scans JPG/JPEG drone photos, extracts standard EXIF and available
XMP metadata, builds a WGS 84 GeoJSON photo database, and creates an
interactive Leaflet.js web map.
Features:
- Recursive directory scanning for drone photos
- EXIF metadata extraction (GPS, datetime, camera model)
- XMP metadata parsing for DJI-specific drone data (gimbal pitch, drone yaw, etc.)
- GeoJSON generation with dynamic properties
- Interactive Leaflet.js webmap with:
    - Custom markers colored by altitude
    - Popups showing photo metadata and file info
    - Time slice filtering with noUiSlider
    - Rectangle selection tool with CSV export of selected points
    - Dynamic statistics panel and horizontal legend
Current metadata and output conventions:
- Geometry uses [longitude, latitude, altitude].
- EXIF coordinates are EXIF_Lat and EXIF_Lon.
- Every XMP attribute uses a canonical namespace-aware xmp_* key.
- Selected unambiguous legacy XMP aliases are retained for schema 2.
- dapm_version and dapm_schema_version identify the producer and contract.
- Legacy duplicate fields mp, make, and camera are omitted from attributes.
- megapixels, Make, and Model are retained.
- Located records are exported to GeoJSON, CSV and PointZ shapefile.
- A .fields.json sidecar maps short DBF names to canonical field names.
- no_gps_photos.csv is created for photos without valid EXIF coordinates.
Usage:
1. Set TARGET_DIR and OUTPUT_FILE in input-test.yaml or another YAML file.
2. Run python src/python/dapm.py, optionally passing a YAML path.
3. Open the generated index.html webmap.
Notes: 
- Required Python libraries are Pillow and PyYAML.
- The script is designed to handle DJI drone photos with XMP metadata, but it will also work with any JPG files that contain standard EXIF GPS data.    
- The webmap uses Leaflet.js and related libraries loaded from CDNs, so an internet connection is required to view the map properly.    
- The CSV export from the rectangle selection will include all properties found in the GeoJSON, so it may contain more fields than just the standard ones if your photos have additional metadata.  

'''

# --- VERSION AND DEFAULT CONFIGURATION ---
DAPM_VERSION = "1.1.2"
DAPM_SCHEMA_VERSION = "2"

# --- IMPORTS ---
import os
import json
import re
import yaml
import csv
import hashlib
import math
import struct
import sys
from datetime import datetime
from PIL import Image
from PIL import ExifTags
from PIL.ExifTags import TAGS, GPSTAGS
import xml.etree.ElementTree as ET

DEFAULT_INPUT_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "input-test.yaml"
)


def load_config(path):
    """Load and validate the same YAML configuration accepted by the Go build."""
    with open(path, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    missing = [key for key in ("TARGET_DIR", "OUTPUT_FILE") if not config.get(key)]
    if missing:
        raise ValueError(f"Missing configuration value(s): {', '.join(missing)}")
    return config


# --- FUNCTIONS ---
def get_decimal_from_dms(dms, ref):
    """Convert EXIF degrees/minutes/seconds to signed decimal degrees."""
    degrees = float(dms[0])
    minutes = float(dms[1]) / 60.0
    seconds = float(dms[2]) / 3600.0
    decimal = degrees + minutes + seconds
    ref = normalize_exif_text(ref).upper()
    if ref in ("S", "W"):
        decimal = -decimal
    return decimal


XMP_NS_XMP = "http://ns.adobe.com/xap/1.0/"
XMP_NS_TIFF = "http://ns.adobe.com/tiff/1.0/"
XMP_NS_EXIF = "http://ns.adobe.com/exif/1.0/"
XMP_NS_XMP_MM = "http://ns.adobe.com/xap/1.0/mm/"
XMP_NS_DC = "http://purl.org/dc/elements/1.1/"
XMP_NS_CRS = "http://ns.adobe.com/camera-raw-settings/1.0/"
XMP_NS_DJI = "http://www.dji.com/drone-dji/1.0/"
XMP_NS_GPANO = "http://ns.google.com/photos/1.0/panorama/"
XMP_NS_CAMERA = "http://pix4d.com/camera/1.0"
XMP_NS_CAMERA_V1 = "http://pix4d.com/camera/1.0/"

XMP_NAMESPACE_NAMES = {
    XMP_NS_XMP: "xmp",
    XMP_NS_TIFF: "tiff",
    XMP_NS_EXIF: "exif",
    XMP_NS_XMP_MM: "xmp_mm",
    XMP_NS_DC: "dc",
    XMP_NS_CRS: "crs",
    XMP_NS_DJI: "drone_dji",
    XMP_NS_GPANO: "gpano",
    XMP_NS_CAMERA: "camera",
    XMP_NS_CAMERA_V1: "camera_v1",
}

_REPORTED_XMP_WARNINGS = set()


def sanitize_xmp_key_part(value):
    part = re.sub(r"[^A-Za-z0-9_]+", "_", value).strip("_")
    return part or "field"


def xmp_namespace_name(uri):
    if not uri:
        return "unqualified"
    if uri in XMP_NAMESPACE_NAMES:
        return XMP_NAMESPACE_NAMES[uri]
    return "ns_" + hashlib.sha256(uri.encode("utf-8")).hexdigest()[:8]


def split_expanded_xml_name(name):
    if name.startswith("{") and "}" in name:
        uri, local = name[1:].split("}", 1)
        return uri, local
    return "", name


def canonical_xmp_key(namespace_uri, local_name):
    return "xmp_{}_{}".format(
        xmp_namespace_name(namespace_uri),
        sanitize_xmp_key_part(local_name),
    )


def legacy_xmp_alias(namespace_uri, local_name):
    """Return the temporary schema-2 alias, or None when no safe alias exists."""
    if local_name.lower() == "version":
        return None
    lowered = local_name.lower()
    if namespace_uri == XMP_NS_XMP:
        if lowered == "createdate":
            return "XMP_CreateDate"
        if lowered == "modifydate":
            return "ModifyDate"
    elif namespace_uri == XMP_NS_TIFF and local_name in ("Make", "Model"):
        return local_name
    elif namespace_uri == XMP_NS_DC and lowered == "format":
        return "format"
    elif namespace_uri == XMP_NS_DJI:
        if lowered == "gpslatitude":
            return "XMP_Gps_Lat"
        if lowered == "gpslongitude":
            return "XMP_Gps_Lon"
        return local_name
    elif namespace_uri in (
        XMP_NS_CRS,
        XMP_NS_CAMERA,
        XMP_NS_CAMERA_V1,
        XMP_NS_GPANO,
        XMP_NS_EXIF,
        XMP_NS_XMP_MM,
    ):
        return local_name
    return None

def parse_xmp_data(xmp_string):
    """Return canonical/transition XMP fields and deterministic warnings."""
    try:
        root = ET.fromstring(xmp_string)
    except ET.ParseError as error:
        return {}, ["XMP parsing error: {}".format(error)]

    attributes = []
    for element in root.iter():
        for expanded_name, value in element.attrib.items():
            namespace_uri, local_name = split_expanded_xml_name(expanded_name)
            if local_name in ("about", "xmptk") or local_name == "xmlns":
                continue
            attributes.append(
                (
                    namespace_uri,
                    local_name,
                    value,
                    canonical_xmp_key(namespace_uri, local_name),
                )
            )

    fields = {}
    warnings = set()
    canonical_sources = {}
    local_namespaces = {}
    alias_sources = {}

    for namespace_uri, local_name, value, canonical in attributes:
        canonical_sources.setdefault(canonical, set()).add(
            (namespace_uri, local_name)
        )
        if canonical not in fields:
            fields[canonical] = value
        elif fields[canonical] != value:
            fields[canonical] = min(fields[canonical], value)
            warnings.add(
                'canonical XMP key "{}" occurs with different values; '
                "keeping the lexicographically first value".format(canonical)
            )

        local_namespaces.setdefault(local_name.lower(), set()).add(
            xmp_namespace_name(namespace_uri)
        )
        alias = legacy_xmp_alias(namespace_uri, local_name)
        if alias:
            alias_sources.setdefault(alias, set()).add(canonical)

    for canonical, sources in canonical_sources.items():
        if len(sources) > 1:
            warnings.add(
                'canonical XMP key "{}" would merge {} distinct expanded '
                "attribute names after key sanitization".format(
                    canonical, len(sources)
                )
            )

    for local_name, namespace_names in local_namespaces.items():
        if len(namespace_names) > 1:
            warnings.add(
                'XMP local-name collision "{}" across namespaces {}; '
                "canonical fields were preserved and an ambiguous legacy "
                "alias was omitted".format(
                    local_name, ", ".join(sorted(namespace_names))
                )
            )

    for alias in sorted(alias_sources):
        sources = alias_sources[alias]
        if len(sources) != 1:
            warnings.add(
                'legacy XMP alias "{}" is ambiguous and was omitted'.format(
                    alias
                )
            )
            continue
        if alias in fields:
            warnings.add(
                'legacy XMP alias "{}" collides with a canonical field and '
                "was omitted".format(alias)
            )
            continue
        canonical = next(iter(sources))
        fields[alias] = fields[canonical]

    return fields, sorted(warnings)


def report_xmp_warning(filepath, warning):
    if warning in _REPORTED_XMP_WARNINGS:
        return
    _REPORTED_XMP_WARNINGS.add(warning)
    print(
        "  WARNING: XMP warning in {}: {} "
        "(further identical warnings suppressed)".format(
            os.path.basename(filepath), warning
        )
    )


def convert_xmp_value(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return value
    return number if math.isfinite(number) else value

def normalize_exif_value(value):
    """Normalize EXIF values for JSON/CSV export."""
    if value is None:
        return None
    if isinstance(value, bytes):
        try:
            return value.decode('utf-8', 'ignore').replace('\x00', '').strip()
        except Exception:
            return value.decode('latin-1', 'ignore').replace('\x00', '').strip()
    if hasattr(value, 'numerator') and hasattr(value, 'denominator'):
        denominator = getattr(value, 'denominator', 0)
        numerator = getattr(value, 'numerator', 0)
        if denominator:
            return float(numerator) / float(denominator)
        return None
    if isinstance(value, tuple):
        if len(value) == 2 and all(isinstance(v, (int, float)) for v in value):
            if value[1] != 0:
                return float(value[0]) / float(value[1])
            return None
        if all(isinstance(v, (int, float)) for v in value):
            return [float(v) for v in value]
    if isinstance(value, list):
        if all(isinstance(v, (int, float)) for v in value):
            return [float(v) for v in value]
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value).replace('\x00', '').strip()


def normalize_exif_text(value):
    normalized = normalize_exif_value(value)
    if normalized is None:
        return ""
    return str(normalized).replace("\x00", "").strip()


def finite_float(value):
    normalized = normalize_exif_value(value)
    if isinstance(normalized, (list, tuple, bool)) or normalized is None:
        return None
    try:
        number = float(normalized)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def positive_float(value):
    number = finite_float(value)
    return number if number is not None and number > 0 else None


def nonnegative_float(value):
    number = finite_float(value)
    return number if number is not None and number >= 0 else None


def _exif_tag_values(exif_data):
    values = {}
    for tag_id, value in exif_data.items():
        values[TAGS.get(tag_id, tag_id)] = value
    try:
        exif_ifd = exif_data.get_ifd(ExifTags.IFD.Exif)
    except Exception:
        exif_ifd = {}
    for tag_id, value in exif_ifd.items():
        values[TAGS.get(tag_id, tag_id)] = value
    return values


def _gps_altitude_reference(value):
    if value is None:
        return 0
    if isinstance(value, bytes):
        if not value:
            return None
        value = value[0]
    try:
        reference = int(value)
    except (TypeError, ValueError):
        return None
    return reference if reference in (0, 1) else None


def extract_drone_metadata(filepath):
    """Extract schema-2 EXIF/XMP metadata without emitting missing properties."""
    fields = {
        "dapm_version": DAPM_VERSION,
        "dapm_schema_version": DAPM_SCHEMA_VERSION,
    }
    latitude = None
    longitude = None
    altitude = None

    try:
        with Image.open(filepath) as image:
            width, height = image.size
            if width > 0 and height > 0:
                fields["width"] = int(width)
                fields["height"] = int(height)
                fields["megapixels"] = (width * height) / 1_000_000.0

            exif_data = image.getexif()
            if exif_data:
                tagged = _exif_tag_values(exif_data)

                datetime_original = normalize_exif_text(
                    tagged.get("DateTimeOriginal")
                )
                datetime_fallback = normalize_exif_text(tagged.get("DateTime"))
                if datetime_original or datetime_fallback:
                    fields["datetime"] = datetime_original or datetime_fallback

                string_fields = {
                    "DateTimeDigitized": "date_time_digitized",
                    "Software": "software",
                    "LensModel": "lens_model",
                    "Artist": "artist",
                    "Copyright": "copyright",
                    "ImageDescription": "image_description",
                }
                for source_name, output_name in string_fields.items():
                    value = normalize_exif_text(tagged.get(source_name))
                    if value:
                        fields[output_name] = value

                exposure_time = positive_float(tagged.get("ExposureTime"))
                if exposure_time is None:
                    shutter_speed = finite_float(tagged.get("ShutterSpeedValue"))
                    if shutter_speed is not None:
                        candidate = math.pow(2.0, -shutter_speed)
                        if math.isfinite(candidate) and candidate > 0:
                            exposure_time = candidate
                if exposure_time is not None:
                    fields["exposure_time"] = exposure_time

                positive_fields = {
                    "FNumber": "f_number",
                    "FocalLength": "focal_length",
                }
                for source_name, output_name in positive_fields.items():
                    value = positive_float(tagged.get(source_name))
                    if value is not None:
                        fields[output_name] = value

                integer_positive_fields = {
                    "ISOSpeedRatings": "iso",
                    "ISOSpeed": "iso",
                    "PhotographicSensitivity": "iso",
                    "Orientation": "orientation",
                }
                for source_name, output_name in integer_positive_fields.items():
                    if output_name in fields:
                        continue
                    value = positive_float(tagged.get(source_name))
                    if value is not None:
                        fields[output_name] = int(value)

                integer_nonnegative_fields = {
                    "Flash": "flash",
                    "FocalLengthIn35mmFilm": "focal_length_35mm",
                    "MeteringMode": "metering_mode",
                    "WhiteBalance": "white_balance",
                    "ColorSpace": "color_space",
                }
                for source_name, output_name in integer_nonnegative_fields.items():
                    value = nonnegative_float(tagged.get(source_name))
                    if value is not None:
                        fields[output_name] = int(value)

                exposure_bias = finite_float(tagged.get("ExposureBiasValue"))
                if exposure_bias is not None:
                    fields["exposure_bias"] = exposure_bias

                try:
                    gps_ifd = exif_data.get_ifd(ExifTags.IFD.GPSInfo)
                    gps_data = {
                        GPSTAGS.get(tag_id, tag_id): value
                        for tag_id, value in gps_ifd.items()
                    }
                except Exception:
                    gps_data = {}

                try:
                    if "GPSLatitude" in gps_data and "GPSLongitude" in gps_data:
                        latitude_ref = normalize_exif_text(
                            gps_data.get("GPSLatitudeRef")
                        )
                        longitude_ref = normalize_exif_text(
                            gps_data.get("GPSLongitudeRef")
                        )
                        if latitude_ref not in ("N", "S") or longitude_ref not in ("E", "W"):
                            raise ValueError("invalid or missing GPS coordinate reference")
                        candidate_latitude = get_decimal_from_dms(
                            gps_data["GPSLatitude"], latitude_ref
                        )
                        candidate_longitude = get_decimal_from_dms(
                            gps_data["GPSLongitude"], longitude_ref
                        )
                        if (
                            math.isfinite(candidate_latitude)
                            and math.isfinite(candidate_longitude)
                            and -90 <= candidate_latitude <= 90
                            and -180 <= candidate_longitude <= 180
                        ):
                            latitude = candidate_latitude
                            longitude = candidate_longitude
                            fields["EXIF_Lat"] = latitude
                            fields["EXIF_Lon"] = longitude
                except (TypeError, ValueError, IndexError, ZeroDivisionError):
                    latitude = None
                    longitude = None

                gps_altitude = nonnegative_float(gps_data.get("GPSAltitude"))
                altitude_ref = _gps_altitude_reference(
                    gps_data.get("GPSAltitudeRef")
                )
                if gps_altitude is not None and altitude_ref is not None:
                    altitude = -gps_altitude if altitude_ref == 1 else gps_altitude

                gps_numbers = {
                    "GPSDOP": "gps_dop",
                    "GPSSpeed": "gps_speed",
                    "GPSImgDirection": "gps_img_direction",
                    "GPSDestBearing": "gps_dest_bearing",
                }
                for source_name, output_name in gps_numbers.items():
                    value = nonnegative_float(gps_data.get(source_name))
                    if value is not None:
                        fields[output_name] = value

                gps_references = {
                    "GPSSpeedRef": "gps_speed_ref",
                    "GPSImgDirectionRef": "gps_img_direction_ref",
                    "GPSDestBearingRef": "gps_dest_bearing_ref",
                }
                for source_name, output_name in gps_references.items():
                    value = normalize_exif_text(gps_data.get(source_name))
                    if value:
                        fields[output_name] = value
    except Exception as error:
        print("Error processing {}: {}".format(filepath, error))
        return {
            "lat": latitude,
            "lon": longitude,
            "alt": altitude,
            "fields": fields,
        }

    try:
        with open(filepath, "rb") as handle:
            image_data = handle.read()
        xmp_start = image_data.find(b"<x:xmpmeta")
        if xmp_start != -1:
            closing = b"</x:xmpmeta>"
            xmp_end = image_data.find(closing, xmp_start)
            if xmp_end != -1:
                xmp_text = image_data[
                    xmp_start : xmp_end + len(closing)
                ].decode("utf-8", errors="ignore")
                xmp_fields, warnings = parse_xmp_data(xmp_text)
                for warning in warnings:
                    report_xmp_warning(filepath, warning)
                for key, raw_value in xmp_fields.items():
                    value = convert_xmp_value(raw_value)
                    if key in fields and fields[key] != value:
                        report_xmp_warning(
                            filepath,
                            'XMP field "{}" collides with an existing output '
                            "field and was omitted".format(key),
                        )
                        continue
                    fields[key] = value
    except OSError as error:
        print("Error reading XMP from {}: {}".format(filepath, error))

    return {
        "lat": latitude,
        "lon": longitude,
        "alt": altitude,
        "fields": fields,
    }


def export_headers(features):
    seen = {"longitude", "latitude", "altitude"}
    extra = []
    for feature in features:
        for key in feature["properties"]:
            if key not in seen:
                seen.add(key)
                extra.append(key)
    return ["longitude", "latitude", "altitude"] + sorted(extra)


def export_value(feature, key):
    if key == "longitude":
        return feature["geometry"]["coordinates"][0]
    if key == "latitude":
        return feature["geometry"]["coordinates"][1]
    if key == "altitude":
        return feature["geometry"]["coordinates"][2]
    return feature["properties"].get(key, "")


def write_main_csv(features, headers, path):
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for feature in features:
            writer.writerow([export_value(feature, key) for key in headers])


def dbf_name(source_name, used):
    candidate = "".join(
        character
        for character in source_name.upper()
        if character == "_"
        or "A" <= character <= "Z"
        or "0" <= character <= "9"
    )
    candidate = (candidate or "FIELD")[:10]
    base = candidate
    suffix = 2
    while candidate in used:
        suffix_text = str(suffix)
        candidate = base[: 10 - len(suffix_text)] + suffix_text
        suffix += 1
    used.add(candidate)
    return candidate


def dbf_columns(features, headers):
    used = set()
    columns = []
    for source_name in headers:
        width = 1
        for feature in features:
            value = export_value(feature, source_name)
            width = max(width, len(str(value).encode("utf-8")))
        columns.append(
            {
                "dbf_name": dbf_name(source_name, used),
                "source_name": source_name,
                "dbf_type": "C",
                "width": min(width, 254),
            }
        )
    return columns


def write_dbf(features, columns, path):
    header_length = 32 + 32 * len(columns) + 1
    record_length = 1 + sum(column["width"] for column in columns)
    now = datetime.now()

    header = bytearray(32)
    header[0] = 3
    header[1] = now.year - 1900
    header[2] = now.month
    header[3] = now.day
    struct.pack_into("<I", header, 4, len(features))
    struct.pack_into("<H", header, 8, header_length)
    struct.pack_into("<H", header, 10, record_length)

    with open(path, "wb") as handle:
        handle.write(header)
        for column in columns:
            descriptor = bytearray(32)
            name = column["dbf_name"].encode("ascii")
            descriptor[: len(name)] = name
            descriptor[11] = ord("C")
            descriptor[16] = column["width"]
            handle.write(descriptor)
        handle.write(b"\r")

        for feature in features:
            record = bytearray(b" " * record_length)
            offset = 1
            for column in columns:
                value = str(export_value(feature, column["source_name"]))
                encoded = value.encode("utf-8")[: column["width"]]
                record[offset : offset + len(encoded)] = encoded
                offset += column["width"]
            handle.write(record)
        handle.write(b"\x1a")


def shapefile_header(file_length_words, bounds):
    x_min, y_min, z_min, x_max, y_max, z_max = bounds
    header = bytearray(100)
    struct.pack_into(">i", header, 0, 9994)
    struct.pack_into(">i", header, 24, file_length_words)
    struct.pack_into("<i", header, 28, 1000)
    struct.pack_into("<i", header, 32, 11)
    struct.pack_into(
        "<8d",
        header,
        36,
        x_min,
        y_min,
        x_max,
        y_max,
        z_min,
        z_max,
        0.0,
        0.0,
    )
    return header


def write_shp(features, base_path):
    if features:
        coordinates = [
            feature["geometry"]["coordinates"] for feature in features
        ]
        x_values = [coordinate[0] for coordinate in coordinates]
        y_values = [coordinate[1] for coordinate in coordinates]
        z_values = [coordinate[2] for coordinate in coordinates]
        bounds = (
            min(x_values),
            min(y_values),
            min(z_values),
            max(x_values),
            max(y_values),
            max(z_values),
        )
    else:
        bounds = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

    with open(base_path + ".shp", "wb") as shp, open(
        base_path + ".shx", "wb"
    ) as shx:
        shp.write(shapefile_header(50 + 22 * len(features), bounds))
        shx.write(shapefile_header(50 + 4 * len(features), bounds))
        offset_words = 50
        for index, feature in enumerate(features, start=1):
            x, y, z = feature["geometry"]["coordinates"]
            content = struct.pack("<idddd", 11, x, y, z, 0.0)
            shp.write(struct.pack(">ii", index, len(content) // 2))
            shp.write(content)
            shx.write(struct.pack(">ii", offset_words, len(content) // 2))
            offset_words += 4 + len(content) // 2


def write_shapefile(features, headers, base_path):
    columns = dbf_columns(features, headers)
    write_dbf(features, columns, base_path + ".dbf")
    write_shp(features, base_path)
    with open(base_path + ".prj", "w", encoding="ascii") as handle:
        handle.write(
            'GEOGCS["WGS 84",DATUM["WGS_1984",'
            'SPHEROID["WGS 84",6378137,298.257223563]],'
            'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]]'
        )
    with open(base_path + ".cpg", "w", encoding="ascii", newline="\n") as handle:
        handle.write("UTF-8\n")
    sidecar = {
        "dapm_version": DAPM_VERSION,
        "dapm_schema_version": DAPM_SCHEMA_VERSION,
        "fields": columns,
    }
    with open(
        base_path + ".fields.json", "w", encoding="utf-8", newline="\n"
    ) as handle:
        json.dump(sidecar, handle, indent=2, allow_nan=False)
        handle.write("\n")


def export_valid_data(features, output_file):
    base_path = os.path.splitext(output_file)[0]
    headers = export_headers(features)
    write_main_csv(features, headers, base_path + ".csv")
    write_shapefile(features, headers, base_path)


def write_no_gps_csv(rows, output_dir):
    fixed_headers = ["filename", "filepath", "relative_filepath"]
    extra_headers = sorted(
        {
            key
            for row in rows
            for key in row
            if key not in fixed_headers
        }
    )
    path = os.path.join(output_dir, "no_gps_photos.csv")
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fixed_headers + extra_headers
        )
        writer.writeheader()
        writer.writerows(rows)
    print(
        "No-GPS photos: {} file(s) saved to {}".format(len(rows), path)
    )


def create_webmap(geojson_file, output_html='index.html', title='Drone Photo Map', author='DAPM'):
    """Create the Leaflet HTML map based on a GeoJSON file."""
    with open(geojson_file, 'r', encoding='utf-8') as f:
        geojson_data = json.load(f)

    features = geojson_data.get('features', [])
    if not features:
        print('WARNING: No valid features available for webmap generation.')
        return None

    lats = []
    lons = []
    alts = []
    for feature in features:
        coords = feature['geometry']['coordinates']
        lons.append(coords[0])
        lats.append(coords[1])
        if len(coords) > 2 and coords[2] is not None:
            alts.append(coords[2])

    if not lats or not lons:
        print('WARNING: No valid GPS coordinates found for map center')
        return None

    center_lat = sum(lats) / len(lats)
    center_lon = sum(lons) / len(lons)

    min_alt = min(alts) if alts else 0
    max_alt = max(alts) if alts else 100
    alt_range = max_alt - min_alt if max_alt > min_alt else 1

    datetimes = []
    for feature in features:
        dt_str = feature['properties'].get('datetime', 'unknown')
        if dt_str and dt_str != 'unknown':
            datetimes.append(dt_str)

    if datetimes:
        datetimes_sorted = sorted(datetimes)
        min_datetime = datetimes_sorted[0]
        max_datetime = datetimes_sorted[-1]
    else:
        min_datetime = 'unknown'
        max_datetime = 'unknown'

    script_directory = os.path.dirname(os.path.abspath(__file__))
    template_candidates = [
        os.path.abspath(
            os.path.join(script_directory, "..", "..", "build", "template.html")
        ),
        os.path.abspath(os.path.join(os.getcwd(), "build", "template.html")),
        os.path.abspath(os.path.join(os.getcwd(), "template.html")),
    ]
    template_path = next(
        (path for path in template_candidates if os.path.isfile(path)), None
    )
    if template_path is None:
        raise FileNotFoundError(
            "template.html not found; checked: {}".format(
                ", ".join(template_candidates)
            )
        )
    with open(template_path, 'r', encoding='utf-8') as f:
        html_template = f.read()

    html_content = html_template.format(
        title=title,
        center_lat=center_lat,
        center_lon=center_lon,
        geojsonFile=os.path.basename(geojson_file),
        geojsonData=json.dumps(geojson_data),
        author=author,
        min_alt=min_alt,
        max_alt=max_alt,
        alt_range=alt_range,
        min_datetime=min_datetime,
        max_datetime=max_datetime,
    )

    output_dir = os.path.dirname(geojson_file)
    webmap_file = os.path.join(output_dir, output_html)
    with open(webmap_file, 'w', encoding='utf-8') as f:
        f.write(html_content)

    print(f"Webmap created: {webmap_file}")
    print(f"   Open it in your browser: file://{os.path.abspath(webmap_file)}")
    return webmap_file


def build_geojson(output_file, target_dir):
    features = []
    no_gps_rows = []
    output_dir = os.path.dirname(output_file) or "."

    for root, directories, files in os.walk(target_dir):
        directories.sort()
        files.sort()
        for filename in files:
            if not filename.lower().endswith((".jpg", ".jpeg")):
                continue

            filepath = os.path.join(root, filename)
            print("Analyzing: {}".format(filepath))
            metadata = extract_drone_metadata(filepath)
            relative_filepath = os.path.relpath(filepath, output_dir)
            relative_filepath = relative_filepath.replace("\\", "/")
            base_properties = {
                "filename": filename,
                "filepath": filepath,
                "relative_filepath": relative_filepath,
            }

            if metadata["lat"] is not None and metadata["lon"] is not None:
                properties = dict(base_properties)
                properties.update(metadata["fields"])
                altitude = (
                    metadata["alt"] if metadata["alt"] is not None else 0.0
                )
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [
                                metadata["lon"],
                                metadata["lat"],
                                altitude,
                            ],
                        },
                        "properties": properties,
                    }
                )
            else:
                print("  WARNING: No GPS data; saving to no-GPS CSV.")
                row = dict(base_properties)
                row.update(metadata["fields"])
                no_gps_rows.append(row)

    geojson_document = {
        "type": "FeatureCollection",
        "dapm_version": DAPM_VERSION,
        "dapm_schema_version": DAPM_SCHEMA_VERSION,
        "features": features,
    }

    with open(output_file, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(
            geojson_document,
            handle,
            indent=4,
            allow_nan=False,
        )
        handle.write("\n")

    export_valid_data(features, output_file)
    print(
        "\nGeoJSON created. Found {} valid photos. Saved to {}".format(
            len(features), output_file
        )
    )

    if no_gps_rows:
        write_no_gps_csv(no_gps_rows, output_dir)

    return geojson_document


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    config_path = arguments[0] if arguments else DEFAULT_INPUT_FILE
    config = load_config(config_path)
    target_dir = config["TARGET_DIR"]
    output_file = config["OUTPUT_FILE"]
    output_dir = os.path.dirname(output_file) or "."
    os.makedirs(output_dir, exist_ok=True)
    build_geojson(output_file, target_dir)
    create_webmap(
        output_file,
        title=config.get("MAP_TITLE") or "Drone Photo Map",
        author=config.get("AUTHOR") or "DAPM",
    )


if __name__ == "__main__":
    main()
