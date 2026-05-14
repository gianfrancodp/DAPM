

'''
Drone Aerial Photo Mapper (DAPM) - Version 1.0
Author: Gianfranco Di Pietro (@gianfrancodp)
Description:
A Python tool to extract GPS and metadata from drone aerial photos (JPG/JPEG), build
a GeoJSON database, and create an interactive Leaflet.js webmap for visualization and exploration.
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
                xmp_dict[key] = attr_value
    
    except Exception as e:
        print(f"XMP parsing error: {e}")
    
    return xmp_dict

def extract_drone_metadata(filepath):
    """Extract EXIF metadata and parse XMP data for drone photos"""
    metadata = {
        "lat": None, "lon": None, "alt": None,
        "datetime": "unknown",
        "camera": "unknown",
        "gimbal_pitch": None
    }
    
    try:
        # 1. Standard EXIF extraction
        image = Image.open(filepath)
        exif_data = image._getexif()
        
        if exif_data:
            for tag_id, value in exif_data.items():
                tag = TAGS.get(tag_id, tag_id)
                if tag == "DateTimeOriginal":
                    metadata["datetime"] = value
                elif tag == "Model":
                    metadata["camera"] = str(value).replace('\x00', '').strip()
                    # metadata["camera"] = value
                elif tag == "GPSInfo":
                    gps_data = {}
                    for t in value:
                        sub_tag = GPSTAGS.get(t, t)
                        gps_data[sub_tag] = value[t]
                    
                    if 'GPSLatitude' in gps_data and 'GPSLongitude' in gps_data:
                        metadata["lat"] = get_decimal_from_dms(gps_data['GPSLatitude'], gps_data.get('GPSLatitudeRef', 'N'))
                        metadata["lon"] = get_decimal_from_dms(gps_data['GPSLongitude'], gps_data.get('GPSLongitudeRef', 'E'))
                    if 'GPSAltitude' in gps_data:
                        metadata["alt"] = float(gps_data['GPSAltitude'])

        # 2. Extract Gimbal/Drone data from XMP (Regex approach for DJI JPG files)
        # with open(filepath, "rb") as f:
        #     img_data = f.read()
        #     # Try to find GimbalPitchDegree in the XMP metadata
        #     pitch_match = re.search(b'GimbalPitchDegree="([^"]+)"', img_data)
        #     if pitch_match:
        #         metadata["gimbal_pitch"] = float(pitch_match.group(1).decode('utf-8'))
        #     # Try to find FlightYawDegree (Drone Yaw) in the XMP metadata
        #     yaw_match = re.search(b'FlightYawDegree="([^"]+)"', img_data)
        #     if yaw_match:
        #         metadata["drone_yaw"] = float(yaw_match.group(1).decode('utf-8'))
        with open(filepath, "rb") as f:
            img_data = f.read()
            
            # Find XMP metadata block
            xmp_start = img_data.find(b'<x:xmpmeta')
            if xmp_start != -1:
                xmp_end = img_data.find(b'</x:xmpmeta>', xmp_start)
                if xmp_end != -1:
                    xmp_data = img_data[xmp_start:xmp_end+12].decode('utf-8', errors='ignore')
                    xmp_dict = parse_xmp_data(xmp_data)
                    
                    # Add all XMP data to metadata, converting numeric strings where appropriate
                    for key, value in xmp_dict.items():
                        if key not in metadata:  # Don't override existing keys
                            # Try to convert to float if it looks like a number
                            try:
                                metadata[key] = float(value)
                            except (ValueError, TypeError):
                                metadata[key] = value
    except Exception as e:
        print(f"Error processing {filepath}: {e}")
        
    return metadata

def create_webmap(geojson_file, output_html='index.html', title="Drone Aerial Photo Map", author="Gianfranco Di Pietro"):
    """Create an interactive Leaflet.js webmap from GeoJSON data"""
    
    # Read GeoJSON to calculate bounds and center
    with open(geojson_file, 'r', encoding='utf-8') as f:
        geojson_data = json.load(f)
    
    # Calculate center and bounds from features
    lats = []
    lons = []
    alts = []
    for feature in geojson_data.get('features', []):
        coords = feature['geometry']['coordinates']
        lons.append(coords[0])
        lats.append(coords[1])
        if len(coords) > 2 and coords[2] is not None:
            alts.append(coords[2])
    
    if not lats or not lons:
        print("⚠️ No valid GPS coordinates found for map center")
        return
    
    center_lat = sum(lats) / len(lats)
    center_lon = sum(lons) / len(lons)
    
    # Calculate altitude range for colormap
    min_alt = min(alts) if alts else 0
    max_alt = max(alts) if alts else 100
    alt_range = max_alt - min_alt if max_alt > min_alt else 1
    
    # Extract and parse datetime values
    datetimes = []
    for feature in geojson_data.get('features', []):
        dt_str = feature['properties'].get('datetime', 'unknown')
        if dt_str and dt_str != 'unknown':
            try:
                # Try to parse datetime (format: YYYY-MM-DD HH:MM:SS or similar)
                datetimes.append(dt_str)
            except:
                pass
    
    # Get min/max datetime (lexicographic sorting works for ISO format)
    if datetimes:
        datetimes_sorted = sorted(datetimes)
        min_datetime = datetimes_sorted[0]
        max_datetime = datetimes_sorted[-1]
    else:
        min_datetime = "unknown"
        max_datetime = "unknown"
    
    # Load HTML template and format with values
    template_path = os.path.join(os.path.dirname(__file__), 'template.html')
    with open(template_path, 'r', encoding='utf-8') as f:
        html_template = f.read()
    
    html_content = html_template.format(
        title=title,
        center_lat=center_lat,
        center_lon=center_lon,
        geojsonFile=os.path.basename(geojson_file),
        geojsonData=json.dumps(geojson_data),
        author=author
    )
    
    # Write HTML file
    output_dir = os.path.dirname(OUTPUT_FILE)
    webmap_file = os.path.join(output_dir, output_html)
    
    with open(webmap_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"✅ Webmap created: {webmap_file}")
    print(f"   Open it in your browser: file://{os.path.abspath(webmap_file)}")

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
                        if key not in ["lat", "lon", "alt"]:  # Skip coordinate fields
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