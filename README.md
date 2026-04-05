# DAPM
It scan recursively into folders to find Arone Aerial Photos and build a webMap with locations and other data

----
# Project Documentation: from Drone Aerial Photos to webMap
## 1. Project Overview
This project is a web-based Geographic Information System (GIS) designed to index, visualize, and analyze drone aerial photography. The system allows users to view drone flight paths on a map and click on any point on the ground to automatically retrieve the photos that most likely captured that specific area, calculating a probability score based on the drone's position, orientation, and camera angle.

## 2. Architecture
The system is built on a hybrid architecture utilizing Python for data processing, Django for the backend, and Leaflet.js for the frontend interface.

* **Data Processing (Python):** A standalone script using `Pillow` and regex/`ExifTool` to recursively scan directories of drone images, extracting GPS coordinates, timestamps, and XMP metadata (Yaw, Gimbal Pitch).
* **Backend (Django):** Serves the web pages, exposes the generated spatial database, and hosts the original image files via static/media routing.
* **Frontend (JavaScript):** Utilizes **Leaflet.js** for map rendering and user interaction, and **Turf.js** for client-side spatial and vector calculations.

## 3. Data Model (GeoJSON)
The core database is a static GeoJSON `FeatureCollection`. Each photo is represented as a `Point` feature with the following properties:

```json
{
  "type": "Feature",
  "geometry": {
    "type": "Point",
    "coordinates": [ <Longitude>, <Latitude>, <Altitude> ]
  },
  "properties": {
    "filename": "DJI_0001.JPG",
    "filepath": "/media/drones/DJI_0001.JPG",
    "datetime": "2026-04-05 14:30:00",
    "camera": "FC3170",
    "drone_yaw": 14.5,
    "gimbal_pitch": -90.0
  }
}
```

## 4. Core Features

### 4.1 Map Visualization & Layer Control
* Loads a base map tile layer (e.g., OpenStreetMap or Satellite).
* Parses the GeoJSON file and renders the drone's photo locations as point markers.
* Includes a Layer Control UI to toggle the visibility of the drone markers overlay.

### 4.2 Interactive Point Query (Click-to-Search)
* Users can click anywhere on the map to define a Target Point.
* The system iterates through the GeoJSON database to find photos that align with the Target Point.
* Selected photos are highlighted on the map using a distinct marker style (e.g., a different color or icon).

### 4.3 Data Popups
* Clicking on a selected drone marker opens a Leaflet popup.
* The popup displays the filename, timestamp, probability score, and a direct hyperlink to open/download the high-resolution image.

### 4.4 Data Export
* Users can download the results of their query as a CSV file.
* The CSV is generated entirely client-side using JavaScript, compiling the properties of the currently selected features.

### 4.5 Session Reset
* A "Clear Selection" function removes the query markers from the map and resets the active arrays, allowing the user to initiate a new search seamlessly.

## 5. Intersection Algorithm & Heuristics
To determine which photos captured the clicked Target Point, the system uses a 2D spatial algorithm powered by Turf.js, combined with strict heuristics to mitigate the "Infinite Horizon" problem (where a camera pitched near 0° intersects the ground at infinity).

### Step 1: Bearing and Yaw Alignment
The script calculates the bearing from the Drone Point to the Target Point using `turf.bearing()`. This bearing is compared against the drone's actual heading (`drone_yaw`). If the absolute difference falls within the camera's Horizontal Field of View (HFOV, assumed ~±35° from the center), the photo proceeds to the next check.

### Step 2: Distance and Pitch Thresholds (Heuristics)
To prevent false positives caused by distant landscape backgrounds and to simulate Ground Sample Distance (GSD) limitations, the following cut-offs are applied:
* **Max Distance Threshold:** Target Points further than a set distance (e.g., 300 meters) from the drone are automatically discarded, regardless of alignment.
* **Gimbal Pitch Threshold:** Photos with a `gimbal_pitch` higher than -15° (close to horizontal) are discarded or heavily penalized, as they are primarily panoramic rather than ground-facing.

### Step 3: Probability Scoring
If a photo passes the thresholds, a probability score (0% to 100%) is calculated based on its angular deviation. A Target Point perfectly aligned with the drone's Yaw receives a higher score, which decreases linearly as the Target Point approaches the edges of the camera's FOV.
