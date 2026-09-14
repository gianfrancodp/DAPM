package main

import (
	"bufio"
	"bytes"
	"encoding/binary"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
)

type dbfColumn struct {
	Name, Source string
	Width        byte
}

func exportValidData(features []geoFeature, output string) {
	base := strings.TrimSuffix(output, filepath.Ext(output))
	headers := exportHeaders(features)
	if err := exportCSV(features, headers, base+".csv"); err != nil {
		fmt.Printf("CSV export error: %v\n", err)
	}
	if err := exportShapefile(features, headers, base); err != nil {
		fmt.Printf("Shapefile export error: %v\n", err)
	}
}

func exportHeaders(features []geoFeature) []string {
	seen := map[string]bool{"longitude": true, "latitude": true, "altitude": true}
	extra := []string{}
	for _, f := range features {
		for k := range f.Properties {
			if !seen[k] {
				seen[k] = true
				extra = append(extra, k)
			}
		}
	}
	sort.Strings(extra)
	return append([]string{"longitude", "latitude", "altitude"}, extra...)
}

func exportValue(f geoFeature, k string) string {
	if k == "longitude" {
		return strconv.FormatFloat(f.Geometry.Coordinates[0], 'f', -1, 64)
	}
	if k == "latitude" {
		return strconv.FormatFloat(f.Geometry.Coordinates[1], 'f', -1, 64)
	}
	if k == "altitude" {
		return strconv.FormatFloat(f.Geometry.Coordinates[2], 'f', -1, 64)
	}
	value, ok := f.Properties[k]
	if !ok || value == nil {
		return ""
	}
	return fmt.Sprint(value)
}

func exportCSV(features []geoFeature, headers []string, path string) error {
	f, e := os.Create(path)
	if e != nil {
		return e
	}
	defer f.Close()
	w := bufio.NewWriter(f)
	defer w.Flush()
	writeCSVRow(w, headers)
	for _, x := range features {
		row := make([]string, len(headers))
		for i, h := range headers {
			row[i] = csvCell(exportValue(x, h))
		}
		writeCSVRow(w, row)
	}
	return nil
}

func exportShapefile(features []geoFeature, headers []string, base string) error {
	used := map[string]bool{}
	cols := make([]dbfColumn, len(headers))
	for i, h := range headers {
		n := 1
		for _, f := range features {
			if z := len([]byte(exportValue(f, h))); z > n {
				n = z
			}
		}
		if n > 254 {
			n = 254
		}
		cols[i] = dbfColumn{dbfName(h, used), h, byte(n)}
	}
	if e := writeExportDBF(features, cols, base+".dbf"); e != nil {
		return e
	}
	if e := writeExportSHP(features, base); e != nil {
		return e
	}
	os.WriteFile(base+".prj", []byte("GEOGCS[\"WGS 84\",DATUM[\"WGS_1984\",SPHEROID[\"WGS 84\",6378137,298.257223563]],PRIMEM[\"Greenwich\",0],UNIT[\"degree\",0.0174532925199433]]"), 0644)
	os.WriteFile(base+".cpg", []byte("UTF-8\n"), 0644)
	return nil
}

func dbfName(s string, used map[string]bool) string {
	var b strings.Builder
	for _, r := range strings.ToUpper(s) {
		if r == '_' || (r >= 'A' && r <= 'Z') || (r >= '0' && r <= '9') {
			b.WriteRune(r)
		}
	}
	x := b.String()
	if x == "" {
		x = "FIELD"
	}
	if len(x) > 10 {
		x = x[:10]
	}
	base := x
	for i := 2; used[x]; i++ {
		q := strconv.Itoa(i)
		x = base[:minExport(len(base), 10-len(q))] + q
	}
	used[x] = true
	return x
}
func minExport(a, b int) int {
	if a < b {
		return a
	}
	return b
}

func writeExportDBF(features []geoFeature, cols []dbfColumn, path string) error {
	f, e := os.Create(path)
	if e != nil {
		return e
	}
	defer f.Close()
	hl, rl := 32+32*len(cols)+1, 1
	for _, c := range cols {
		rl += int(c.Width)
	}
	h := make([]byte, 32)
	h[0] = 3
	n := time.Now()
	h[1], h[2], h[3] = byte(n.Year()-1900), byte(n.Month()), byte(n.Day())
	binary.LittleEndian.PutUint32(h[4:], uint32(len(features)))
	binary.LittleEndian.PutUint16(h[8:], uint16(hl))
	binary.LittleEndian.PutUint16(h[10:], uint16(rl))
	f.Write(h)
	for _, c := range cols {
		d := make([]byte, 32)
		copy(d, c.Name)
		d[11] = 'C'
		d[16] = c.Width
		f.Write(d)
	}
	f.Write([]byte{13})
	for _, x := range features {
		r := bytes.Repeat([]byte(" "), rl)
		r[0] = ' '
		p := 1
		for _, c := range cols {
			v := []byte(exportValue(x, c.Source))
			if len(v) > int(c.Width) {
				v = v[:c.Width]
			}
			copy(r[p:p+int(c.Width)], v)
			p += int(c.Width)
		}
		f.Write(r)
	}
	_, e = f.Write([]byte{26})
	return e
}

func writeExportSHP(features []geoFeature, base string) error {
	x0, y0, z0 := math.Inf(1), math.Inf(1), math.Inf(1)
	x1, y1, z1 := math.Inf(-1), math.Inf(-1), math.Inf(-1)
	for _, f := range features {
		c := f.Geometry.Coordinates
		x0 = math.Min(x0, c[0])
		x1 = math.Max(x1, c[0])
		y0 = math.Min(y0, c[1])
		y1 = math.Max(y1, c[1])
		z0 = math.Min(z0, c[2])
		z1 = math.Max(z1, c[2])
	}
	if len(features) == 0 {
		x0, y0, z0, x1, y1, z1 = 0, 0, 0, 0, 0, 0
	}
	head := func(words int) []byte {
		b := make([]byte, 100)
		binary.BigEndian.PutUint32(b, 9994)
		binary.BigEndian.PutUint32(b[24:], uint32(words))
		binary.LittleEndian.PutUint32(b[28:], 1000)
		binary.LittleEndian.PutUint32(b[32:], 11)
		for i, v := range []float64{x0, y0, x1, y1, z0, z1, 0, 0} {
			binary.LittleEndian.PutUint64(b[36+i*8:], math.Float64bits(v))
		}
		return b
	}
	shp, e := os.Create(base + ".shp")
	if e != nil {
		return e
	}
	defer shp.Close()
	shx, e := os.Create(base + ".shx")
	if e != nil {
		return e
	}
	defer shx.Close()
	shp.Write(head(50 + 22*len(features)))
	shx.Write(head(50 + 4*len(features)))
	off := 50
	for i, f := range features {
		c := f.Geometry.Coordinates
		r := make([]byte, 36)
		binary.LittleEndian.PutUint32(r, 11)
		for j, v := range []float64{c[0], c[1], c[2], -1e38} {
			binary.LittleEndian.PutUint64(r[4+j*8:], math.Float64bits(v))
		}
		rh := make([]byte, 8)
		binary.BigEndian.PutUint32(rh, uint32(i+1))
		binary.BigEndian.PutUint32(rh[4:], 18)
		shp.Write(rh)
		shp.Write(r)
		ix := make([]byte, 8)
		binary.BigEndian.PutUint32(ix, uint32(off))
		binary.BigEndian.PutUint32(ix[4:], 18)
		shx.Write(ix)
		off += 22
	}
	return nil
}
