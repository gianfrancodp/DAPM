package main

import (
	"encoding/binary"
	"encoding/csv"
	"encoding/json"
	"math"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"testing"
)

type testTag struct {
	tag, typ uint16
	count    uint32
	data     []byte
}

func shortTag(o binary.ByteOrder, tag uint16, v uint16) testTag {
	b := make([]byte, 2)
	o.PutUint16(b, v)
	return testTag{tag, 3, 1, b}
}
func longTag(o binary.ByteOrder, tag uint16, v uint32) testTag {
	b := make([]byte, 4)
	o.PutUint32(b, v)
	return testTag{tag, 4, 1, b}
}
func fractionTag(o binary.ByteOrder, tag uint16, n, d int32, signed bool) testTag {
	b := make([]byte, 8)
	o.PutUint32(b, uint32(n))
	o.PutUint32(b[4:], uint32(d))
	typ := uint16(5)
	if signed {
		typ = 10
	}
	return testTag{tag, typ, 1, b}
}
func asciiTag(tag uint16, s string) testTag {
	return testTag{tag, 2, uint32(len(s) + 1), append([]byte(s), 0)}
}

func testTIFF(o binary.ByteOrder, exif, gps []testTag) []byte {
	root := []testTag{shortTag(o, 0x0112, 1), shortTag(o, 0x0100, 8064), longTag(o, 0x0101, 6048)}
	root = append(root, longTag(o, 0x8769, 0))
	if gps != nil {
		root = append(root, longTag(o, 0x8825, 0))
	}
	exifOff := 8 + 2 + 12*len(root) + 4
	gpsOff := exifOff + 2 + 12*len(exif) + 4
	end := gpsOff
	if gps != nil {
		end += 2 + 12*len(gps) + 4
	}
	root[3] = longTag(o, 0x8769, uint32(exifOff))
	if gps != nil {
		root[4] = longTag(o, 0x8825, uint32(gpsOff))
	}
	b := make([]byte, end)
	if o == binary.LittleEndian {
		copy(b, "II")
	} else {
		copy(b, "MM")
	}
	o.PutUint16(b[2:], 42)
	o.PutUint32(b[4:], 8)
	put := func(off int, tags []testTag) {
		o.PutUint16(b[off:], uint16(len(tags)))
		for i, e := range tags {
			at := off + 2 + 12*i
			o.PutUint16(b[at:], e.tag)
			o.PutUint16(b[at+2:], e.typ)
			o.PutUint32(b[at+4:], e.count)
			if len(e.data) <= 4 {
				copy(b[at+8:at+12], e.data)
			} else {
				o.PutUint32(b[at+8:], uint32(len(b)))
				b = append(b, e.data...)
			}
		}
	}
	put(8, root)
	put(exifOff, exif)
	if gps != nil {
		put(gpsOff, gps)
	}
	return b
}
func testJPEG(tiff []byte) []byte {
	b := []byte{0xff, 0xd8, 0xff, 0xe1, 0, 0, 'E', 'x', 'i', 'f', 0, 0}
	binary.BigEndian.PutUint16(b[4:], uint16(len(tiff)+8))
	b = append(b, tiff...)
	return append(b, 0xff, 0xd9)
}
func cameraTags(o binary.ByteOrder) []testTag {
	return []testTag{
		fractionTag(o, 0x829A, 1, 2500, false), fractionTag(o, 0x9201, 1128771, 100000, true),
		fractionTag(o, 0x829D, 17, 10, false), shortTag(o, 0x8827, 210),
		fractionTag(o, 0x9204, -1, 3, true), shortTag(o, 0x9209, 0),
		fractionTag(o, 0x920A, 672, 100, false), shortTag(o, 0xA405, 24),
		shortTag(o, 0xA406, 0), shortTag(o, 0xA001, 1), shortTag(o, 0x9207, 1),
		shortTag(o, 0xA403, 0), asciiTag(0xA433, "Lens maker"), asciiTag(0xA434, "Correct lens model"),
	}
}
func TestCameraEXIFBothByteOrders(t *testing.T) {
	for _, o := range []binary.ByteOrder{binary.LittleEndian, binary.BigEndian} {
		t.Run(o.String(), func(t *testing.T) {
			tags := cameraTags(o)
			for _, reverse := range []bool{false, true} {
				if reverse {
					for i, j := 0, len(tags)-1; i < j; i, j = i+1, j-1 {
						tags[i], tags[j] = tags[j], tags[i]
					}
				}
				r := extractEXIF(testJPEG(testTIFF(o, tags, nil)))
				if r == nil {
					t.Fatal("no EXIF")
				}
				if r.ExposureTime != 0.0004 || r.ExposureBias != -1.0/3 || r.ISO != 210 ||
					r.FocalLength35mm != 24 || r.ColorSpace != 1 || r.MeteringMode != 1 ||
					r.WhiteBalance != 0 || r.Flash != 0 || r.FNumber != 1.7 || r.FocalLength != 6.72 ||
					r.Width != 8064 || r.Height != 6048 || r.Orientation != 1 || r.LensModel != "Correct lens model" {
					t.Fatalf("wrong camera metadata: %+v", r)
				}
				if !r.Present["white_balance"] || !r.Present["flash"] {
					t.Fatal("zero lost")
				}
			}
		})
	}
}
func TestShutterFallbackAndMissingValues(t *testing.T) {
	o := binary.LittleEndian
	for _, tc := range []struct {
		name    string
		tags    []testTag
		seconds float64
	}{
		{"missing", nil, 0},
		{"one second", []testTag{fractionTag(o, 0x9201, 0, 1, true)}, 1},
		{"long exposure", []testTag{fractionTag(o, 0x9201, -1, 1, true)}, 2},
		{"invalid duration fallback", []testTag{fractionTag(o, 0x829A, 1, 0, false), fractionTag(o, 0x9201, 1, 1, true)}, 0.5},
		{"invalid only", []testTag{fractionTag(o, 0x829A, 1, 0, false)}, 0},
		{"wrong type", []testTag{shortTag(o, 0x829A, 1)}, 0},
	} {
		t.Run(tc.name, func(t *testing.T) {
			r := parseTIFFExif(testTIFF(o, tc.tags, nil))
			if r.ExposureTime != tc.seconds {
				t.Fatalf("got %g want %g", r.ExposureTime, tc.seconds)
			}
			if r.Present["exposure_bias"] || r.Present["white_balance"] {
				t.Fatal("invented missing values")
			}
		})
	}
}
func gpsTags(o binary.ByteOrder) []testTag {
	dms := func(tag uint16, n int32) testTag {
		b := append([]byte{}, fractionTag(o, tag, n, 1, false).data...)
		b = append(b, fractionTag(o, tag, 0, 1, false).data...)
		b = append(b, fractionTag(o, tag, 0, 1, false).data...)
		return testTag{tag, 5, 3, b}
	}
	return []testTag{asciiTag(1, "S"), dms(2, 37), asciiTag(3, "W"), dms(4, 14),
		{5, 1, 1, []byte{1}}, fractionTag(o, 6, 10, 1, false),
		fractionTag(o, 0xB, 1, 1, false), asciiTag(0xC, "K"), fractionTag(o, 0xD, 0, 1, false),
		asciiTag(0x10, "T"), fractionTag(o, 0x11, 0, 1, false),
		asciiTag(0x17, "M"), fractionTag(o, 0x18, 90, 1, false),
		fractionTag(o, 0x19, 1234, 1, false)}
}
func TestGPSReferencesAndZero(t *testing.T) {
	for _, o := range []binary.ByteOrder{binary.LittleEndian, binary.BigEndian} {
		r := parseTIFFExif(testTIFF(o, nil, gpsTags(o)))
		if r.Lat == nil || *r.Lat != -37 || r.Lon == nil || *r.Lon != -14 || r.Alt == nil || *r.Alt != -10 ||
			r.GPSImgDirection != 0 || r.GPSDestBearing != 90 || r.GPSSpeed != 0 ||
			r.GPSImgDirectionRef != "T" || r.GPSDestBearingRef != "M" || r.GPSSpeedRef != "K" {
			t.Fatalf("wrong GPS: %+v", r)
		}
		if !r.Present["gps_img_direction"] || !r.Present["gps_speed"] {
			t.Fatal("valid zero lost")
		}
		tags := gpsTags(o)
		tags[0] = asciiTag(1, "?")
		if r := parseTIFFExif(testTIFF(o, nil, tags)); r.Lat != nil {
			t.Fatal("accepted invalid coordinate reference")
		}
		tags = gpsTags(o)
		o.PutUint32(tags[1].data[4:], 0)
		if r := parseTIFFExif(testTIFF(o, nil, tags)); r.Lat != nil {
			t.Fatal("accepted zero denominator")
		}
	}
}
func TestMalformedTIFF(t *testing.T) {
	o := binary.LittleEndian
	tr := &tiffReader{data: make([]byte, 8), order: o}
	for _, e := range []ifdEntry{
		{typ: 5, count: 1, offset: 0xffffffff}, {typ: 10, count: 1, offset: 7},
		{typ: 5, count: 0}, {typ: 3, count: 0}, {typ: 99, count: 1},
	} {
		if _, ok := tr.fraction(e, e.typ == 10); ok {
			t.Fatal("invalid fraction accepted")
		}
		if _, ok := tr.unsigned(e); ok {
			t.Fatal("invalid integer accepted")
		}
	}
	valid := testTIFF(o, cameraTags(o), gpsTags(o))
	for n := 0; n < len(valid); n++ {
		parseTIFFExif(valid[:n])
	}
}
func TestExportPreservesZerosAndMissing(t *testing.T) {
	dir := t.TempDir()
	o := binary.LittleEndian
	tags := cameraTags(o)
	tags = append(tags, fractionTag(o, 0x9204, 0, 1, true), shortTag(o, 0x9207, 0), shortTag(o, 0xA405, 0))
	if err := os.WriteFile(filepath.Join(dir, "located.jpg"), testJPEG(testTIFF(o, tags, gpsTags(o))), 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "unlocated.jpg"), testJPEG(testTIFF(o, nil, nil)), 0600); err != nil {
		t.Fatal(err)
	}
	output := filepath.Join(dir, "photodb.geojson")
	buildGeoJSON(Config{TargetDir: dir, OutputFile: output})
	raw, err := os.ReadFile(output)
	if err != nil {
		t.Fatal(err)
	}
	var fc geoCollection
	if err = json.Unmarshal(raw, &fc); err != nil {
		t.Fatal(err)
	}
	if len(fc.Features) != 1 {
		t.Fatalf("got %d features", len(fc.Features))
	}
	if fc.DAPMVersion != dapmVersion || fc.DAPMSchemaVersion != dapmSchemaVersion {
		t.Fatalf("missing collection version markers: %+v", fc)
	}
	f := fc.Features[0]
	if f.Properties["dapm_version"] != dapmVersion || f.Properties["dapm_schema_version"] != dapmSchemaVersion {
		t.Fatalf("missing feature version markers: %v", f.Properties)
	}
	for _, key := range []string{"exposure_bias", "flash", "metering_mode", "white_balance", "focal_length_35mm", "gps_speed", "gps_img_direction"} {
		v, ok := f.Properties[key]
		if !ok || v != float64(0) {
			t.Fatalf("%s: %v present=%v", key, v, ok)
		}
	}
	if f.Geometry.Coordinates[2] != -10 {
		t.Fatal("lost altitude sign")
	}
	data, err := os.ReadFile(filepath.Join(dir, "photodb.csv"))
	if err != nil {
		t.Fatal(err)
	}
	rows, err := csv.NewReader(strings.NewReader(string(data))).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	for i, key := range rows[0] {
		if key == "white_balance" || key == "exposure_bias" {
			if rows[1][i] != "0" {
				t.Fatalf("%s zero lost in CSV", key)
			}
		}
	}
	if _, err := os.Stat(filepath.Join(dir, "no_gps_photos.csv")); err != nil {
		t.Fatal(err)
	}
	for _, ext := range []string{".shp", ".shx", ".dbf", ".prj", ".cpg", ".fields.json"} {
		if _, err := os.Stat(filepath.Join(dir, "photodb"+ext)); err != nil {
			t.Fatal(err)
		}
	}
	mappingRaw, err := os.ReadFile(filepath.Join(dir, "photodb.fields.json"))
	if err != nil {
		t.Fatal(err)
	}
	var mapping dbfFieldMappingFile
	if err := json.Unmarshal(mappingRaw, &mapping); err != nil {
		t.Fatal(err)
	}
	if mapping.DAPMVersion != dapmVersion || mapping.DAPMSchemaVersion != dapmSchemaVersion || len(mapping.Fields) != len(rows[0]) {
		t.Fatalf("invalid DBF field mapping: %+v", mapping)
	}
	dbfNames := map[string]bool{}
	foundSchemaVersion := false
	for _, field := range mapping.Fields {
		if field.DBFName == "" || len(field.DBFName) > 10 || dbfNames[field.DBFName] || field.DBFType != "C" {
			t.Fatalf("invalid DBF mapping field: %+v", field)
		}
		dbfNames[field.DBFName] = true
		foundSchemaVersion = foundSchemaVersion || field.SourceName == "dapm_schema_version"
	}
	if !foundSchemaVersion {
		t.Fatal("DBF mapping omitted dapm_schema_version")
	}
	meta := extractMetadata(filepath.Join(dir, "unlocated.jpg"))
	if _, ok := meta.Fields["white_balance"]; ok {
		t.Fatal("missing white balance exported")
	}
}

// Optional independent integration check: set DAPM_AUDIT_CSV to an existing
// photodb.csv and DAPM_EXIFTOOL to ExifTool's executable. Reads source photos only.
func TestOriginalPhotosAgainstExifTool(t *testing.T) {
	path := os.Getenv("DAPM_AUDIT_CSV")
	if path == "" {
		t.Skip("set DAPM_AUDIT_CSV to compare original photos")
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	rows, err := csv.NewReader(strings.NewReader(string(data))).ReadAll()
	if err != nil {
		t.Fatal(err)
	}
	cols := map[string]int{}
	for i, k := range rows[0] {
		cols[k] = i
	}
	chosen := map[string]bool{}
	samples := [][]string{}
	for _, row := range rows[1:] {
		key := row[cols["metering_mode"]] + "/" + row[cols["white_balance"]]
		if row[cols["exposure_bias"]] != "" {
			v, _ := strconv.ParseFloat(row[cols["exposure_bias"]], 64)
			if v > 1e6 {
				key += "/negative"
			}
		}
		if row[cols["iso"]] == "" {
			key += "/missingISO"
		}
		if !chosen[key] {
			chosen[key] = true
			samples = append(samples, row)
		}
	}
	exe := os.Getenv("DAPM_EXIFTOOL")
	if exe == "" {
		exe = "exiftool"
	}
	pairs := map[string]string{"iso": "ISO", "exposure_time": "ExposureTime", "exposure_bias": "ExposureCompensation",
		"focal_length_35mm": "FocalLengthIn35mmFormat", "color_space": "ColorSpace", "metering_mode": "MeteringMode",
		"white_balance": "WhiteBalance", "flash": "Flash", "f_number": "FNumber", "focal_length": "FocalLength"}
	for _, row := range samples {
		photo := row[cols["filepath"]]
		t.Run(row[cols["relative_filepath"]], func(t *testing.T) {
			args := []string{"-j", "-n"}
			for _, tag := range pairs {
				args = append(args, "-EXIF:"+tag)
			}
			args = append(args, photo)
			out, err := exec.Command(exe, args...).Output()
			if err != nil {
				t.Fatal(err)
			}
			var ref []map[string]interface{}
			if err = json.Unmarshal(out, &ref); err != nil {
				t.Fatal(err)
			}
			meta := extractMetadata(photo)
			for key, tag := range pairs {
				want, exists := ref[0][tag]
				got, present := meta.Fields[key]
				if exists != present {
					t.Errorf("%s presence: Go %v ExifTool %v", key, present, exists)
					continue
				}
				if !exists {
					continue
				}
				a, err := strconv.ParseFloat(stringValue(got), 64)
				if err != nil {
					t.Fatal(err)
				}
				b, ok := want.(float64)
				if !ok {
					t.Fatalf("unexpected ExifTool value %v", want)
				}
				if math.Abs(a-b) > 1e-8*math.Max(1, math.Abs(b)) {
					t.Errorf("%s: Go %v ExifTool %v", key, a, b)
				}
			}
		})
	}
	t.Logf("Compared %d original photographs with ExifTool", len(samples))
}
func stringValue(v interface{}) string {
	b, _ := json.Marshal(v)
	return string(b)
}

func TestOutputSchemaDocument(t *testing.T) {
	payload, err := os.ReadFile(filepath.Join("..", "..", "OUTPUT_SCHEMA.json"))
	if err != nil {
		t.Fatal(err)
	}
	var schema map[string]interface{}
	if err := json.Unmarshal(payload, &schema); err != nil {
		t.Fatalf("invalid OUTPUT_SCHEMA.json: %v", err)
	}
	if schema["$schema"] != "https://json-schema.org/draft/2020-12/schema" {
		t.Fatalf("unexpected JSON Schema dialect: %v", schema["$schema"])
	}
	definitions, ok := schema["$defs"].(map[string]interface{})
	if !ok || definitions["featureCollection"] == nil || definitions["dbfFieldMapping"] == nil {
		t.Fatal("schema must define both GeoJSON and DBF field-mapping documents")
	}
}

func TestWebmapFindsRelocatedTemplate(t *testing.T) {
	dir := t.TempDir()
	output := filepath.Join(dir, "photodb.geojson")
	payload, err := json.Marshal(geoCollection{
		Type:              "FeatureCollection",
		DAPMVersion:       dapmVersion,
		DAPMSchemaVersion: dapmSchemaVersion,
		Features: []geoFeature{{
			Type: "Feature",
			Geometry: geoGeometry{
				Type:        "Point",
				Coordinates: []float64{14.3, 37.1, 500},
			},
			Properties: map[string]interface{}{
				"filename":            "photo.jpg",
				"filepath":            "C:/photos/photo.jpg",
				"relative_filepath":   "photo.jpg",
				"dapm_version":        dapmVersion,
				"dapm_schema_version": dapmSchemaVersion,
			},
		}},
	})
	if err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(output, payload, 0600); err != nil {
		t.Fatal(err)
	}
	createWebmap(Config{OutputFile: output})
	if _, err := os.Stat(filepath.Join(dir, "index.html")); err != nil {
		t.Fatalf("relocated build/template.html was not used: %v", err)
	}
}

func TestXMPNamespaceContractIsOrderIndependent(t *testing.T) {
	xmpA := "<x:xmpmeta xmlns:x=\"adobe:ns:meta/\" xmlns:d=\"http://www.dji.com/drone-dji/1.0/\" xmlns:c=\"http://ns.adobe.com/camera-raw-settings/1.0/\" xmlns:m=\"http://ns.adobe.com/xap/1.0/\"><item d:Version=\"1.6\" c:Version=\"7.0\" d:GpsLatitude=\"37.1\" m:CreateDate=\"2026-01-01T12:00:00Z\" c:HasCrop=\"False\" d:SensorFPS=\"1500/100\"/></x:xmpmeta>"
	xmpB := "<x:xmpmeta xmlns:m=\"http://ns.adobe.com/xap/1.0/\" xmlns:c=\"http://ns.adobe.com/camera-raw-settings/1.0/\" xmlns:d=\"http://www.dji.com/drone-dji/1.0/\" xmlns:x=\"adobe:ns:meta/\"><item d:SensorFPS=\"1500/100\" c:HasCrop=\"False\" m:CreateDate=\"2026-01-01T12:00:00Z\" d:GpsLatitude=\"37.1\" c:Version=\"7.0\" d:Version=\"1.6\"/></x:xmpmeta>"
	parsedA := parseXMP(xmpA)
	parsedB := parseXMP(xmpB)
	if !reflect.DeepEqual(parsedA, parsedB) {
		t.Fatalf("attribute order changed XMP result:\nA=%+v\nB=%+v", parsedA, parsedB)
	}
	for key, value := range map[string]string{
		"xmp_drone_dji_Version":     "1.6",
		"xmp_crs_Version":           "7.0",
		"xmp_drone_dji_GpsLatitude": "37.1",
		"xmp_xmp_CreateDate":        "2026-01-01T12:00:00Z",
		"xmp_crs_HasCrop":           "False",
		"XMP_Gps_Lat":               "37.1",
		"XMP_CreateDate":            "2026-01-01T12:00:00Z",
		"HasCrop":                   "False",
		"SensorFPS":                 "1500/100",
	} {
		if parsedA.Fields[key] != value {
			t.Errorf("%s: got %q want %q", key, parsedA.Fields[key], value)
		}
	}
	if _, exists := parsedA.Fields["Version"]; exists {
		t.Fatal("ambiguous Version legacy alias was emitted")
	}
	if len(parsedA.Warnings) != 1 || !strings.Contains(parsedA.Warnings[0], "local-name collision \"version\"") {
		t.Fatalf("missing deterministic Version collision warning: %v", parsedA.Warnings)
	}

	unknownA := parseXMP("<x:xmpmeta xmlns:x=\"adobe:ns:meta/\" xmlns:u=\"https://example.com/xmp/custom/1.0/\"><item u:Custom-Field=\"value\"/></x:xmpmeta>")
	unknownB := parseXMP("<x:xmpmeta xmlns:x=\"adobe:ns:meta/\" xmlns:renamed=\"https://example.com/xmp/custom/1.0/\"><item renamed:Custom-Field=\"value\"/></x:xmpmeta>")
	if !reflect.DeepEqual(unknownA, unknownB) || unknownA.Fields["xmp_ns_63901216_Custom_Field"] != "value" {
		t.Fatalf("unknown namespace key is not stable: A=%+v B=%+v", unknownA, unknownB)
	}

	cameraNamespaces := parseXMP(`<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:a="http://pix4d.com/camera/1.0" xmlns:b="http://pix4d.com/camera/1.0/"><item a:LensPosition="1" b:LensPosition="2"/></x:xmpmeta>`)
	if cameraNamespaces.Fields["xmp_camera_LensPosition"] != "1" || cameraNamespaces.Fields["xmp_camera_v1_LensPosition"] != "2" {
		t.Fatalf("distinct camera namespace URIs were not preserved: %#v", cameraNamespaces.Fields)
	}
	if _, exists := cameraNamespaces.Fields["LensPosition"]; exists {
		t.Fatal("ambiguous camera legacy alias LensPosition must be omitted")
	}

	sanitizedCollision := parseXMP(`<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:u="https://example.com/xmp/custom/1.0/"><item u:Custom-Field="z" u:Custom_Field="a"/></x:xmpmeta>`)
	if sanitizedCollision.Fields["xmp_ns_63901216_Custom_Field"] != "a" {
		t.Fatalf("sanitized collision must resolve independently of document order: %#v", sanitizedCollision.Fields)
	}
	foundSanitizedWarning := false
	for _, warning := range sanitizedCollision.Warnings {
		if strings.Contains(warning, "distinct expanded attribute names") {
			foundSanitizedWarning = true
		}
	}
	if !foundSanitizedWarning {
		t.Fatalf("missing sanitized-key collision warning: %#v", sanitizedCollision.Warnings)
	}

	pathXMP := xmpA[:strings.Index(xmpA, "</x:xmpmeta>")] + "<item d:Nonfinite=\"NaN\"/></x:xmpmeta>"
	path := filepath.Join(t.TempDir(), "xmp.jpg")
	// No padding after the closing element: must not slice beyond the file.
	if err := os.WriteFile(path, append(testJPEG(testTIFF(binary.LittleEndian, nil, nil)), []byte(pathXMP)...), 0600); err != nil {
		t.Fatal(err)
	}
	m := extractMetadata(path)
	if m.Fields["xmp_drone_dji_Version"] != 1.6 || m.Fields["xmp_crs_Version"] != float64(7) ||
		m.Fields["XMP_Gps_Lat"] != 37.1 || m.Fields["dapm_schema_version"] != dapmSchemaVersion ||
		m.Fields["HasCrop"] != "False" || m.Fields["SensorFPS"] != "1500/100" ||
		m.Fields["xmp_drone_dji_Nonfinite"] != "NaN" || m.Fields["XMP_CreateDate"] != "2026-01-01T12:00:00Z" {
		t.Fatalf("unexpected XMP conversion: %v", m.Fields)
	}
	if _, err := json.Marshal(m.Fields); err != nil {
		t.Fatal(err)
	}
}

func TestMalformedJPEGSegments(t *testing.T) {
	for _, data := range [][]byte{
		{0xff, 0xd8, 0xff, 0xe1, 0, 2, 'E', 'x', 'i', 'f', 0, 0},
		{0xff, 0xd8, 0xff, 0xe1, 0xff, 0xff},
		{0xff, 0xd8, 0xff, 0xe1, 0, 0, 'E', 'x', 'i', 'f', 0, 0},
	} {
		if extractEXIF(data) != nil {
			t.Fatal("accepted malformed JPEG segment")
		}
	}
}
