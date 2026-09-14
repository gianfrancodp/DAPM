# DAPM output field specification

Specification revision: 2026-09-14. This document defines DAPM executable/script version <code>1.1.2</code> for both the Go and Python implementations. 

The normative machine-readable companion is [OUTPUT_SCHEMA.json](OUTPUT_SCHEMA.json). It validates GeoJSON FeatureCollections and shapefile field-mapping sidecars.

## Output contract

DAPM scans JPG/JPEG files recursively. Each photograph with usable EXIF latitude and longitude becomes a GeoJSON Point Feature. Geometry coordinates are <code>[longitude, latitude, altitude]</code>. A photograph without valid EXIF latitude and longitude is written to <code>no_gps_photos.csv</code>. XMP coordinates do not supply geometry.

Every metadata record contains these strings:

| Field | JSON type | Description | Example |
| --- | --- | --- | --- |
| dapm_version | string | Version of the executable that produced the record. | 1.1.2 |
| dapm_schema_version | string | Version of the output field contract. | 2 |

The GeoJSON FeatureCollection also repeats both markers at its top level. They are properties/columns in each GeoJSON Feature, main CSV row, DBF row and no-GPS CSV row. The shapefile field-mapping sidecar repeats them at its top level.

The main CSV contains the same located records as GeoJSON. Its first columns are <code>longitude,latitude,altitude</code>, followed by the alphabetically sorted union of property names. The no-GPS CSV starts with <code>filename,filepath,relative_filepath</code>, followed by sorted metadata columns. Missing values are blank.

The shapefile represents the same points as PointZ. DBF attributes are text, field names are limited to ten ASCII characters and values are limited to 254 bytes. DAPM writes <code>&lt;output-base&gt;.fields.json</code> to map each short DBF name back to its full source name. The PRJ declares horizontal WGS 84 and the CPG declares UTF-8.

The horizontal coordinate convention follows [RFC 7946, section 3.1.1](https://www.rfc-editor.org/rfc/rfc7946#section-3.1.1). DAPM copies EXIF altitude without converting its vertical datum to RFC 7946 ellipsoidal height. Missing or invalid EXIF altitude becomes geometry/CSV value <code>0</code> for compatibility; this is not evidence of measured sea-level altitude.

## Types, absence and precedence

- The tables describe JSON values. CSV and DBF values are text and must be parsed by field name independently of locale.
- Missing properties are absent in GeoJSON and blank in CSV. An empty XMP string remains an empty string in GeoJSON and is indistinguishable from a missing value in CSV.
- Wrong EXIF type/count, truncated data and fractions with a zero denominator are omitted. Positive-only fields also omit nonpositive values.
- Successfully decoded zero is retained for exposure compensation, flash, 35 mm focal length, metering mode, white balance, colour space, DOP, speed and directions.
- EXIF wins if an XMP alias has exactly the same output key. Keys are case-sensitive.
- An XMP attribute that parses completely as a finite floating-point number becomes a JSON number. Other values remain strings. Thus <code>7.0</code> becomes <code>7</code>, while <code>False</code>, <code>1500/100</code>, <code>NaN</code> and infinity tokens remain strings.
- EXIF strings are trimmed. Dates retain source formatting. DAPM does not add a timezone, merge subseconds or normalize timestamps to UTC.

## Generated fields

Examples are illustrative.

| Field | JSON type | Source | Description / values / units | Example |
| --- | --- | --- | --- | --- |
| longitude | number | Geometry index 0; CSV/DBF | Decimal degrees east; negative west; range [-180, 180]. Not duplicated in GeoJSON properties. | 14.384606388888889 |
| latitude | number | Geometry index 1; CSV/DBF | Decimal degrees north; negative south; range [-90, 90]. Not duplicated in GeoJSON properties. | 37.15882608333333 |
| altitude | number | Geometry index 2; CSV/DBF | Signed EXIF GPS height in metres; missing becomes 0. | 514.166 |
| dapm_version | string | Executable constant | Producing DAPM version. | 1.1.2 |
| dapm_schema_version | string | Schema constant | Output contract version. | 2 |
| filename | string | Filesystem | Basename including extension; not necessarily unique. | image.JPG |
| filepath | string | Filesystem walk | Path used to read the photograph. | C:\photos\image.JPG |
| relative_filepath | string | Derived | Relative to the output directory, with forward slashes. It may contain <code>../</code>. | A/2026-01-26/image.JPG |
| megapixels | number | Derived | Width × height / 1,000,000; encoded pixel count rather than advertised sensor resolution. | 48.771072 |

## Standard EXIF fields

Storage/tag definitions are in the [CIPA DC-008 Exif standards](https://www.cipa.jp/e/std/std-sec.html). Adobe also publishes readable namespace references for [EXIF](https://developer.adobe.com/xmp/docs/xmp-namespaces/exif/) and [TIFF](https://developer.adobe.com/xmp/docs/xmp-namespaces/tiff/). DAPM serialization is defined below.

| Field | JSON type | Source | Description / values / units | Example |
| --- | --- | --- | --- | --- |
| datetime | string | ExifIFD 0x9003; fallback IFD0 0x0132 | Capture time, falling back to image modification time. Raw EXIF form; timezone unspecified. | 2026:01:26 10:13:31 |
| date_time_digitized | string | ExifIFD 0x9004 | Digitization time in raw EXIF notation. | 2026:01:26 10:13:31 |
| software | string | IFD0 0x0131 | Image-writing software or firmware. | 10.08.09.64 |
| lens_model | string | ExifIFD 0xA434 | Lens model. | Example 24mm lens |
| artist | string | IFD0 0x013B | Creator text. | Survey team |
| copyright | string | IFD0 0x8298 | Source copyright notice. | Copyright 2026 Survey team |
| image_description | string | IFD0 0x010E | Free text. | default |
| orientation | integer | IFD0 0x0112 SHORT | Orientation code; DAPM does not rotate pixels. | 1 |
| exposure_time | number | ExifIFD 0x829A RATIONAL | Seconds. Native duration takes precedence; fallback from ShutterSpeedValue 0x9201 is 2^(-value). | 0.0004 |
| f_number | number | ExifIFD 0x829D RATIONAL | Dimensionless f-number. | 1.7 |
| iso | integer | ExifIFD 0x8827 single SHORT | Photographic sensitivity. The 65535 overflow sentinel is retained. | 210 |
| exposure_bias | number | ExifIFD 0x9204 SRATIONAL | Signed exposure compensation in EV/stops. | -0.3333333333333333 |
| flash | integer | ExifIFD 0x9209 SHORT | Raw EXIF bitmask. | 0 |
| focal_length | number | ExifIFD 0x920A RATIONAL | Physical focal length in millimetres. | 6.72 |
| focal_length_35mm | integer | ExifIFD 0xA405 SHORT | 35 mm equivalent focal length in millimetres. Zero means unknown. | 24 |
| metering_mode | integer | ExifIFD 0x9207 SHORT | EXIF metering code. Zero means unknown. | 1 |
| white_balance | integer | ExifIFD 0xA403 SHORT | 0 automatic; 1 manual. This is not colour temperature. | 0 |
| color_space | integer | ExifIFD 0xA001 SHORT | 1 sRGB; 65535 uncalibrated. | 1 |
| width | integer | ExifIFD 0xA002; fallback IFD0 0x0100 | Encoded pixel width; emitted with positive height. | 8064 |
| height | integer | ExifIFD 0xA003; fallback IFD0 0x0101 | Encoded pixel height before orientation transforms. | 6048 |
| EXIF_Lat | number | GPS 0x0001 and 0x0002 | DMS converted to decimal degrees with N/S sign. | 37.15882608333333 |
| EXIF_Lon | number | GPS 0x0003 and 0x0004 | DMS converted to decimal degrees with E/W sign. | 14.384606388888889 |
| gps_dop | number | GPS 0x000B RATIONAL | Dilution of precision; GPSMeasureMode is not exported, so HDOP versus PDOP is unresolved. | 1.2 |
| gps_speed | number | GPS 0x000D RATIONAL | Speed in the unit named by gps_speed_ref; no conversion. | 12.5 |
| gps_speed_ref | string | GPS 0x000C ASCII | K km/h; M mph; N knots. | K |
| gps_img_direction | number | GPS 0x0011 RATIONAL | Image direction in degrees, conventionally [0, 360). | 0 |
| gps_img_direction_ref | string | GPS 0x0010 ASCII | T true north; M magnetic north. | T |
| gps_dest_bearing | number | GPS 0x0018 RATIONAL | Bearing toward the destination in degrees. | 90 |
| gps_dest_bearing_ref | string | GPS 0x0017 ASCII | T true north; M magnetic north. | T |

GPS altitude uses 0x0006 with reference 0x0005: 0 means above sea level and 1 means below. An absent reference defaults to 0; unsupported codes omit altitude. No geoid correction is applied.

Orientation codes are 1 normal, 2 horizontal mirror, 3 rotate 180°, 4 vertical mirror, 5 transpose, 6 rotate 90° clockwise, 7 transverse and 8 rotate 270° clockwise. Metering codes are 0 unknown, 1 average, 2 centre-weighted average, 3 spot, 4 multispot, 5 pattern, 6 partial and 255 other. For flash, bit 0 indicates firing, bits 1–2 report return status, bits 3–4 report mode, bit 5 means no flash function and bit 6 means red-eye reduction.

## Canonical XMP keys

DAPM reads XML attributes from the first literal <code>&lt;x:xmpmeta ...&gt;...&lt;/x:xmpmeta&gt;</code> packet. It does not currently read element text, RDF lists, structured values, sidecars, extended XMP or a differently prefixed outer wrapper. See the [Adobe XMP specifications](https://developer.adobe.com/xmp/docs/xmp-specifications/) and the [W3C Namespaces in XML recommendation](https://www.w3.org/TR/xml-names/).

Every retained XMP attribute has a canonical output key:

~~~text
xmp_<namespace-name>_<sanitized-local-name>
~~~

The XML prefix is not identity. DAPM resolves the prefix to the namespace URI and derives the key from that URI, so renaming or reordering prefixes and attributes cannot change the result.

| Namespace URI | Canonical namespace name | Example key |
| --- | --- | --- |
| http://ns.adobe.com/xap/1.0/ | xmp | xmp_xmp_CreateDate |
| http://ns.adobe.com/tiff/1.0/ | tiff | xmp_tiff_Make |
| http://ns.adobe.com/exif/1.0/ | exif | xmp_exif_ExposureTime |
| http://ns.adobe.com/xap/1.0/mm/ | xmp_mm | xmp_xmp_mm_DocumentID |
| http://purl.org/dc/elements/1.1/ | dc | xmp_dc_format |
| http://ns.adobe.com/camera-raw-settings/1.0/ | crs | xmp_crs_Version |
| http://www.dji.com/drone-dji/1.0/ | drone_dji | xmp_drone_dji_Version |
| http://ns.google.com/photos/1.0/panorama/ | gpano | xmp_gpano_ProjectionType |
| http://pix4d.com/camera/1.0 | camera | xmp_camera_LensPosition |
| http://pix4d.com/camera/1.0/ | camera_v1 | xmp_camera_v1_LensPosition |
| no namespace URI | unqualified | xmp_unqualified_Field |

For any unknown namespace URI, <code>namespace-name</code> is <code>ns_</code> followed by the first eight lowercase hexadecimal characters of SHA-256(namespace URI), as defined by [NIST FIPS 180-4](https://csrc.nist.gov/pubs/fips/180-4/upd1/final). Example:

~~~text
URI: https://example.com/xmp/custom/1.0/
key: xmp_ns_63901216_Custom_Field
~~~

The local name keeps ASCII letters, digits and underscores. Every run of other characters becomes one underscore; leading/trailing underscores are removed. An empty result becomes <code>field</code>.

Canonical collisions are never silently order-dependent. If two distinct expanded XML names sanitize to one key, DAPM warns. If repeated input produces different values for one canonical key, DAPM warns and keeps the lexicographically first source value. If the same local name appears under multiple namespace URIs, both canonical fields are retained and DAPM warns that a legacy flat name would be ambiguous.

Attributes whose local name is <code>about</code> or <code>xmptk</code>, and XML namespace declaration attributes, are ignored.

## Transition legacy aliases

Schema 2 is the one-release transition contract. Canonical fields are always written. The following aliases are additionally written only when one canonical source supplies the alias and it does not collide with another output key:

| Namespace | Canonical example | Legacy alias rule |
| --- | --- | --- |
| XMP Basic | xmp_xmp_CreateDate | CreateDate → XMP_CreateDate; ModifyDate → ModifyDate |
| TIFF | xmp_tiff_Make | Make → Make; Model → Model |
| Dublin Core | xmp_dc_format | format → format |
| DJI | xmp_drone_dji_GpsLatitude | GpsLatitude → XMP_Gps_Lat; GpsLongitude → XMP_Gps_Lon; other local names stay unchanged |
| Camera Raw, Pix4D camera, GPano, XMP EXIF and XMP Media Management | xmp_crs_HasCrop | local name stays unchanged |
| Unknown or unqualified namespace | xmp_ns_63901216_Custom_Field | no legacy alias |

The local name <code>Version</code> never receives a legacy alias. For example, DJI <code>Version=1.6</code> and Camera Raw <code>Version=7.0</code> are exported as <code>xmp_drone_dji_Version</code> and <code>xmp_crs_Version</code>; an ambiguous <code>Version</code> field is never written.

Legacy aliases are transitional. Machine consumers should use canonical <code>xmp_...</code> keys.

## Observed XMP field dictionary

Every canonical XMP value has JSON type number or string according to lexical conversion. Further attributes are retained by the canonical wildcard rule even when absent from this table.

Adobe references: [XMP Basic](https://developer.adobe.com/xmp/docs/xmp-namespaces/xmp/), [Camera Raw](https://developer.adobe.com/xmp/docs/xmp-namespaces/crs/), [TIFF](https://developer.adobe.com/xmp/docs/xmp-namespaces/tiff/) and [Dublin Core](https://developer.adobe.com/xmp/docs/xmp-namespaces/dc/). Vendor context: [DJI Mavic 3 Enterprise XMP fields](https://repair.dji.com/help/content?customId=01700007024&documentType=&lang=zh-CN&paperDocType=ARTICLE&re=CN&spaceId=17) and [Zenmuse P1 manual, page 22](https://dl.djicdn.com/downloads/Zenmuse_P1/20210510/Zenmuse_P1%20_User%20Manual_EN_v1.2_3.pdf#page=22). These vendor documents do not define every model/firmware combination.

| Canonical field | Transition alias | Description / values / units | Example |
| --- | --- | --- | --- |
| xmp_tiff_Make | Make | Manufacturer. | DJI |
| xmp_tiff_Model | Model | Camera model identifier. | FC8282 |
| xmp_dc_format | format | Source MIME text. | image/jpeg |
| xmp_xmp_ModifyDate | ModifyDate | Modification time; explicit offsets are retained. | 2026-01-26T10:13:31+01:00 |
| xmp_xmp_CreateDate | XMP_CreateDate | XMP creation time, separate from EXIF datetime. | 2026-01-26T10:13:31+01:00 |
| xmp_crs_AlreadyApplied | AlreadyApplied | Camera Raw processing flag; Boolean-like text remains a string. | False |
| xmp_crs_HasCrop | HasCrop | Camera Raw crop flag. | False |
| xmp_crs_HasSettings | HasSettings | Camera Raw settings flag. | False |
| xmp_crs_Version | none | Camera Raw schema/version source value. | 7 |
| xmp_drone_dji_Version | none | DJI metadata version source value. | 1.6 |
| xmp_drone_dji_GpsLatitude | XMP_Gps_Lat | XMP latitude; does not drive geometry. | 37.158826101 |
| xmp_drone_dji_GpsLongitude | XMP_Gps_Lon | XMP longitude; does not drive geometry. | 14.384606415 |
| xmp_drone_dji_AbsoluteAltitude | AbsoluteAltitude | Vendor absolute height; usually metres. Confirm the datum. | 514.166 |
| xmp_drone_dji_RelativeAltitude | RelativeAltitude | Height relative to takeoff, usually metres. | 82.2 |
| xmp_drone_dji_AltitudeType | AltitudeType | Vendor height-estimation label. | GpsFusionAlt |
| xmp_drone_dji_GpsStatus | GpsStatus | Vendor status; not a numeric accuracy estimate. | Normal |
| xmp_drone_dji_FlightPitchDegree | FlightPitchDegree | Aircraft pitch at capture, degrees. | -11.9 |
| xmp_drone_dji_FlightRollDegree | FlightRollDegree | Aircraft roll at capture, degrees. | -7.1 |
| xmp_drone_dji_FlightYawDegree | FlightYawDegree | Aircraft yaw at capture, degrees. | 126.3 |
| xmp_drone_dji_GimbalPitchDegree | GimbalPitchDegree | Gimbal pitch at capture, degrees. | -18.3 |
| xmp_drone_dji_GimbalRollDegree | GimbalRollDegree | Gimbal roll at capture, degrees. | 0 |
| xmp_drone_dji_GimbalYawDegree | GimbalYawDegree | Gimbal yaw at capture, degrees. | 105.7 |
| xmp_drone_dji_FlightXSpeed | FlightXSpeed | Vendor X velocity; north in cited enterprise documentation. | 2 |
| xmp_drone_dji_FlightYSpeed | FlightYSpeed | Vendor Y velocity; east in cited enterprise documentation. | 11.7 |
| xmp_drone_dji_FlightZSpeed | FlightZSpeed | Vendor vertical velocity; verify sign and units for the model. | 0 |
| xmp_drone_dji_CamReverse | CamReverse | Vendor camera inversion code. | 0 |
| xmp_drone_dji_GimbalReverse | GimbalReverse | Vendor gimbal inversion code. | 0 |
| xmp_drone_dji_CameraSerialNumber | CameraSerialNumber | Identifier, not a physical quantity. | CAMERA123ABC |
| xmp_drone_dji_ProductName | ProductName | Vendor product label. | Air3 |
| xmp_drone_dji_ShutterType | ShutterType | Shutter technology label. | Electronic |
| xmp_drone_dji_SurveyingMode | SurveyingMode | Vendor survey mode flag; not a DAPM accuracy certification. | 0 |
| xmp_drone_dji_WhiteBalanceCCT | WhiteBalanceCCT | Correlated colour temperature by name, conventionally kelvin. | 5789 |
| xmp_drone_dji_SensorFPS | SensorFPS | Vendor sensor-rate field. Rational text is retained as text. | 1500/100 |
| xmp_drone_dji_SensorTemperature | SensorTemperature | Sensor temperature by name; unit/calibration not inferred. | 17 |
| xmp_camera_LensTemperature or xmp_camera_v1_LensTemperature | LensTemperature | Vendor lens temperature; unit/calibration not inferred. | 15.4 |
| xmp_camera_LensPosition or xmp_camera_v1_LensPosition | LensPosition | Internal lens/focus value; not assumed to be millimetres. | 139 |
| xmp_camera_LensInfinitePosition or xmp_camera_v1_LensInfinitePosition | LensInfinitePosition | Internal infinity-focus reference. | 143 |
| xmp_camera_FileType or xmp_camera_v1_FileType | FileType | Vendor classification rather than MIME type. | single |
| xmp_camera_SelectAngle or xmp_camera_v1_SelectAngle | SelectAngle | Uninterpreted vendor value. | empty string |
| xmp_drone_dji_SelfData | SelfData | Vendor custom data retained without structured decoding. | empty string |

Angles are retained as supplied. DAPM does not assume a rotation order, normalize angles, calibrate telemetry or validate physical plausibility.

The legacy fields <code>mp</code>, lowercase <code>make</code> and <code>camera</code> are excluded from exports. XMP TIFF Make/Model remain available through their canonical names and transition aliases.

## Shapefile field-mapping sidecar

For <code>photodb.shp</code>, DAPM writes <code>photodb.fields.json</code>. Array order is DBF column order and includes longitude, latitude and altitude:

~~~json
{
  "dapm_version": "1.1.2",
  "dapm_schema_version": "2",
  "fields": [
    {
      "dbf_name": "DAPM_SCHEM",
      "source_name": "dapm_schema_version",
      "dbf_type": "C",
      "width": 1
    }
  ]
}
~~~

<code>dbf_name</code> is the actual DBF name, <code>source_name</code> is the complete CSV/GeoJSON field name, <code>dbf_type</code> is <code>C</code> (character) and <code>width</code> is the stored byte width from 1 to 254.

## Machine consumption

Prefer GeoJSON for JSON types and missing-property distinctions. Preserve unknown canonical XMP attributes. Treat legacy aliases as compatibility data and never infer provenance from a flat alias. Use the version markers to select a parser.

Example:

~~~json
{
  "type": "FeatureCollection",
  "dapm_version": "1.1.2",
  "dapm_schema_version": "2",
  "features": [
    {
      "type": "Feature",
      "geometry": {
        "type": "Point",
        "coordinates": [14.384606388888889, 37.15882608333333, 514.166]
      },
      "properties": {
        "filename": "image.JPG",
        "filepath": "C:\\photos\\image.JPG",
        "relative_filepath": "image.JPG",
        "dapm_version": "1.1.2",
        "dapm_schema_version": "2",
        "focal_length_35mm": 24,
        "color_space": 1,
        "xmp_drone_dji_Version": 1.6,
        "xmp_crs_Version": 7
      }
    }
  ]
}
~~~

Display <code>exposure_time=0.0004</code> as 1/2500 s for people while retaining numeric seconds for machines. Floating-point values are approximations; original rational numerator/denominator pairs are not exported.

## Compatibility and verification

Schema 2 writes canonical XMP keys and selected legacy aliases for one transition release. Existing CSV, GeoJSON, shapefile and embedded-map metadata must be regenerated from the original JPEG files; replacing the executable does not alter prior exports.

The corrected EXIF reader decodes inline SHORT/BYTE values with the file byte order, handles signed compensation, uses ShutterSpeedValue only as an exposure-time fallback, reads 35 mm focal length from 0xA405 and lens model from 0xA434, exports flash as a bitmask, preserves valid zeros and fixes GPS altitude sign plus direction/bearing tags. Nonfinite XMP tokens remain text so JSON serialization stays valid.

The reader implements a subset of EXIF/XMP. It does not merge MakerNotes, resolve ISO overflow, determine a rigorous vertical datum or decode XMP RDF element content.

Go and Python regression tests cover missing/zero values, export round trips, namespace identity, unknown namespace hashing, ambiguous aliases, canonical collision warnings and reversed XML attribute order. The Go suite additionally exercises both TIFF byte orders, signed fractions, exposure precedence/fallback, GPS references and malformed binary payloads.

~~~powershell
Push-Location src/golang
go test ./...
go vet ./...
Pop-Location
python src/python/test_dapm.py -v
$env:DAPM_AUDIT_CSV = 'C:\path\photodb.csv'
$env:DAPM_EXIFTOOL = 'C:\path\exiftool.exe'
Push-Location src/golang
go test -run TestOriginalPhotosAgainstExifTool -v -count=1
Pop-Location
~~~

The 2026-09-14 integration audit matched ExifTool across ten camera fields in 16 photographs sampled from the anomalous metering, white-balance, ISO and compensation groups. This is a sample check rather than a reread of every source image.
