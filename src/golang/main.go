package main

// dapm – stdlib only, zero external dependencies.
// Recursively scans JPEG/JPEG drone photos, extracting standard EXIF and
// available XMP metadata, including DJI flight and gimbal attributes.
// It writes GeoJSON Point features and an interactive Leaflet web map.
// Geometry uses WGS 84 [longitude, latitude, altitude]; EXIF and XMP coordinate
// properties use EXIF_ and XMP_ prefixes. Legacy mp, make, and camera fields
// are omitted from output; photos without valid EXIF coordinates go to no_gps_photos.csv.
//
// Usage:  ../../build/dapm.exe input.yaml
// Build:  build_it.bat

import (
	"bufio"
	"bytes"
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"encoding/xml"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
)

const (
	dapmVersion       = "1.1.2"
	dapmSchemaVersion = "2"
)

// Config holds the values from the YAML file.
type Config struct {
	TargetDir  string
	OutputFile string
	MapTitle   string
	Author     string
}

// parseYAML reads the four keys the script needs from a simple YAML file.
// It handles inline comments (# …), quoted and unquoted values.
func parseYAML(filename string) (Config, error) {
	var cfg Config

	f, err := os.Open(filename)
	if err != nil {
		return cfg, err
	}
	defer f.Close()

	sc := bufio.NewScanner(f)
	for sc.Scan() {
		line := sc.Text()

		// Strip inline comments: only strip " #" that appears outside quotes.
		// Strategy: if the value is quoted, the comment is after the closing quote.
		//           Otherwise strip at the first " #".
		if ci := strings.Index(line, " #"); ci != -1 {
			// Only strip if we're not inside a quoted section before that point.
			pre := line[:ci]
			if strings.Count(pre, `"`)%2 == 0 {
				line = pre
			}
		}
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}

		idx := strings.Index(line, ":")
		if idx == -1 {
			continue
		}
		key := strings.TrimSpace(line[:idx])
		val := strings.TrimSpace(line[idx+1:])
		val = strings.Trim(val, `"'`) // remove surrounding quotes

		switch key {
		case "TARGET_DIR":
			cfg.TargetDir = val
		case "OUTPUT_FILE":
			cfg.OutputFile = val
		case "MAP_TITLE":
			cfg.MapTitle = val
		case "AUTHOR":
			cfg.Author = val
		}
	}
	return cfg, sc.Err()
}

// ════════════════════════════════════════════════════════════════════════════
// Minimal JPEG / TIFF / EXIF parser
// ════════════════════════════════════════════════════════════════════════════

// tiffReader wraps raw TIFF bytes and provides typed accessors.
type tiffReader struct {
	data  []byte
	order binary.ByteOrder
}

func (r *tiffReader) u16(off int) uint16 {
	if off < 0 || off > len(r.data)-2 {
		return 0
	}
	return r.order.Uint16(r.data[off : off+2])
}

func (r *tiffReader) u32(off int) uint32 {
	if off < 0 || off > len(r.data)-4 {
		return 0
	}
	return r.order.Uint32(r.data[off : off+4])
}

// valueBytes resolves TIFF inline values and offsets using the declared type/count.
// Invalid types, empty values, and truncated payloads are never treated as zero.
func (r *tiffReader) valueBytes(e ifdEntry) ([]byte, bool) {
	var size uint64
	switch e.typ {
	case 1, 2, 7:
		size = 1
	case 3:
		size = 2
	case 4, 9:
		size = 4
	case 5, 10:
		size = 8
	default:
		return nil, false
	}
	n := size * uint64(e.count)
	if n == 0 {
		return nil, false
	}
	if n <= 4 {
		return e.rawVal[:int(n)], true
	}
	end := uint64(e.offset) + n
	if end > uint64(len(r.data)) {
		return nil, false
	}
	return r.data[int(e.offset):int(end)], true
}

func (r *tiffReader) unsigned(e ifdEntry) (uint32, bool) {
	if e.count != 1 {
		return 0, false
	}
	b, ok := r.valueBytes(e)
	if !ok {
		return 0, false
	}
	switch e.typ {
	case 1:
		return uint32(b[0]), true
	case 3:
		return uint32(r.order.Uint16(b)), true
	case 4:
		return r.order.Uint32(b), true
	}
	return 0, false
}

func (r *tiffReader) fraction(e ifdEntry, signed bool) (float64, bool) {
	expected := uint16(5)
	if signed {
		expected = 10
	}
	if e.typ != expected || e.count != 1 {
		return 0, false
	}
	b, ok := r.valueBytes(e)
	if !ok {
		return 0, false
	}
	num, den := float64(r.order.Uint32(b)), float64(r.order.Uint32(b[4:]))
	if signed {
		num, den = float64(int32(r.order.Uint32(b))), float64(int32(r.order.Uint32(b[4:])))
	}
	if den == 0 {
		return 0, false
	}
	return num / den, true
}

func (r *tiffReader) coordinate(e ifdEntry) ([3]float64, bool) {
	var dms [3]float64
	if e.typ != 5 || e.count != 3 {
		return dms, false
	}
	b, ok := r.valueBytes(e)
	if !ok {
		return dms, false
	}
	for i := range dms {
		den := r.order.Uint32(b[i*8+4:])
		if den == 0 {
			return dms, false
		}
		dms[i] = float64(r.order.Uint32(b[i*8:])) / float64(den)
	}
	return dms, dms[1] < 60 && dms[2] < 60
}

// ifdEntry represents one 12-byte entry in a TIFF IFD.
type ifdEntry struct {
	tag    uint16
	typ    uint16
	count  uint32
	rawVal [4]byte // raw bytes of the value/offset field
	offset uint32  // same bytes interpreted as uint32
}

// readIFD parses all entries of an IFD starting at the given byte offset.
func (r *tiffReader) readIFD(off int) []ifdEntry {
	if off < 0 || off > len(r.data)-2 {
		return nil
	}
	n := int(r.u16(off))
	off += 2
	entries := make([]ifdEntry, 0, n)
	for i := 0; i < n; i++ {
		if off+12 > len(r.data) {
			break
		}
		var e ifdEntry
		e.tag = r.u16(off)
		e.typ = r.u16(off + 2)
		e.count = r.u32(off + 4)
		copy(e.rawVal[:], r.data[off+8:off+12])
		e.offset = r.u32(off + 8)
		entries = append(entries, e)
		off += 12
	}
	return entries
}

// dmsToDecimal converts GPS degrees/minutes/seconds + hemisphere ref to decimal.
func dmsToDecimal(dms [3]float64, ref string) float64 {
	dec := dms[0] + dms[1]/60.0 + dms[2]/3600.0
	if ref == "S" || ref == "W" {
		dec = -dec
	}
	return dec
}

// exifResult holds the EXIF values we care about.
type exifResult struct {
	Present            map[string]bool
	GPSSpeedRef        string
	GPSImgDirectionRef string
	GPSDestBearingRef  string
	DateTime           string
	DateTimeDigitized  string
	Camera             string
	Make               string
	Software           string
	LensModel          string
	Artist             string
	Copyright          string
	ImageDescription   string
	Orientation        int
	ExposureTime       float64
	FNumber            float64
	ISO                int
	ExposureBias       float64
	Flash              int
	FocalLength        float64
	FocalLength35mm    float64
	MeteringMode       int
	WhiteBalance       int
	ColorSpace         int
	GPSDOP             float64
	GPSSpeed           float64
	GPSImgDirection    float64
	GPSDestBearing     float64
	Lat                *float64
	Lon                *float64
	Alt                *float64
	Width              int
	Height             int
}

func computeMegapixels(width, height int) float64 {
	if width <= 0 || height <= 0 {
		return 0
	}
	return float64(width*height) / 1_000_000.0
}

// extractEXIF scans JPEG segments for an APP1/Exif block and parses it.
func extractEXIF(data []byte) *exifResult {
	// Must start with JPEG SOI marker.
	if len(data) < 4 || data[0] != 0xFF || data[1] != 0xD8 {
		return nil
	}

	i := 2
	for i+4 < len(data) {
		if data[i] != 0xFF {
			break
		}
		marker := data[i+1]
		if marker == 0xDA || marker == 0xD9 {
			break
		}
		segLen := int(binary.BigEndian.Uint16(data[i+2 : i+4]))
		end := i + 2 + segLen
		if segLen < 2 || end > len(data) {
			break
		}

		if marker == 0xE1 && i+10 <= end {
			// APP1: check for "Exif\x00\x00" header
			if bytes.Equal(data[i+4:i+10], []byte("Exif\x00\x00")) {
				return parseTIFFExif(data[i+10 : end])
			}
		}
		i = end
	}
	return nil
}

// parseTIFFExif decodes a TIFF block embedded inside an APP1 segment.
func parseTIFFExif(data []byte) *exifResult {
	if len(data) < 8 {
		return nil
	}

	tr := &tiffReader{data: data}
	switch {
	case data[0] == 'I' && data[1] == 'I':
		tr.order = binary.LittleEndian
	case data[0] == 'M' && data[1] == 'M':
		tr.order = binary.BigEndian
	default:
		return nil
	}
	if tr.u16(2) != 0x002A { // TIFF magic number
		return nil
	}

	ifd0Off := int(tr.u32(4))
	result := &exifResult{Present: make(map[string]bool)}
	var exifOff, gpsOff uint32
	var hasExif, hasGPS bool
	textValue := func(e ifdEntry) string {
		if e.typ != 2 {
			return ""
		}
		b, ok := tr.valueBytes(e)
		if !ok {
			return ""
		}
		return strings.TrimSpace(strings.TrimRight(string(b), "\x00"))
	}
	setInt := func(e ifdEntry, dst *int, key string) {
		if e.typ != 3 {
			return
		}
		if v, ok := tr.unsigned(e); ok {
			*dst = int(v)
			result.Present[key] = true
		}
	}
	setFraction := func(e ifdEntry, dst *float64, key string, signed bool) {
		if v, ok := tr.fraction(e, signed); ok {
			*dst = v
			result.Present[key] = true
		}
	}
	setDimension := func(e ifdEntry, dst *int) {
		if e.typ != 3 && e.typ != 4 {
			return
		}
		if v, ok := tr.unsigned(e); ok {
			*dst = int(v)
		}
	}
	for _, e := range tr.readIFD(ifd0Off) {
		switch e.tag {
		case 0x0100:
			setDimension(e, &result.Width)
		case 0x0101:
			setDimension(e, &result.Height)
		case 0x010E:
			result.ImageDescription = textValue(e)
		case 0x010F:
			result.Make = textValue(e)
		case 0x0110:
			result.Camera = textValue(e)
		case 0x0112:
			setInt(e, &result.Orientation, "orientation")
		case 0x0131:
			result.Software = textValue(e)
		case 0x0132:
			result.DateTime = textValue(e)
		case 0x013B:
			result.Artist = textValue(e)
		case 0x8298:
			result.Copyright = textValue(e)
		case 0x8769:
			if e.typ == 4 {
				exifOff, hasExif = tr.unsigned(e)
			}
		case 0x8825:
			if e.typ == 4 {
				gpsOff, hasGPS = tr.unsigned(e)
			}
		}
	}
	var shutterSpeed float64
	var hasShutterSpeed bool
	if hasExif {
		for _, e := range tr.readIFD(int(exifOff)) {
			switch e.tag {
			case 0x9003:
				if v := textValue(e); v != "" {
					result.DateTime = v
				}
			case 0x9004:
				result.DateTimeDigitized = textValue(e)
			case 0x829A:
				setFraction(e, &result.ExposureTime, "exposure_time", false)
			case 0x9201:
				shutterSpeed, hasShutterSpeed = tr.fraction(e, true)
			case 0x829D:
				setFraction(e, &result.FNumber, "f_number", false)
			case 0x8827:
				setInt(e, &result.ISO, "iso")
			case 0x9204:
				setFraction(e, &result.ExposureBias, "exposure_bias", true)
			case 0x9209:
				setInt(e, &result.Flash, "flash")
			case 0x920A:
				setFraction(e, &result.FocalLength, "focal_length", false)
			case 0xA434:
				result.LensModel = textValue(e)
			case 0xA001:
				setInt(e, &result.ColorSpace, "color_space")
			case 0xA002:
				setDimension(e, &result.Width)
			case 0xA003:
				setDimension(e, &result.Height)
			case 0x9207:
				setInt(e, &result.MeteringMode, "metering_mode")
			case 0xA405:
				if e.typ == 3 {
					if v, ok := tr.unsigned(e); ok {
						result.FocalLength35mm = float64(v)
						result.Present["focal_length_35mm"] = true
					}
				}
			case 0xA403:
				setInt(e, &result.WhiteBalance, "white_balance")
			}
		}
	}
	// ExposureTime is authoritative regardless of directory order.
	if (!result.Present["exposure_time"] || result.ExposureTime <= 0) && hasShutterSpeed {
		if seconds := math.Exp2(-shutterSpeed); seconds > 0 && !math.IsInf(seconds, 0) {
			result.ExposureTime = seconds
			result.Present["exposure_time"] = true
		}
	}

	if hasGPS {
		var latRef, lonRef string
		var latDMS, lonDMS [3]float64
		var hasLat, hasLon bool
		var altitude float64
		var hasAltitude bool
		var altitudeRef uint32
		// EXIF defines zero (above sea level) as the default altitude reference.
		altitudeRefOK := true
		for _, e := range tr.readIFD(int(gpsOff)) {
			switch e.tag {
			case 0x0001:
				latRef = textValue(e)
			case 0x0002:
				latDMS, hasLat = tr.coordinate(e)
			case 0x0003:
				lonRef = textValue(e)
			case 0x0004:
				lonDMS, hasLon = tr.coordinate(e)
			case 0x0005:
				altitudeRefOK = false
				if e.typ == 1 {
					altitudeRef, altitudeRefOK = tr.unsigned(e)
					altitudeRefOK = altitudeRefOK && altitudeRef <= 1
				}
			case 0x0006:
				altitude, hasAltitude = tr.fraction(e, false)
			case 0x000B:
				setFraction(e, &result.GPSDOP, "gps_dop", false)
			case 0x000C:
				result.GPSSpeedRef = textValue(e)
			case 0x000D:
				setFraction(e, &result.GPSSpeed, "gps_speed", false)
			case 0x0010:
				result.GPSImgDirectionRef = textValue(e)
			case 0x0011:
				setFraction(e, &result.GPSImgDirection, "gps_img_direction", false)
			case 0x0017:
				result.GPSDestBearingRef = textValue(e)
			case 0x0018:
				setFraction(e, &result.GPSDestBearing, "gps_dest_bearing", false)
			}
		}
		if hasAltitude && altitudeRefOK {
			if altitudeRef == 1 {
				altitude = -altitude
			}
			result.Alt = &altitude
		}
		if hasLat && hasLon && (latRef == "N" || latRef == "S") && (lonRef == "E" || lonRef == "W") {
			lat := dmsToDecimal(latDMS, latRef)
			lon := dmsToDecimal(lonDMS, lonRef)
			if math.Abs(lat) <= 90 && math.Abs(lon) <= 180 {
				result.Lat, result.Lon = &lat, &lon
			}
		}
	}

	return result
}

// ════════════════════════════════════════════════════════════════════════════
// XMP parser
// ════════════════════════════════════════════════════════════════════════════

const (
	xmpNSXMP      = "http://ns.adobe.com/xap/1.0/"
	xmpNSTIFF     = "http://ns.adobe.com/tiff/1.0/"
	xmpNSEXIF     = "http://ns.adobe.com/exif/1.0/"
	xmpNSXMPMM    = "http://ns.adobe.com/xap/1.0/mm/"
	xmpNSDC       = "http://purl.org/dc/elements/1.1/"
	xmpNSCRS      = "http://ns.adobe.com/camera-raw-settings/1.0/"
	xmpNSDJI      = "http://www.dji.com/drone-dji/1.0/"
	xmpNSGPano    = "http://ns.google.com/photos/1.0/panorama/"
	xmpNSCamera   = "http://pix4d.com/camera/1.0"
	xmpNSCameraV1 = "http://pix4d.com/camera/1.0/"
)

var xmpNamespaceNames = map[string]string{
	xmpNSXMP:      "xmp",
	xmpNSTIFF:     "tiff",
	xmpNSEXIF:     "exif",
	xmpNSXMPMM:    "xmp_mm",
	xmpNSDC:       "dc",
	xmpNSCRS:      "crs",
	xmpNSDJI:      "drone_dji",
	xmpNSGPano:    "gpano",
	xmpNSCamera:   "camera",
	xmpNSCameraV1: "camera_v1",
}

type xmpAttribute struct {
	namespace string
	local     string
	value     string
	canonical string
}

type xmpParseResult struct {
	Fields   map[string]string
	Warnings []string
}

func sanitizeXMPKeyPart(s string) string {
	var b strings.Builder
	lastUnderscore := false
	for _, r := range s {
		valid := r == '_' || r >= 'A' && r <= 'Z' || r >= 'a' && r <= 'z' || r >= '0' && r <= '9'
		if valid {
			b.WriteRune(r)
			lastUnderscore = r == '_'
		} else if !lastUnderscore {
			b.WriteByte('_')
			lastUnderscore = true
		}
	}
	part := strings.Trim(b.String(), "_")
	if part == "" {
		return "field"
	}
	return part
}

func xmpNamespaceName(uri string) string {
	if uri == "" {
		return "unqualified"
	}
	if name, ok := xmpNamespaceNames[uri]; ok {
		return name
	}
	sum := sha256.Sum256([]byte(uri))
	return fmt.Sprintf("ns_%x", sum[:4])
}

func xmpCanonicalKey(namespace, local string) string {
	return "xmp_" + xmpNamespaceName(namespace) + "_" + sanitizeXMPKeyPart(local)
}

// legacyXMPAlias defines the temporary compatibility surface for schema 2.
// Version is deliberately excluded because it is common to multiple namespaces.
func legacyXMPAlias(namespace, local string) (string, bool) {
	if strings.EqualFold(local, "Version") {
		return "", false
	}
	switch namespace {
	case xmpNSXMP:
		switch strings.ToLower(local) {
		case "createdate":
			return "XMP_CreateDate", true
		case "modifydate":
			return "ModifyDate", true
		}
	case xmpNSTIFF:
		if local == "Make" || local == "Model" {
			return local, true
		}
	case xmpNSDC:
		if strings.EqualFold(local, "format") {
			return "format", true
		}
	case xmpNSDJI:
		switch strings.ToLower(local) {
		case "gpslatitude":
			return "XMP_Gps_Lat", true
		case "gpslongitude":
			return "XMP_Gps_Lon", true
		default:
			return local, true
		}
	case xmpNSCRS, xmpNSCamera, xmpNSCameraV1, xmpNSGPano, xmpNSEXIF, xmpNSXMPMM:
		return local, true
	}
	return "", false
}

// parseXMP emits a canonical key for every attribute using its namespace URI.
// Selected unambiguous legacy aliases are retained for one transition schema.
func parseXMP(xmpData string) xmpParseResult {
	attributes := []xmpAttribute{}
	decoder := xml.NewDecoder(strings.NewReader(xmpData))
	for {
		token, err := decoder.Token()
		if err != nil {
			break
		}
		start, ok := token.(xml.StartElement)
		if !ok {
			continue
		}
		for _, attr := range start.Attr {
			if attr.Name.Space == "xmlns" || attr.Name.Local == "xmlns" {
				continue
			}
			if attr.Name.Local == "about" || attr.Name.Local == "xmptk" {
				continue
			}
			attributes = append(attributes, xmpAttribute{
				namespace: attr.Name.Space,
				local:     attr.Name.Local,
				value:     attr.Value,
				canonical: xmpCanonicalKey(attr.Name.Space, attr.Name.Local),
			})
		}
	}

	result := xmpParseResult{Fields: make(map[string]string)}
	warnings := map[string]bool{}
	localNamespaces := map[string]map[string]bool{}
	aliasSources := map[string]map[string]bool{}
	canonicalSources := map[string]map[string]bool{}

	for _, attr := range attributes {
		if canonicalSources[attr.canonical] == nil {
			canonicalSources[attr.canonical] = map[string]bool{}
		}
		canonicalSources[attr.canonical][attr.namespace+"\x00"+attr.local] = true

		if previous, exists := result.Fields[attr.canonical]; exists && previous != attr.value {
			values := []string{previous, attr.value}
			sort.Strings(values)
			result.Fields[attr.canonical] = values[0]
			warnings[fmt.Sprintf("canonical XMP key %q occurs with different values; keeping the lexicographically first value", attr.canonical)] = true
		} else if !exists {
			result.Fields[attr.canonical] = attr.value
		}

		localKey := strings.ToLower(attr.local)
		if localNamespaces[localKey] == nil {
			localNamespaces[localKey] = map[string]bool{}
		}
		localNamespaces[localKey][xmpNamespaceName(attr.namespace)] = true

		if alias, ok := legacyXMPAlias(attr.namespace, attr.local); ok {
			if aliasSources[alias] == nil {
				aliasSources[alias] = map[string]bool{}
			}
			aliasSources[alias][attr.canonical] = true
		}
	}

	for canonical, sources := range canonicalSources {
		if len(sources) > 1 {
			warnings[fmt.Sprintf("canonical XMP key %q would merge %d distinct expanded attribute names after key sanitization", canonical, len(sources))] = true
		}
	}

	for local, namespaces := range localNamespaces {
		if len(namespaces) < 2 {
			continue
		}
		names := make([]string, 0, len(namespaces))
		for name := range namespaces {
			names = append(names, name)
		}
		sort.Strings(names)
		warnings[fmt.Sprintf("XMP local-name collision %q across namespaces %s; canonical fields were preserved and an ambiguous legacy alias was omitted", local, strings.Join(names, ", "))] = true
	}

	aliases := make([]string, 0, len(aliasSources))
	for alias := range aliasSources {
		aliases = append(aliases, alias)
	}
	sort.Strings(aliases)
	for _, alias := range aliases {
		sources := aliasSources[alias]
		if len(sources) != 1 {
			warnings[fmt.Sprintf("legacy XMP alias %q is ambiguous and was omitted", alias)] = true
			continue
		}
		if _, collides := result.Fields[alias]; collides {
			warnings[fmt.Sprintf("legacy XMP alias %q collides with a canonical field and was omitted", alias)] = true
			continue
		}
		for canonical := range sources {
			result.Fields[alias] = result.Fields[canonical]
		}
	}

	for warning := range warnings {
		result.Warnings = append(result.Warnings, warning)
	}
	sort.Strings(result.Warnings)
	return result
}

// ════════════════════════════════════════════════════════════════════════════
// Metadata extraction
// ════════════════════════════════════════════════════════════════════════════

// Metadata holds all extracted values for one photo.
type Metadata struct {
	Lat    *float64
	Lon    *float64
	Alt    *float64
	Fields map[string]interface{}
}

var excludedOutputFields = map[string]bool{
	"mp":     true,
	"make":   true,
	"camera": true,
}

func newMetadata() Metadata {
	return Metadata{Fields: map[string]interface{}{
		"dapm_version":        dapmVersion,
		"dapm_schema_version": dapmSchemaVersion,
	}}
}

var reportedXMPWarnings = map[string]bool{}

func reportXMPWarning(filePath, warning string) {
	if reportedXMPWarnings[warning] {
		return
	}
	reportedXMPWarnings[warning] = true
	fmt.Printf("  ⚠ XMP warning in %s: %s (further identical warnings suppressed)\n", filepath.Base(filePath), warning)
}

// extractMetadata reads EXIF + XMP from a JPEG file.
func extractMetadata(filePath string) Metadata {
	meta := newMetadata()

	raw, err := os.ReadFile(filePath)
	if err != nil {
		fmt.Printf("  Error reading %s: %v\n", filePath, err)
		return meta
	}

	// ── EXIF ─────────────────────────────────────────────────────────────────
	if exif := extractEXIF(raw); exif != nil {
		if exif.DateTime != "" {
			meta.Fields["datetime"] = exif.DateTime
		}
		if exif.DateTimeDigitized != "" {
			meta.Fields["date_time_digitized"] = exif.DateTimeDigitized
		}
		if exif.Camera != "" {
			meta.Fields["camera"] = exif.Camera
		}
		if exif.Make != "" {
			meta.Fields["make"] = exif.Make
		}
		if exif.Software != "" {
			meta.Fields["software"] = exif.Software
		}
		if exif.LensModel != "" {
			meta.Fields["lens_model"] = exif.LensModel
		}
		if exif.Artist != "" {
			meta.Fields["artist"] = exif.Artist
		}
		if exif.Copyright != "" {
			meta.Fields["copyright"] = exif.Copyright
		}
		if exif.ImageDescription != "" {
			meta.Fields["image_description"] = exif.ImageDescription
		}
		if exif.Orientation > 0 {
			meta.Fields["orientation"] = exif.Orientation
		}
		if exif.ExposureTime > 0 {
			meta.Fields["exposure_time"] = exif.ExposureTime
		}
		if exif.FNumber > 0 {
			meta.Fields["f_number"] = exif.FNumber
		}
		if exif.ISO > 0 {
			meta.Fields["iso"] = exif.ISO
		}
		if exif.Present["exposure_bias"] {
			meta.Fields["exposure_bias"] = exif.ExposureBias
		}
		if exif.Present["flash"] {
			meta.Fields["flash"] = exif.Flash
		}
		if exif.FocalLength > 0 {
			meta.Fields["focal_length"] = exif.FocalLength
		}
		if exif.Present["focal_length_35mm"] {
			meta.Fields["focal_length_35mm"] = exif.FocalLength35mm
		}
		if exif.Present["metering_mode"] {
			meta.Fields["metering_mode"] = exif.MeteringMode
		}
		if exif.Present["white_balance"] {
			meta.Fields["white_balance"] = exif.WhiteBalance
		}
		if exif.Present["color_space"] {
			meta.Fields["color_space"] = exif.ColorSpace
		}
		if exif.Present["gps_dop"] {
			meta.Fields["gps_dop"] = exif.GPSDOP
		}
		if exif.Present["gps_speed"] {
			meta.Fields["gps_speed"] = exif.GPSSpeed
		}
		if exif.Present["gps_img_direction"] {
			meta.Fields["gps_img_direction"] = exif.GPSImgDirection
		}
		if exif.Present["gps_dest_bearing"] {
			meta.Fields["gps_dest_bearing"] = exif.GPSDestBearing
		}
		for key, value := range map[string]string{
			"gps_speed_ref":         exif.GPSSpeedRef,
			"gps_img_direction_ref": exif.GPSImgDirectionRef,
			"gps_dest_bearing_ref":  exif.GPSDestBearingRef,
		} {
			if value != "" {
				meta.Fields[key] = value
			}
		}
		if exif.Width > 0 && exif.Height > 0 {
			meta.Fields["width"] = exif.Width
			meta.Fields["height"] = exif.Height
			meta.Fields["megapixels"] = computeMegapixels(exif.Width, exif.Height)
			meta.Fields["mp"] = computeMegapixels(exif.Width, exif.Height)
		}
		meta.Lat = exif.Lat
		meta.Lon = exif.Lon
		meta.Alt = exif.Alt
		if exif.Lat != nil {
			meta.Fields["EXIF_Lat"] = *exif.Lat
		}
		if exif.Lon != nil {
			meta.Fields["EXIF_Lon"] = *exif.Lon
		}
	} else {
		fmt.Printf("  ⚠ No EXIF data found in %s\n", filepath.Base(filePath))
	}

	// ── XMP ──────────────────────────────────────────────────────────────────
	xmpStart := bytes.Index(raw, []byte("<x:xmpmeta"))
	if xmpStart != -1 {
		remaining := raw[xmpStart:]
		xmpEnd := bytes.Index(remaining, []byte("</x:xmpmeta>"))
		if xmpEnd != -1 {
			xmpBlock := string(remaining[:xmpEnd+len("</x:xmpmeta>")])
			parsed := parseXMP(xmpBlock)
			for _, warning := range parsed.Warnings {
				reportXMPWarning(filePath, warning)
			}
			for k, v := range parsed.Fields {
				if existing, exists := meta.Fields[k]; exists {
					if fmt.Sprint(existing) != v {
						reportXMPWarning(filePath, fmt.Sprintf("XMP field %q conflicts with an existing output field and was omitted", k))
					}
					continue
				}
				if fv, err := strconv.ParseFloat(v, 64); err == nil && !math.IsNaN(fv) && !math.IsInf(fv, 0) {
					meta.Fields[k] = fv
				} else {
					meta.Fields[k] = v
				}
			}
		}
	}

	return meta
}

// ════════════════════════════════════════════════════════════════════════════
// GeoJSON structures & builder
// ════════════════════════════════════════════════════════════════════════════

type geoGeometry struct {
	Type        string    `json:"type"`
	Coordinates []float64 `json:"coordinates"`
}

type geoFeature struct {
	Type       string                 `json:"type"`
	Geometry   geoGeometry            `json:"geometry"`
	Properties map[string]interface{} `json:"properties"`
}

type geoCollection struct {
	Type              string       `json:"type"`
	DAPMVersion       string       `json:"dapm_version"`
	DAPMSchemaVersion string       `json:"dapm_schema_version"`
	Features          []geoFeature `json:"features"`
}

func buildGeoJSON(cfg Config) {
	var features []geoFeature
	// Each entry is a flat map of all recoverable fields for no-GPS files.
	var noGPSRows []map[string]interface{}

	outputDir := filepath.Dir(cfg.OutputFile)

	err := filepath.WalkDir(cfg.TargetDir, func(path string, d os.DirEntry, walkErr error) error {
		if walkErr != nil {
			fmt.Printf("Walk error at %s: %v\n", path, walkErr)
			return nil // continue despite errors
		}
		if d.IsDir() {
			return nil
		}
		lower := strings.ToLower(d.Name())
		if !strings.HasSuffix(lower, ".jpg") && !strings.HasSuffix(lower, ".jpeg") {
			return nil
		}

		fmt.Printf("Analyzing: %s\n", path)
		meta := extractMetadata(path)

		relPath, _ := filepath.Rel(outputDir, path)
		relPathSlash := filepath.ToSlash(relPath)

		if meta.Lat == nil || meta.Lon == nil {
			fmt.Printf("  ⚠ No GPS data – will be saved to no-gps CSV.\n")
			row := map[string]interface{}{
				"filename":          d.Name(),
				"filepath":          path,
				"relative_filepath": relPathSlash,
			}
			for k, v := range meta.Fields {
				if !excludedOutputFields[k] {
					row[k] = v
				}
			}
			noGPSRows = append(noGPSRows, row)
			return nil
		}

		alt := 0.0
		if meta.Alt != nil {
			alt = *meta.Alt
		}

		props := map[string]interface{}{
			"filename":          d.Name(),
			"filepath":          path,
			"relative_filepath": relPathSlash,
		}
		for k, v := range meta.Fields {
			if !excludedOutputFields[k] {
				props[k] = v
			}
		}

		features = append(features, geoFeature{
			Type: "Feature",
			Geometry: geoGeometry{
				Type:        "Point",
				Coordinates: []float64{*meta.Lon, *meta.Lat, alt},
			},
			Properties: props,
		})
		return nil
	})

	if err != nil {
		fmt.Printf("Fatal walk error: %v\n", err)
		return
	}

	if features == nil {
		features = []geoFeature{}
	}

	out, err := json.MarshalIndent(geoCollection{
		Type:              "FeatureCollection",
		DAPMVersion:       dapmVersion,
		DAPMSchemaVersion: dapmSchemaVersion,
		Features:          features,
	}, "", "    ")
	if err != nil {
		fmt.Printf("JSON error: %v\n", err)
		return
	}
	if err := os.WriteFile(cfg.OutputFile, out, 0644); err != nil {
		fmt.Printf("Error writing output: %v\n", err)
		return
	}
	exportValidData(features, cfg.OutputFile)
	fmt.Printf("\n✅ GeoJSON created! Found %d valid photos. Saved to %s\n",
		len(features), cfg.OutputFile)

	// Write no-GPS CSV if there are any such files.
	if len(noGPSRows) > 0 {
		writeNoGPSCSV(noGPSRows, outputDir)
	}
}

// writeNoGPSCSV writes all files that had no GPS coordinates to a CSV file.
// Columns are collected dynamically across all rows so no metadata is lost.
func writeNoGPSCSV(rows []map[string]interface{}, outputDir string) {
	// ── 1. Collect the union of all column names, preserving a stable order ──
	seen := make(map[string]bool)
	// Always put the identifying fields first.
	fixed := []string{"filename", "filepath", "relative_filepath"}
	for _, k := range fixed {
		seen[k] = true
	}
	var extra []string
	for _, row := range rows {
		for k := range row {
			if !seen[k] {
				seen[k] = true
				extra = append(extra, k)
			}
		}
	}
	// Sort extra columns for deterministic output.
	sortStrings(extra)
	headers := append(fixed, extra...)

	// ── 2. Build and write the CSV ────────────────────────────────────────────
	csvPath := filepath.Join(outputDir, "no_gps_photos.csv")
	f, err := os.Create(csvPath)
	if err != nil {
		fmt.Printf("writeNoGPSCSV: cannot create file: %v\n", err)
		return
	}
	defer f.Close()

	w := bufio.NewWriter(f)
	writeCSVRow(w, headers)
	for _, row := range rows {
		cells := make([]string, len(headers))
		for i, h := range headers {
			cells[i] = csvCell(row[h])
		}
		writeCSVRow(w, cells)
	}
	w.Flush()

	fmt.Printf("📋 No-GPS photos: %d file(s) saved to %s\n", len(rows), csvPath)
}

// csvCell converts any value to its CSV cell string representation.
func csvCell(v interface{}) string {
	if v == nil {
		return ""
	}
	s := fmt.Sprintf("%v", v)
	// Quote the cell if it contains a comma, quote, or newline.
	if strings.ContainsAny(s, `,"`+"\n") {
		s = `"` + strings.ReplaceAll(s, `"`, `""`) + `"`
	}
	return s
}

// writeCSVRow writes one CSV row to the writer.
func writeCSVRow(w *bufio.Writer, cells []string) {
	w.WriteString(strings.Join(cells, ",") + "\n")
}

// sortStrings sorts a string slice in place (avoids importing "sort" for just this).
func sortStrings(s []string) {
	for i := 1; i < len(s); i++ {
		for j := i; j > 0 && s[j] < s[j-1]; j-- {
			s[j], s[j-1] = s[j-1], s[j]
		}
	}
}

// ════════════════════════════════════════════════════════════════════════════
// Webmap generator
// ════════════════════════════════════════════════════════════════════════════

// createWebmap reads template.html from the build directory,
// fills in the placeholders, and writes index.html next to the GeoJSON file.
// The placeholders used in template.html are Python str.format() style, i.e.
// {title}, {center_lat}, {center_lon}, {geojsonFile}, {geojsonData}, {author}.
func createWebmap(cfg Config) {
	// ── 1. Read the GeoJSON we just wrote ────────────────────────────────────
	raw, err := os.ReadFile(cfg.OutputFile)
	if err != nil {
		fmt.Printf("createWebmap: cannot read GeoJSON: %v\n", err)
		return
	}

	// ── 2. Decode just enough to compute the map centre ──────────────────────
	var fc geoCollection
	if err := json.Unmarshal(raw, &fc); err != nil {
		fmt.Printf("createWebmap: cannot parse GeoJSON: %v\n", err)
		return
	}

	var latSum, lonSum float64
	var count int
	for _, f := range fc.Features {
		if len(f.Geometry.Coordinates) >= 2 {
			lonSum += f.Geometry.Coordinates[0]
			latSum += f.Geometry.Coordinates[1]
			count++
		}
	}
	if count == 0 {
		fmt.Println("createWebmap: no valid coordinates, skipping webmap.")
		return
	}
	centerLat := latSum / float64(count)
	centerLon := lonSum / float64(count)

	// ── 3. Locate template.html ───────────────────────────────────────────────
	exe, err := os.Executable()
	if err != nil {
		exe = "."
	}
	templateCandidates := []string{
		filepath.Join(filepath.Dir(exe), "template.html"),
		"template.html",
		filepath.Join("build", "template.html"),
		filepath.Join("..", "..", "build", "template.html"),
	}
	var tmplBytes []byte
	for _, candidate := range templateCandidates {
		if data, readErr := os.ReadFile(candidate); readErr == nil {
			tmplBytes = data
			break
		}
	}
	if tmplBytes == nil {
		fmt.Printf("createWebmap: cannot find template.html; checked %s\n", strings.Join(templateCandidates, ", "))
		return
	}

	// ── 4. Fill placeholders ─────────────────────────────────────────────────
	title := cfg.MapTitle
	if title == "" {
		title = "Drone Aerial Photo Map"
	}
	author := cfg.Author
	if author == "" {
		author = "unknown"
	}

	geojsonFile := filepath.Base(cfg.OutputFile)
	geojsonData := string(raw)

	html := string(tmplBytes)
	html = strings.ReplaceAll(html, "{title}", title)
	html = strings.ReplaceAll(html, "{center_lat}", strconv.FormatFloat(centerLat, 'f', 6, 64))
	html = strings.ReplaceAll(html, "{center_lon}", strconv.FormatFloat(centerLon, 'f', 6, 64))
	html = strings.ReplaceAll(html, "{geojsonFile}", geojsonFile)
	html = strings.ReplaceAll(html, "{geojsonData}", geojsonData)
	html = strings.ReplaceAll(html, "{author}", author)

	// The template uses Python str.format() escaping: {{ and }} represent
	// literal braces in JS/CSS. Now that all {placeholder} substitutions are
	// done, unescape them so the browser receives valid JavaScript.
	html = strings.ReplaceAll(html, "{{", "{")
	html = strings.ReplaceAll(html, "}}", "}")

	// ── 5. Write index.html next to the GeoJSON ──────────────────────────────
	outputDir := filepath.Dir(cfg.OutputFile)
	outPath := filepath.Join(outputDir, "index.html")
	if err := os.WriteFile(outPath, []byte(html), 0644); err != nil {
		fmt.Printf("createWebmap: cannot write index.html: %v\n", err)
		return
	}

	fmt.Printf("✅ Webmap created: %s\n", outPath)
	fmt.Printf("   Open in browser: file://%s\n", filepath.ToSlash(outPath))
}

// ════════════════════════════════════════════════════════════════════════════
// Entry point
// ════════════════════════════════════════════════════════════════════════════

func main() {
	if len(os.Args) < 2 {
		fmt.Println("Usage:  dapm <input.yaml>")
		fmt.Println()
		fmt.Println("Required keys in input.yaml:")
		fmt.Println("  TARGET_DIR   – folder with drone photos (scanned recursively)")
		fmt.Println("  OUTPUT_FILE  – destination .geojson file")
		fmt.Println("Optional:")
		fmt.Println("  MAP_TITLE    – title for the web map")
		fmt.Println("  AUTHOR       – author name / handle")
		os.Exit(1)
	}

	cfg, err := parseYAML(os.Args[1])
	if err != nil {
		fmt.Printf("Cannot read config '%s': %v\n", os.Args[1], err)
		os.Exit(1)
	}
	if cfg.TargetDir == "" || cfg.OutputFile == "" {
		fmt.Println("Config error: TARGET_DIR and OUTPUT_FILE are required.")
		os.Exit(1)
	}

	if err := os.MkdirAll(filepath.Dir(cfg.OutputFile), 0755); err != nil {
		fmt.Printf("Cannot create output directory: %v\n", err)
		os.Exit(1)
	}

	fmt.Printf("📁 Scanning: %s\n", cfg.TargetDir)
	fmt.Printf("📄 Output:   %s\n\n", cfg.OutputFile)
	buildGeoJSON(cfg)
	createWebmap(cfg)
}
