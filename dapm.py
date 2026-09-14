

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
- XMP coordinates are XMP_Gps_Lat and XMP_Gps_Lon; XMP_CreateDate is retained.
- Legacy duplicate fields mp, make, and camera are omitted from attributes.
- megapixels, Make, and Model are retained.
- no_gps_photos.csv is created for photos without valid EXIF coordinates.
Usage:
1. Set the TARGET_DIR variable to the directory containing your drone photos.
2. Set the OUTPUT_FILE variable to the desired output GeoJSON file path.
3. Run the script. It will generate the GeoJSON and create an index.html webmap in the same directory as the output file.   
4. Open the generated index.html in your web browser to explore your drone photo map!
Notes: 
- Ensure you have the required Python libraries installed: Pillow for image processing.
- The script is designed to handle DJI drone photos with XMP metadata, but it will also work with any JPG files that contain standard EXIF GPS data.    
- The webmap uses Leaflet.js and related libraries loaded from CDNs, so an internet connection is required to view the map properly.    
- The CSV export from the rectangle selection will include all properties found in the GeoJSON, so it may contain more fields than just the standard ones if your photos have additional metadata.  

'''

# --- CONFIGURATION ---
inputfile = "input-test.yaml"
# replace with your actual configuration file path if different

# --- IMPORTS ---
import os
import json
import re
import yaml
import csv
from PIL import Image
from PIL import ExifTags
from PIL.ExifTags import TAGS, GPSTAGS
import xml.etree.ElementTree as ET

# Read configuration from YAML file
with open(inputfile, 'r') as f:
    config = yaml.safe_load(f)

TARGET_DIR = config.get('TARGET_DIR')
OUTPUT_FILE = config.get('OUTPUT_FILE')
MAP_TITLE = config.get('MAP_TITLE')
AUTHOR = config.get('AUTHOR')


# --- FUNCTIONS ---
def get_decimal_from_dms(dms, ref):
    """Conversion from DMS (Degrees, Minutes, Seconds) to Decimal Degrees"""
    degrees = dms[0]
    minutes = dms[1] / 60.0
    seconds = dms[2] / 3600.0
    decimal = degrees + minutes + seconds
    if ref in ['S', 'W']: # Sud o Ovest sono negativi
        decimal = -decimal
    return round(decimal, 6)

def parse_xmp_data(xmp_string):
    """Parse XMP metadata and return as dictionary"""
    xmp_dict = {}
    try:
        root = ET.fromstring(xmp_string)
        
        # Namespaces commonly used in DJI XMP
        namespaces = {
            'drone': 'http://www.dji.com/drone/1.0/',
            'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#'
        }
        
        # Extract all drone-related attributes
        for elem in root.iter():
            tag = elem.tag
            # Remove namespace prefix
            if '}' in tag:
                tag = tag.split('}')[1]
            
            if elem.text and elem.text.strip():
                xmp_dict[tag] = elem.text.strip()
            
            # Also capture attributes
            for attr_name, attr_value in elem.attrib.items():
                if '}' in attr_name:
                    attr_name = attr_name.split('}')[1]
                    # Remove "Description_" prefix if it exists
                    if attr_name.startswith("Description_"):
                        key = attr_name.replace("Description_", "", 1)
                    else:
                        # key = f"{tag}_{attr_name}"
                        key = attr_name
                if key.lower() == "gpslatitude":
                    key = "XMP_Gps_Lat"
                elif key.lower() == "gpslongitude":
                    key = "XMP_Gps_Lon"
                elif key.lower() == "createdate":
                    key = "XMP_CreateDate"
                xmp_dict[key] = attr_value
    
    except Exception as e:
        print(f"XMP parsing error: {e}")
    
    return xmp_dict

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
            return round(float(numerator) / float(denominator), 6)
        return float(numerator)
    if isinstance(value, tuple):
        if len(value) == 2 and all(isinstance(v, (int, float)) for v in value):
            if value[1] != 0:
                return round(float(value[0]) / float(value[1]), 6)
            return float(value[0])
        if all(isinstance(v, (int, float)) for v in value):
            return [float(v) for v in value]
    if isinstance(value, list):
        if all(isinstance(v, (int, float)) for v in value):
            return [float(v) for v in value]
    if isinstance(value, (int, float, str, bool)):
        return value
    return str(value).replace('\x00', '').strip()


def extract_drone_metadata(filepath):
    """Extract EXIF metadata and parse XMP data for drone photos."""
    metadata = {
        "lat": None, "lon": None, "alt": None,
        "EXIF_Lat": None, "EXIF_Lon": None,
        "datetime": "unknown",
        "camera": "unknown",
        "make": None,
        "software": None,
        "lens_model": None,
        "date_time_digitized": None,
        "exposure_time": None,
        "f_number": None,
        "iso": None,
        "flash": None,
        "focal_length": None,
        "focal_length_35mm": None,
        "orientation": None,
        "image_description": None,
        "artist": None,
        "copyright": None,
        "metering_mode": None,
        "white_balance": None,
        "color_space": None,
        "exposure_bias": None,
        "gimbal_pitch": None,
        "gimbal_roll": None,
        "gimbal_yaw": None,
        "flight_yaw": None,
        "relative_altitude": None,
        "absolute_altitude": None,
        "camera_pitch": None,
        "camera_yaw": None,
        "camera_roll": None,
        "gps_dop": None,
        "gps_speed": None,
        "gps_img_direction": None,
        "gps_dest_bearing": None,
        "width": None,
        "height": None,
        "megapixels": None,
        "mp": None,
    }

    try:
        with Image.open(filepath) as image:
            width, height = image.size
            metadata["width"] = width
            metadata["height"] = height
            if width and height:
                metadata["megapixels"] = round((width * height) / 1_000_000, 2)
                metadata["mp"] = metadata["megapixels"]

            exif_data = image.getexif()
            if exif_data:
                gps_data = {}
                try:
                    gps_ifd = exif_data.get_ifd(ExifTags.IFD.GPSInfo)
                    if gps_ifd:
                        gps_data = {GPSTAGS.get(k, k): v for k, v in gps_ifd.items()}
                except Exception:
                    gps_data = {}

                for tag_id, value in exif_data.items():
                    tag = TAGS.get(tag_id, tag_id)
                    normalized = normalize_exif_value(value)

                    if tag == "DateTimeOriginal":
                        metadata["datetime"] = normalized
                    elif tag == "DateTimeDigitized":
                        metadata["date_time_digitized"] = normalized
                    elif tag == "DateTime":
                        metadata["datetime"] = normalized
                    elif tag == "Make":
                        metadata["make"] = str(normalized).replace('\x00', '').strip() if normalized is not None else None
                    elif tag == "Model":
                        metadata["camera"] = str(normalized).replace('\x00', '').strip() if normalized is not None else None
                    elif tag == "Software":
                        metadata["software"] = str(normalized).replace('\x00', '').strip() if normalized is not None else None
                    elif tag == "LensModel":
                        metadata["lens_model"] = str(normalized).replace('\x00', '').strip() if normalized is not None else None
                    elif tag == "ExposureTime":
                        metadata["exposure_time"] = normalized
                    elif tag == "FNumber":
                        metadata["f_number"] = normalized
                    elif tag in ["ISOSpeedRatings", "ISOSpeed"]:
                        metadata["iso"] = normalized
                    elif tag == "Flash":
                        metadata["flash"] = normalized
                    elif tag == "FocalLength":
                        metadata["focal_length"] = normalized
                    elif tag == "FocalLengthIn35mmFilm":
                        metadata["focal_length_35mm"] = normalized
                    elif tag == "Orientation":
                        metadata["orientation"] = normalized
                    elif tag == "ImageDescription":
                        metadata["image_description"] = str(normalized).replace('\x00', '').strip() if normalized is not None else None
                    elif tag == "Artist":
                        metadata["artist"] = str(normalized).replace('\x00', '').strip() if normalized is not None else None
                    elif tag == "Copyright":
                        metadata["copyright"] = str(normalized).replace('\x00', '').strip() if normalized is not None else None
                    elif tag == "MeteringMode":
                        metadata["metering_mode"] = normalized
                    elif tag == "WhiteBalance":
                        metadata["white_balance"] = normalized
                    elif tag == "ColorSpace":
                        metadata["color_space"] = normalized
                    elif tag == "ExposureBiasValue":
                        metadata["exposure_bias"] = normalized
                    elif tag in ["ImageWidth", "ExifImageWidth", "PixelXDimension"]:
                        metadata["width"] = int(normalized)
                    elif tag in ["ImageLength", "ExifImageHeight", "PixelYDimension"]:
                        metadata["height"] = int(normalized)

                if 'GPSLatitude' in gps_data and 'GPSLongitude' in gps_data:
                    metadata["lat"] = get_decimal_from_dms(gps_data['GPSLatitude'], gps_data.get('GPSLatitudeRef', 'N'))
                    metadata["lon"] = get_decimal_from_dms(gps_data['GPSLongitude'], gps_data.get('GPSLongitudeRef', 'E'))
                    metadata["EXIF_Lat"] = metadata["lat"]
                    metadata["EXIF_Lon"] = metadata["lon"]
                if 'GPSAltitude' in gps_data:
                    metadata["alt"] = float(gps_data['GPSAltitude'])
                if 'GPSDOP' in gps_data:
                    metadata["gps_dop"] = float(gps_data['GPSDOP'])
                if 'GPSSpeed' in gps_data:
                    metadata["gps_speed"] = float(gps_data['GPSSpeed'])
                if 'GPSImgDirection' in gps_data:
                    metadata["gps_img_direction"] = float(gps_data['GPSImgDirection'])
                if 'GPSDestBearing' in gps_data:
                    metadata["gps_dest_bearing"] = float(gps_data['GPSDestBearing'])

            if metadata["width"] and metadata["height"]:
                metadata["megapixels"] = round((metadata["width"] * metadata["height"]) / 1_000_000, 2)
                metadata["mp"] = metadata["megapixels"]

        with open(filepath, "rb") as f:
            img_data = f.read()
            xmp_start = img_data.find(b'<x:xmpmeta')
            if xmp_start != -1:
                xmp_end = img_data.find(b'</x:xmpmeta>', xmp_start)
                if xmp_end != -1:
                    xmp_data = img_data[xmp_start:xmp_end+12].decode('utf-8', errors='ignore')
                    xmp_dict = parse_xmp_data(xmp_data)
                    for key, value in xmp_dict.items():
                        if key in ('GimbalPitchDegree', 'GimbalPitch'):
                            metadata['gimbal_pitch'] = float(value)
                        elif key in ('GimbalRollDegree', 'GimbalRoll'):
                            metadata['gimbal_roll'] = float(value)
                        elif key in ('GimbalYawDegree', 'GimbalYaw'):
                            metadata['gimbal_yaw'] = float(value)
                        elif key in ('FlightYawDegree', 'FlightYaw', 'Yaw'):
                            metadata['flight_yaw'] = float(value)
                        elif key in ('RelativeAltitude', 'RelativeAltitudeM'):
                            metadata['relative_altitude'] = float(value)
                        elif key in ('AbsoluteAltitude', 'AbsoluteAltitudeM'):
                            metadata['absolute_altitude'] = float(value)
                        elif key in ('CameraPitchDegree', 'CameraPitch'):
                            metadata['camera_pitch'] = float(value)
                        elif key in ('CameraYawDegree', 'CameraYaw'):
                            metadata['camera_yaw'] = float(value)
                        elif key in ('CameraRollDegree', 'CameraRoll'):
                            metadata['camera_roll'] = float(value)
                        elif key == 'Model':
                            metadata['Model'] = str(value)
                        elif key == 'LensModel' and metadata.get('lens_model') is None:
                            metadata['lens_model'] = str(value)
                        elif key not in metadata:
                            try:
                                metadata[key] = float(value)
                            except (ValueError, TypeError):
                                metadata[key] = value
    except Exception as e:
        print(f"Error processing {filepath}: {e}")

    return metadata


def create_webmap(geojson_file, output_html='index.html', title='Drone Photo Map', author='DAPM'):
    """Create the Leaflet HTML map based on a GeoJSON file."""
    with open(geojson_file, 'r', encoding='utf-8') as f:
        geojson_data = json.load(f)

    features = geojson_data.get('features', [])
    if not features:
        print('⚠️ No valid features available for webmap generation.')
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
        print('⚠️ No valid GPS coordinates found for map center')
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

    template_path = os.path.join(os.path.dirname(__file__), 'template.html')
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

    print(f"✅ Webmap created: {webmap_file}")
    print(f"   Open it in your browser: file://{os.path.abspath(webmap_file)}")
    return webmap_file


def build_geojson(OUTPUT_FILE=OUTPUT_FILE):
    features = []
    no_gps_rows = [] 
    output_dir = os.path.dirname(OUTPUT_FILE)
    
    # Recursively walk through the target directory to find all JPG/JPEG files
    for root, dirs, files in os.walk(TARGET_DIR):
        for file in files:
            if file.lower().endswith(('.jpg', '.jpeg')):
                filepath = os.path.join(root, file)
                print(f"Analyzing: {filepath}")
                
                meta = extract_drone_metadata(filepath)
                relative_filepath = os.path.relpath(filepath, output_dir)
                
                # Add to features only if we have valid GPS data
                if meta["lat"] is not None and meta["lon"] is not None:
                    properties = {
                        "filename": file,
                        "filepath": filepath,
                        "relative_filepath": relative_filepath
                    }
                    
                    # Add all metadata fields, handling None and special values
                    for key, value in meta.items():
                        if key not in ["lat", "lon", "alt", "mp", "make", "camera"]:
                            if value is None:
                                properties[key] = None
                            elif isinstance(value, (int, float, str, bool)):
                                properties[key] = value
                            else:
                                properties[key] = str(value)
                    
                    feature = {
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [meta["lon"], meta["lat"], meta.get("alt", 0.0)]
                        },
                        "properties": properties
                    }
                    features.append(feature)
                else:
                    # no GPS data found, add to no_gps_rows for CSV output
                    print("  ⚠️ No GPS data – will be saved to no-gps CSV.")
                    row = {
                        "filename": file,
                        "filepath": filepath,
                        "relative_filepath": relative_filepath
                    }
                    for key, value in meta.items():
                        if key in ["mp", "make", "camera"]:
                            continue
                        if value is None:
                            row[key] = ""
                        elif isinstance(value, (int, float, str, bool)):
                            row[key] = value
                        else:
                            row[key] = str(value)
                    no_gps_rows.append(row)

    # Final GeoJSON structure
    geojson_dict = {
        "type": "FeatureCollection",
        "features": features
    }
    
    # Write to output file
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(geojson_dict, f, indent=4)
    print(f"\n✅ GeoJSON created! Found {len(features)} valid photos. Saved to {OUTPUT_FILE}")
    
    # write no GPS data to CSV if there are any
    if no_gps_rows:
        csv_path = os.path.join(output_dir, "no_gps_photos.csv")
        
        # extract all unique keys from no_gps_rows to ensure all metadata fields are included in the CSV
        fixed_headers = ['filename', 'filepath', 'relative_filepath']
        extra_headers = set()
        for row in no_gps_rows:
            for k in row.keys():
                if k not in fixed_headers:
                    extra_headers.add(k)
                    
        # sort extra headers alphabetically and combine with fixed headers for final CSV header order
        headers = fixed_headers + sorted(list(extra_headers))
        
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=headers)
            writer.writeheader()
            for row in no_gps_rows:
                writer.writerow(row)
                
        print(f"📋 No-GPS photos: {len(no_gps_rows)} file(s) saved to {csv_path}")



# Execution
if __name__ == "__main__":
    # create Output directory if it doesn't exist
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    build_geojson(OUTPUT_FILE)
    create_webmap(OUTPUT_FILE, title=MAP_TITLE, author=AUTHOR)