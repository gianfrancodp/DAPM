import csv
import json
import os
import struct
import tempfile
import unittest

from PIL import Image

import dapm


class XMPContractTests(unittest.TestCase):
    def test_namespace_identity_and_attribute_order(self):
        forward = (
            '<x:xmpmeta xmlns:x="adobe:ns:meta/" '
            'xmlns:d="http://www.dji.com/drone-dji/1.0/" '
            'xmlns:c="http://ns.adobe.com/camera-raw-settings/1.0/" '
            'xmlns:m="http://ns.adobe.com/xap/1.0/">'
            '<item d:Version="1.6" c:Version="7.0" '
            'd:GpsLatitude="37.1" m:CreateDate="2026-01-01T12:00:00Z" '
            'c:HasCrop="False" d:SensorFPS="1500/100"/>'
            "</x:xmpmeta>"
        )
        reversed_order = (
            '<x:xmpmeta xmlns:m="http://ns.adobe.com/xap/1.0/" '
            'xmlns:c="http://ns.adobe.com/camera-raw-settings/1.0/" '
            'xmlns:d="http://www.dji.com/drone-dji/1.0/" '
            'xmlns:x="adobe:ns:meta/">'
            '<item d:SensorFPS="1500/100" c:HasCrop="False" '
            'm:CreateDate="2026-01-01T12:00:00Z" '
            'd:GpsLatitude="37.1" c:Version="7.0" d:Version="1.6"/>'
            "</x:xmpmeta>"
        )
        parsed_forward = dapm.parse_xmp_data(forward)
        parsed_reversed = dapm.parse_xmp_data(reversed_order)
        self.assertEqual(parsed_forward, parsed_reversed)

        fields, warnings = parsed_forward
        self.assertEqual(fields["xmp_drone_dji_Version"], "1.6")
        self.assertEqual(fields["xmp_crs_Version"], "7.0")
        self.assertEqual(fields["XMP_Gps_Lat"], "37.1")
        self.assertEqual(fields["XMP_CreateDate"], "2026-01-01T12:00:00Z")
        self.assertNotIn("Version", fields)
        self.assertTrue(
            any('local-name collision "version"' in item for item in warnings)
        )

    def test_unknown_namespace_hash_and_sanitized_collision(self):
        first = (
            '<x:xmpmeta xmlns:x="adobe:ns:meta/" '
            'xmlns:u="https://example.com/xmp/custom/1.0/">'
            '<item u:Custom-Field="value"/></x:xmpmeta>'
        )
        renamed = (
            '<x:xmpmeta xmlns:x="adobe:ns:meta/" '
            'xmlns:renamed="https://example.com/xmp/custom/1.0/">'
            '<item renamed:Custom-Field="value"/></x:xmpmeta>'
        )
        self.assertEqual(dapm.parse_xmp_data(first), dapm.parse_xmp_data(renamed))
        fields, _ = dapm.parse_xmp_data(first)
        self.assertEqual(fields["xmp_ns_63901216_Custom_Field"], "value")

        collision = (
            '<x:xmpmeta xmlns:x="adobe:ns:meta/" '
            'xmlns:u="https://example.com/xmp/custom/1.0/">'
            '<item u:Custom-Field="z" u:Custom_Field="a"/></x:xmpmeta>'
        )
        fields, warnings = dapm.parse_xmp_data(collision)
        self.assertEqual(fields["xmp_ns_63901216_Custom_Field"], "a")
        self.assertTrue(
            any("distinct expanded attribute names" in item for item in warnings)
        )

    def test_distinct_pix4d_namespace_uris(self):
        packet = (
            '<x:xmpmeta xmlns:x="adobe:ns:meta/" '
            'xmlns:a="http://pix4d.com/camera/1.0" '
            'xmlns:b="http://pix4d.com/camera/1.0/">'
            '<item a:LensPosition="1" b:LensPosition="2"/></x:xmpmeta>'
        )
        fields, _ = dapm.parse_xmp_data(packet)
        self.assertEqual(fields["xmp_camera_LensPosition"], "1")
        self.assertEqual(fields["xmp_camera_v1_LensPosition"], "2")
        self.assertNotIn("LensPosition", fields)

    def test_nonfinite_xmp_remains_text(self):
        self.assertEqual(dapm.convert_xmp_value("NaN"), "NaN")
        self.assertEqual(dapm.convert_xmp_value("Infinity"), "Infinity")
        self.assertEqual(dapm.convert_xmp_value("7.0"), 7.0)


class OutputContractTests(unittest.TestCase):
    def representative_feature(self):
        return {
            "type": "Feature",
            "geometry": {
                "type": "Point",
                "coordinates": [14.3, 37.1, 500.0],
            },
            "properties": {
                "filename": "photo.jpg",
                "filepath": "C:/photos/photo.jpg",
                "relative_filepath": "photo.jpg",
                "dapm_version": dapm.DAPM_VERSION,
                "dapm_schema_version": dapm.DAPM_SCHEMA_VERSION,
                "xmp_drone_dji_Version": 1.6,
                "xmp_crs_Version": 7.0,
            },
        }

    def test_csv_shapefile_and_mapping_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "photodb.geojson")
            feature = self.representative_feature()
            dapm.export_valid_data([feature], output)

            for extension in (
                ".csv",
                ".shp",
                ".shx",
                ".dbf",
                ".prj",
                ".cpg",
                ".fields.json",
            ):
                self.assertTrue(os.path.exists(os.path.join(directory, "photodb" + extension)))

            with open(
                os.path.join(directory, "photodb.fields.json"),
                encoding="utf-8",
            ) as handle:
                mapping = json.load(handle)
            self.assertEqual(mapping["dapm_version"], dapm.DAPM_VERSION)
            self.assertEqual(
                mapping["dapm_schema_version"], dapm.DAPM_SCHEMA_VERSION
            )
            self.assertTrue(
                any(
                    field["source_name"] == "xmp_drone_dji_Version"
                    for field in mapping["fields"]
                )
            )
            names = [field["dbf_name"] for field in mapping["fields"]]
            self.assertEqual(len(names), len(set(names)))
            self.assertTrue(all(len(name) <= 10 for name in names))

            with open(
                os.path.join(directory, "photodb.shp"), "rb"
            ) as handle:
                header = handle.read(100)
            self.assertEqual(struct.unpack(">i", header[:4])[0], 9994)
            self.assertEqual(struct.unpack("<i", header[32:36])[0], 11)

    def test_build_writes_schema_markers_and_no_nulls(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "photos")
            os.mkdir(target)
            image_path = os.path.join(target, "plain.jpg")
            Image.new("RGB", (20, 10)).save(image_path, "JPEG")

            packet = (
                '<x:xmpmeta xmlns:x="adobe:ns:meta/" '
                'xmlns:d="http://www.dji.com/drone-dji/1.0/" '
                'xmlns:c="http://ns.adobe.com/camera-raw-settings/1.0/">'
                '<item d:Version="1.6" c:Version="7.0" '
                'd:SensorFPS="1500/100" d:Nonfinite="NaN"/>'
                "</x:xmpmeta>"
            )
            with open(image_path, "ab") as handle:
                handle.write(packet.encode("utf-8"))

            output = os.path.join(directory, "photodb.geojson")
            document = dapm.build_geojson(output, target)
            self.assertEqual(document["dapm_version"], dapm.DAPM_VERSION)
            self.assertEqual(
                document["dapm_schema_version"], dapm.DAPM_SCHEMA_VERSION
            )
            self.assertEqual(document["features"], [])

            with open(output, encoding="utf-8") as handle:
                serialized = handle.read()
            self.assertNotIn(": NaN", serialized)
            self.assertNotIn(": null", serialized)

            with open(
                os.path.join(directory, "no_gps_photos.csv"),
                newline="",
                encoding="utf-8",
            ) as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["dapm_version"], dapm.DAPM_VERSION)
            self.assertEqual(
                rows[0]["dapm_schema_version"], dapm.DAPM_SCHEMA_VERSION
            )
            self.assertEqual(rows[0]["xmp_drone_dji_Version"], "1.6")
            self.assertEqual(rows[0]["xmp_crs_Version"], "7.0")
            self.assertEqual(rows[0]["xmp_drone_dji_Nonfinite"], "NaN")
            self.assertNotIn("Version", rows[0])

    def test_machine_schema_document_is_valid_json(self):
        schema_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "OUTPUT_SCHEMA.json"
        )
        with open(schema_path, encoding="utf-8") as handle:
            schema = json.load(handle)
        self.assertEqual(
            schema["$schema"],
            "https://json-schema.org/draft/2020-12/schema",
        )
        self.assertIn("featureCollection", schema["$defs"])
        self.assertIn("dbfFieldMapping", schema["$defs"])

    def test_webmap_finds_relocated_template(self):
        with tempfile.TemporaryDirectory() as directory:
            output = os.path.join(directory, "photodb.geojson")
            document = {
                "type": "FeatureCollection",
                "dapm_version": dapm.DAPM_VERSION,
                "dapm_schema_version": dapm.DAPM_SCHEMA_VERSION,
                "features": [self.representative_feature()],
            }
            with open(output, "w", encoding="utf-8") as handle:
                json.dump(document, handle)
            result = dapm.create_webmap(output)
            self.assertEqual(result, os.path.join(directory, "index.html"))
            self.assertTrue(os.path.isfile(result))


if __name__ == "__main__":
    unittest.main()
