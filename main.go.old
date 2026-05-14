package main

// dapm – stdlib only, zero external dependencies.
// Reads EXIF (GPS, camera model, datetime) and XMP (DJI gimbal / drone data)
// from JPEG drone photos and writes a GeoJSON FeatureCollection.
//
// Usage:  dapm input.yaml
// Build:  go build -o dapm.exe .

import (
	"bufio"
	"bytes"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
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
	if off+2 > len(r.data) {
		return 0
	}
	return r.order.Uint16(r.data[off : off+2])
}

func (r *tiffReader) u32(off int) uint32 {
	if off+4 > len(r.data) {
		return 0
	}
	return r.order.Uint32(r.data[off : off+4])
}

// rational reads a TIFF RATIONAL (two consecutive uint32) at the given offset.
func (r *tiffReader) rational(off int) float64 {
	if off+8 > len(r.data) {
		return 0
	}
	num := r.u32(off)
	den := r.u32(off + 4)
	if den == 0 {
		return 0
	}
	return float64(num) / float64(den)
}

// rationals3 reads three consecutive RATIONALs (used for GPS DMS values).
func (r *tiffReader) rationals3(off uint32) [3]float64 {
	var v [3]float64
	for i := 0; i < 3; i++ {
		v[i] = r.rational(int(off) + i*8)
	}
	return v
}

// ascii reads a TIFF ASCII field.
// rawVal is the raw 4-byte field from the IFD entry;
// if count <= 4 the string is stored inline, otherwise offset points to it.
func (r *tiffReader) ascii(count uint32, rawVal [4]byte, offset uint32) string {
	var b []byte
	if count <= 4 {
		b = rawVal[:count]
	} else {
		end := int(offset) + int(count)
		if end > len(r.data) {
			return ""
		}
		b = r.data[offset:end]
	}
	return strings.TrimRight(strings.TrimSpace(string(b)), "\x00")
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
	if off+2 > len(r.data) {
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
	DateTime string
	Camera   string
	Lat      *float64
	Lon      *float64
	Alt      *float64
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
		segLen := int(binary.BigEndian.Uint16(data[i+2 : i+4]))
		end := i + 2 + segLen

		if marker == 0xE1 && end <= len(data) && i+10 <= len(data) {
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
	result := &exifResult{}
	var exifOff, gpsOff uint32
	var hasExif, hasGPS bool

	// ── IFD0: camera model + sub-IFD pointers ──────────────────────────────
	for _, e := range tr.readIFD(ifd0Off) {
		switch e.tag {
		case 0x0110: // Model
			result.Camera = tr.ascii(e.count, e.rawVal, e.offset)
		case 0x8769: // ExifIFD pointer
			exifOff, hasExif = e.offset, true
		case 0x8825: // GPS IFD pointer
			gpsOff, hasGPS = e.offset, true
		}
	}

	// ── ExifIFD: DateTimeOriginal ───────────────────────────────────────────
	if hasExif {
		for _, e := range tr.readIFD(int(exifOff)) {
			if e.tag == 0x9003 { // DateTimeOriginal
				result.DateTime = tr.ascii(e.count, e.rawVal, e.offset)
			}
		}
	}

	// ── GPS IFD: lat / lon / alt ────────────────────────────────────────────
	if hasGPS {
		var latRef, lonRef string
		var latDMS, lonDMS [3]float64
		var hasLat, hasLon bool

		for _, e := range tr.readIFD(int(gpsOff)) {
			switch e.tag {
			case 0x0001: // GPSLatitudeRef  (e.g. "N")
				latRef = tr.ascii(e.count, e.rawVal, e.offset)
			case 0x0002: // GPSLatitude – 3 RATIONALs
				latDMS = tr.rationals3(e.offset)
				hasLat = true
			case 0x0003: // GPSLongitudeRef (e.g. "E")
				lonRef = tr.ascii(e.count, e.rawVal, e.offset)
			case 0x0004: // GPSLongitude – 3 RATIONALs
				lonDMS = tr.rationals3(e.offset)
				hasLon = true
			case 0x0006: // GPSAltitude – 1 RATIONAL
				alt := tr.rational(int(e.offset))
				result.Alt = &alt
			}
		}

		if hasLat && hasLon {
			lat := dmsToDecimal(latDMS, latRef)
			lon := dmsToDecimal(lonDMS, lonRef)
			result.Lat = &lat
			result.Lon = &lon
		}
	}

	return result
}

// ════════════════════════════════════════════════════════════════════════════
// XMP parser
// ════════════════════════════════════════════════════════════════════════════

// xmpAttrRe matches [namespace:]Key="value" attributes in XMP XML.
var xmpAttrRe = regexp.MustCompile(`(?:[\w-]+:)?([\w]+)="([^"]*)"`)

// skipXMPKeys contains XML boilerplate keys to ignore.
var skipXMPKeys = map[string]bool{
	"xmlns": true, "about": true, "xmptk": true,
}

// parseXMP extracts all key/value attribute pairs from an XMP block string,
// stripping namespace prefixes (e.g. "drone-dji:GimbalPitchDegree" → "GimbalPitchDegree").
func parseXMP(xmpData string) map[string]string {
	result := make(map[string]string)
	for _, m := range xmpAttrRe.FindAllStringSubmatch(xmpData, -1) {
		key := m[1]
		if skipXMPKeys[key] || strings.HasPrefix(strings.ToLower(key), "xmlns") {
			continue
		}
		result[key] = m[2]
	}
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

func newMetadata() Metadata {
	return Metadata{Fields: make(map[string]interface{})}
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
		if exif.Camera != "" {
			meta.Fields["camera"] = exif.Camera
		}
		meta.Lat = exif.Lat
		meta.Lon = exif.Lon
		meta.Alt = exif.Alt
	} else {
		fmt.Printf("  ⚠ No EXIF data found in %s\n", filepath.Base(filePath))
	}

	// ── XMP ──────────────────────────────────────────────────────────────────
	xmpStart := bytes.Index(raw, []byte("<x:xmpmeta"))
	if xmpStart != -1 {
		remaining := raw[xmpStart:]
		xmpEnd := bytes.Index(remaining, []byte("</x:xmpmeta>"))
		if xmpEnd != -1 {
			xmpBlock := string(remaining[:xmpEnd+12])
			for k, v := range parseXMP(xmpBlock) {
				if _, exists := meta.Fields[k]; exists {
					continue // never override EXIF values
				}
				if fv, err := strconv.ParseFloat(v, 64); err == nil {
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
	Type     string       `json:"type"`
	Features []geoFeature `json:"features"`
}

func buildGeoJSON(cfg Config) {
	var features []geoFeature

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

		if meta.Lat == nil || meta.Lon == nil {
			fmt.Printf("  ⚠ No GPS data – skipped.\n")
			return nil
		}

		alt := 0.0
		if meta.Alt != nil {
			alt = *meta.Alt
		}

		outputDir := filepath.Dir(cfg.OutputFile)
		relPath, _ := filepath.Rel(outputDir, path)

		props := map[string]interface{}{
			"filename":          d.Name(),
			"filepath":          path,
			"relative_filepath": filepath.ToSlash(relPath),
		}
		for k, v := range meta.Fields {
			props[k] = v
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

	out, err := json.MarshalIndent(geoCollection{Type: "FeatureCollection", Features: features}, "", "    ")
	if err != nil {
		fmt.Printf("JSON error: %v\n", err)
		return
	}
	if err := os.WriteFile(cfg.OutputFile, out, 0644); err != nil {
		fmt.Printf("Error writing output: %v\n", err)
		return
	}

	fmt.Printf("\n✅ GeoJSON created! Found %d valid photos. Saved to %s\n",
		len(features), cfg.OutputFile)
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
}
