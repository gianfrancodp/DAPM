

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
inputfile = "input.yaml"

# --- IMPORTS ---
import os
import json
import re
import yaml
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
    
    # HTML template with Leaflet.js, Turf.js, noUiSlider, Leaflet.Draw, and the new Title Box
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css" />
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.4/leaflet.draw.css" />
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/noUiSlider/15.7.1/nouislider.min.css" />
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background: #f0f0f0; }}
        #map {{ width: 100%; height: 100vh; }}
        
        .photo-popup {{ min-width: 250px; }}
        .photo-popup strong {{ display: block; margin-bottom: 8px; color: #333; }}
        .photo-popup p {{ margin: 5px 0; font-size: 12px; color: #666; }}
        .photo-popup a {{ display: inline-block; margin-top: 8px; padding: 5px 10px; background: #4CAF50; color: white; text-decoration: none; border-radius: 3px; font-size: 12px; }}
        .photo-popup a:hover {{ background: #45a049; }}
        
        /* New Title Box Styles */
        .title-box {{ background: white; padding: 15px; border-radius: 5px; box-shadow: 0 0 15px rgba(0,0,0,0.2); border-left: 5px solid #4CAF50; margin-top: 10px !important; margin-left: 10px !important; }}
        .title-box h1 {{ margin: 0 0 5px 0; font-size: 18px; color: #333; letter-spacing: 0.5px; }}
        .title-box .subtitle-db {{ margin: 0 0 5px 0; font-size: 13px; color: #555; }}
        .title-box .subtitle-credits {{ margin: 0; font-size: 11px; color: #888; font-style: italic; }}
        .title-box .subtitle-credits a {{ color: #4CAF50; font-weight: bold; text-decoration: none; }}
        .title-box .subtitle-credits a:hover {{ text-decoration: underline; }}
        
        .stats, .timeslice-control {{ background: white; padding: 15px; border-radius: 5px; box-shadow: 0 0 15px rgba(0,0,0,0.2); font-size: 14px; text-align: center;}}
        .stats h3, .timeslice-control h4 {{ margin-bottom: 10px; color: #333; }}
        .stats-item {{ margin: 5px 0; color: #666; }}
        
        /* Time Slider Control */
        .timeslice-control {{ min-width: 320px; }}
        .slider-container {{ padding: 15px 10px 25px 10px; }}
        .noUi-connect {{ background: #4CAF50; }}
        .noUi-handle {{ border-radius: 50%; cursor: grab; box-shadow: 0 1px 3px rgba(0,0,0,0.3); }}
        .noUi-handle:active {{ cursor: grabbing; }}
        .noUi-horizontal .noUi-handle {{ width: 24px; height: 24px; right: -12px; top: -5px; }}
        .noUi-handle:before, .noUi-handle:after {{ display: none; }}
        
        .time-display-grid {{ display: flex; justify-content: space-between; background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 5px; padding: 10px; margin-top: 5px; text-align: center; }}
        .time-box {{ flex: 1; }}
        .time-box:first-child {{ border-right: 1px solid #dee2e6; }}
        .time-label {{ font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; color: #888; margin-bottom: 4px; }}
        .time-value {{ font-size: 12px; font-weight: bold; color: #333; }}
        .photo-count {{ text-align: center; margin-top: 12px; font-weight: 600; color: #4CAF50; font-size: 13px; }}
        
        /* Rectangle Select Button */
        .action-btn {{ width: 100%; margin-top: 15px; padding: 10px; background-color: #2196F3; color: white; border: none; border-radius: 4px; font-weight: bold; cursor: pointer; transition: background 0.3s; }}
        .action-btn:hover {{ background-color: #0b7dda; }}
        .action-btn.active {{ background-color: #f44336; }}
        
        /* Horizontal Legend */
        .legend-horizontal {{ background: white; padding: 10px 15px; border-radius: 5px; box-shadow: 0 0 15px rgba(0,0,0,0.2); min-width: 300px; margin-bottom: 20px !important; margin-left: 10px !important; }}
        .legend-horizontal h4 {{ margin: 0 0 10px 0; color: #333; font-size: 13px; text-align: center; }}
        .color-scale-bar {{ height: 12px; width: 100%; border-radius: 4px; background: linear-gradient(to right, #0000FF, #00FF00, #FFFF00, #FF8000, #FF0000); border: 1px solid #aaa; }}        .color-ticks {{ display: flex; justify-content: space-between; margin-top: 6px; }}
        .color-ticks span {{ font-size: 10px; color: #555; position: relative; }}
        .color-ticks span::before {{ content: ''; position: absolute; top: -6px; left: 50%; transform: translateX(-50%); width: 1px; height: 5px; background: #555; }}
    </style>
</head>
<body>
    <div id="map"></div>
    
    <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet.draw/1.0.4/leaflet.draw.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/turf.js/6/turf.min.js"></script>
    <script src="https://cdnjs.cloudflare.com/ajax/libs/noUiSlider/15.7.1/nouislider.min.js"></script>
    
    <script>
        // Initialize map
        const map = L.map('map', {{
            zoomControl: false // Disable default zoom to make room for our custom title, we will re-add it below
        }}).setView([{center_lat}, {center_lon}], 16);
        
        // Define basemap layers
        const osmLayer = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
            maxZoom: 19,
            attribution: '© OpenStreetMap contributors',
            name: 'OpenStreetMap'
        }});
        
        const bingAerialLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
            maxZoom: 19,
            attribution: '© Esri, DigitalGlobe, Earthstar Geographics',
            name: 'Bing Aerials'
        }});
        
        // Add OpenStreetMap as default
        osmLayer.addTo(map);
        
        // Create basemap switcher control
        const baseMaps = {{
            "OpenStreetMap": osmLayer,
            "Bing Aerials": bingAerialLayer
        }};
        
        L.control.layers(baseMaps, {{}}, {{ position: 'topright', collapsed: true }}).addTo(map);
        
        const geojsonFile = '{os.path.basename(geojson_file)}';
        
        // Hardcoded GeoJSON data to prevent CORS policy issues
        const geojsonData = {json.dumps(geojson_data)};

        // --- TITLE PANEL (TOP-LEFT) ---
        const titleControl = L.control({{ position: 'topleft' }});
                titleControl.onAdd = function(map) {{
            let div = L.DomUtil.create('div', 'title-box');
            div.innerHTML = '<h1>{title}</h1>';
            div.innerHTML += '<p class="subtitle-db">database file: <strong>' + geojsonFile + '</strong></p>';
            div.innerHTML += '<p class="subtitle-credits">Created by <strong>{author}</strong> with <a href="https://github.com/gianfrancodp/DAPM" target="_blank">DAPM</a></p>';
            return div;
        }};
        titleControl.addTo(map);

        // Re-add Zoom control below the title
        L.control.zoom({{ position: 'topleft' }}).addTo(map);
        
        // Terrain Colormap function
        function getTerrainColor(normalizedAltitude) {{
            const colors = [
                {{ratio: 0.0, color: '#0000FF'}},
                {{ratio: 0.25, color: '#00FF00'}},
                {{ratio: 0.5, color: '#FFFF00'}},
                {{ratio: 0.75, color: '#FF8000'}},
                {{ratio: 1.0, color: '#FF0000'}}
            ];
            
            let lower = colors[0];
            let upper = colors[colors.length - 1];
            
            for (let i = 0; i < colors.length - 1; i++) {{
                if (normalizedAltitude >= colors[i].ratio && normalizedAltitude <= colors[i + 1].ratio) {{
                    lower = colors[i];
                    upper = colors[i + 1];
                    break;
                }}
            }}
            
            const range = upper.ratio - lower.ratio;
            const ratio = (normalizedAltitude - lower.ratio) / range;
            
            const hexToRGB = hex => {{
                const result = /^#?([a-f\d]{{2}})([a-f\d]{{2}})([a-f\d]{{2}})$/i.exec(hex);
                return result ? {{ r: parseInt(result[1], 16), g: parseInt(result[2], 16), b: parseInt(result[3], 16) }} : {{r: 0, g: 0, b: 0}};
            }};
            
            const lowerRGB = hexToRGB(lower.color);
            const upperRGB = hexToRGB(upper.color);
            
            const r = Math.round(lowerRGB.r + (upperRGB.r - lowerRGB.r) * ratio);
            const g = Math.round(lowerRGB.g + (upperRGB.g - lowerRGB.g) * ratio);
            const b = Math.round(lowerRGB.b + (upperRGB.b - lowerRGB.b) * ratio);
            
            return '#' + [r, g, b].map(x => {{ const hex = x.toString(16); return hex.length === 1 ? '0' + hex : hex; }}).join('');
        }}
        
        // Process hardcoded GeoJSON data
        const data = geojsonData;
        {{
            const photoMarkers = L.featureGroup();
            
            const allDatetimes = data.features.map(f => f.properties.datetime).filter(dt => dt && dt !== 'unknown').sort();
            const uniqueDatetimes = [...new Set(allDatetimes)];
            const maxDatetimeIndex = uniqueDatetimes.length - 1;
            
            let currentMinIndex = 0;
                let currentMaxIndex = maxDatetimeIndex;
                
                const allAltitudes = data.features.map(f => f.geometry.coordinates[2]).filter(alt => alt !== null && alt !== undefined);
                const minAlt = Math.min(...allAltitudes);
                const maxAlt = Math.max(...allAltitudes);
                const altRange = maxAlt - minAlt > 0 ? maxAlt - minAlt : 1;
                
                const markerData = []; // Store references for filtering and exporting
                
                data.features.forEach((feature) => {{
                    const coords = feature.geometry.coordinates;
                    const props = feature.properties;
                    const altitude = coords[2] || 0;
                    const datetime = props.datetime || 'unknown';
                    
                    const normalizedAlt = (altitude - minAlt) / altRange;
                    const markerColor = getTerrainColor(normalizedAlt);
                    
                    let popupContent = '<div class="photo-popup">';
                    popupContent += '<strong>' + props.filename + '</strong>';
                    popupContent += '<img src="' + props.filepath + '" style="width: 250px; max-width: 100%; height: auto; margin: 10px 0; border-radius: 4px; display: block;" />';
                    popupContent += '<p><strong>Date:</strong> ' + (props.datetime || 'N/A') + '</p>';
                    popupContent += '<p><strong>Camera:</strong> ' + (props.camera || 'N/A') + '</p>';
                    popupContent += '<p><strong>Altitude:</strong> ' + (coords[2] ? coords[2].toFixed(2) + ' m' : 'N/A') + '</p>';
                    popupContent += '<p><strong>GPS:</strong> ' + coords[1].toFixed(6) + ', ' + coords[0].toFixed(6) + '</p>';
                    popupContent += '<a href="#" onclick="event.preventDefault(); alert(\\'Filepath: ' + props.filepath.replace(/\\\\/g, '\\\\\\\\') + '\\');">Info File</a>';
                    popupContent += '</div>';
                    
                    const marker = L.circleMarker([coords[1], coords[0]], {{
                        radius: 6, fillColor: markerColor, color: '#333333', weight: 1.5, opacity: 0.9, fillOpacity: 0.8
                    }}).bindPopup(popupContent);
                    
                    markerData.push({{
                        marker: marker,
                        datetimeIndex: uniqueDatetimes.indexOf(datetime),
                        feature: feature // Save raw feature data for CSV export
                    }});
                    
                    photoMarkers.addLayer(marker);
                }});
                
                function updateMarkersByTime(minIdx, maxIdx) {{
                    markerData.forEach(md => {{
                        if (md.datetimeIndex >= minIdx && md.datetimeIndex <= maxIdx) {{
                            photoMarkers.addLayer(md.marker);
                        }} else {{
                            photoMarkers.removeLayer(md.marker);
                        }}
                    }});
                }}
                
                map.addLayer(photoMarkers);
                if (photoMarkers.getLayers().length > 0) {{
                    map.fitBounds(photoMarkers.getBounds(), {{ padding: [50, 50] }});
                }}
                
                // --- ADD TIMESLICE CONTROL & DRAW RECTANGLE BTN ---
                const timesliceControl = L.control({{ position: 'topright' }});
                timesliceControl.onAdd = function(map) {{
                    let div = L.DomUtil.create('div', 'timeslice-control');
                    div.innerHTML = '<h4>Time Slice Filter</h4>';
                    div.innerHTML += '<div class="slider-container"><div id="time-slider"></div></div>';
                    div.innerHTML += '<div class="time-display-grid"><div class="time-box"><div class="time-label">From</div><div id="timeMinDisplay" class="time-value">' + (uniqueDatetimes[0] || 'N/A') + '</div></div><div class="time-box"><div class="time-label">To</div><div id="timeMaxDisplay" class="time-value">' + (uniqueDatetimes[maxDatetimeIndex] || 'N/A') + '</div></div></div>';
                    div.innerHTML += '<div id="timePhotoCount" class="photo-count">Photos Visible: ' + markerData.length + '</div>';
                    
                    // Add Rectangle Select Button
                    div.innerHTML += '<button id="btn-draw-rect" class="action-btn">Select by Rectangle & Export CSV</button>';
                    return div;
                }};
                timesliceControl.addTo(map);
                
                if (maxDatetimeIndex > 0) {{
                    const slider = document.getElementById('time-slider');
                    noUiSlider.create(slider, {{ start: [0, maxDatetimeIndex], connect: true, step: 1, range: {{ 'min': 0, 'max': maxDatetimeIndex }} }});
                    slider.noUiSlider.on('update', function (values) {{
                        currentMinIndex = parseInt(values[0]);
                        currentMaxIndex = parseInt(values[1]);
                        document.getElementById('timeMinDisplay').textContent = uniqueDatetimes[currentMinIndex] || 'N/A';
                        document.getElementById('timeMaxDisplay').textContent = uniqueDatetimes[currentMaxIndex] || 'N/A';
                        updateMarkersByTime(currentMinIndex, currentMaxIndex);
                        document.getElementById('timePhotoCount').textContent = 'Photos Visible: ' + markerData.filter(md => md.datetimeIndex >= currentMinIndex && md.datetimeIndex <= currentMaxIndex).length;
                    }});
                }}
                
                // --- LEAFLET DRAW LOGIC (Select & Export) ---
                const drawControl = new L.Draw.Rectangle(map, {{ shapeOptions: {{ color: '#2196F3', weight: 2 }} }});
                const drawBtn = document.getElementById('btn-draw-rect');
                
                drawBtn.addEventListener('click', function(e) {{
                    e.preventDefault();
                    if (this.classList.contains('active')) {{
                        drawControl.disable();
                        this.classList.remove('active');
                        this.textContent = 'Select by Rectangle & Export CSV';
                    }} else {{
                        drawControl.enable();
                        this.classList.add('active');
                        this.textContent = 'Drawing... (Click & Drag on Map)';
                    }}
                }});

                map.on(L.Draw.Event.CREATED, function (e) {{
                    const layer = e.layer;
                    const bounds = layer.getBounds();
                    
                    // Reset UI button
                    drawBtn.classList.remove('active');
                    drawBtn.textContent = 'Select by Rectangle & Export CSV';
                    
                    // Highlight selected area briefly
                    map.addLayer(layer);
                    setTimeout(() => map.removeLayer(layer), 1500);
                    
                    // Filter: point must be inside bounds AND currently visible on slider
                    const selectedFeatures = [];
                    markerData.forEach(md => {{
                        const isVisible = (md.datetimeIndex >= currentMinIndex && md.datetimeIndex <= currentMaxIndex);
                        if (isVisible && bounds.contains(md.marker.getLatLng())) {{
                            selectedFeatures.push(md.feature);
                        }}
                    }});
                    
                    if (selectedFeatures.length > 0) {{
                        exportGeoJSONtoCSV(selectedFeatures);
                    }} else {{
                        alert("No visible points found in the selected area.");
                    }}
                }});
                
                // CSV Export logic
                function exportGeoJSONtoCSV(features) {{
                    if (!features || features.length === 0) return;
                    
                    // Gather all unique property keys dynamically
                    let propKeys = new Set();
                    features.forEach(f => Object.keys(f.properties).forEach(k => propKeys.add(k)));
                    propKeys = Array.from(propKeys);
                    
                    // Define headers (Geometry + Properties)
                    const headers = ['longitude', 'latitude', 'altitude', ...propKeys];
                    let csvContent = headers.join(',') + '\\n';
                    
                    features.forEach(f => {{
                        const coords = f.geometry.coordinates;
                        const row = [coords[0], coords[1], coords[2] || 0];
                        
                        propKeys.forEach(key => {{
                            let val = f.properties[key];
                            if (val === null || val === undefined) {{
                                row.push('');
                            }} else if (typeof val === 'string') {{
                                row.push('"' + val.replace(/"/g, '""') + '"'); // Escape double quotes
                            }} else {{
                                row.push(val);
                            }}
                        }});
                        csvContent += row.join(',') + '\\n';
                    }});
                    
                    // Trigger browser download
                    const blob = new Blob([csvContent], {{ type: 'text/csv;charset=utf-8;' }});
                    const link = document.createElement("a");
                    link.href = URL.createObjectURL(blob);
                    link.download = "drone_selection_export.csv";
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                }}

                // --- HORIZONTAL BOTTOM-LEFT LEGEND ---
                const legend = L.control({{ position: 'bottomleft' }});
                legend.onAdd = function(map) {{
                    let div = L.DomUtil.create('div', 'legend-horizontal');
                    div.innerHTML = '<h4>Altitude Photo (meters)</h4>';
                    div.innerHTML += '<div class="color-scale-bar"></div>';
                    div.innerHTML += '<div class="color-ticks">' +
                        '<span>' + minAlt.toFixed(1) + '</span>' +
                        '<span>' + (minAlt + altRange * 0.25).toFixed(1) + '</span>' +
                        '<span>' + (minAlt + altRange * 0.5).toFixed(1) + '</span>' +
                        '<span>' + (minAlt + altRange * 0.75).toFixed(1) + '</span>' +
                        '<span>' + maxAlt.toFixed(1) + '</span>' +
                        '</div>';
                    return div;
                }};
                legend.addTo(map);
                
                // --- STATISTICS PANEL ---
                const stats = L.control({{ position: 'bottomright' }});
                stats.onAdd = function(map) {{
                    let div = L.DomUtil.create('div', 'stats');
                    div.innerHTML = '<h3>Statistics</h3>';
                    div.innerHTML += '<div class="stats-item"><strong>Total Photos:</strong> ' + data.features.length + '</div>';
                    div.innerHTML += '<div class="stats-item">Wide Altitude Range: ' + minAlt.toFixed(2) + ' to ' + maxAlt.toFixed(2) + ' m</div>';
                    return div;
                }};
                stats.addTo(map);
        }}
    </script>
</body>
</html>
"""
    
    # Write HTML file
    output_dir = os.path.dirname(OUTPUT_FILE)
    webmap_file = os.path.join(output_dir, output_html)
    
    with open(webmap_file, 'w', encoding='utf-8') as f:
        f.write(html_content)
    
    print(f"✅ Webmap created: {webmap_file}")
    print(f"   Open it in your browser: file://{os.path.abspath(webmap_file)}")

def build_geojson(OUTPUT_FILE=OUTPUT_FILE):
    features = []
    
    # Recursively walk through the target directory to find all JPG/JPEG files
    for root, dirs, files in os.walk(TARGET_DIR):
        for file in files:
            if file.lower().endswith(('.jpg', '.jpeg')):
                filepath = os.path.join(root, file)
                print(f"Analyzing: {filepath}")
                
                meta = extract_drone_metadata(filepath)
                
                # Add to features only if we have valid GPS data
                if meta["lat"] is not None and meta["lon"] is not None:
                    # Build properties dynamically from all metadata keys
                    properties = {
                        "filename": file,
                        "filepath": filepath,
                    }
                    
                    # Add all metadata fields, handling None and special values
                    for key, value in meta.items():
                        if key not in ["lat", "lon", "alt"]:  # Skip coordinate fields
                            # Convert None to null for JSON, keep other values as-is
                            if value is None:
                                properties[key] = None
                            elif isinstance(value, (int, float, str, bool)):
                                properties[key] = value
                            else:
                                # Convert other types to string
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

    # Final GeoJSON structure
    geojson_dict = {
        "type": "FeatureCollection",
        "features": features
    }
    
    # Write to output file
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(geojson_dict, f, indent=4)
    print(f"\n✅ GeoJSON created! Found {len(features)} valid photos. Saved to {OUTPUT_FILE}")
    
    # Create webmap
    create_webmap(OUTPUT_FILE)

# Execution
if __name__ == "__main__":
    # create Output directory if it doesn't exist
    os.makedirs(os.path.dirname(OUTPUT_FILE), exist_ok=True)
    build_geojson(OUTPUT_FILE)
    create_webmap(OUTPUT_FILE, title=MAP_TITLE, author=AUTHOR)