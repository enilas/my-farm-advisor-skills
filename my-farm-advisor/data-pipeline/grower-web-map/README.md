# Grower Web Map

A grower-level interactive field map: one HTML file per grower, all farms and
fields on one page, satellite basemap, clickable polygons, and a field list that
zooms to any field.

This package is deliberately **separate** from `admin/interactive-web-map/`.
That package is a generic folium/Leaflet recipe with no knowledge of the data
pipeline. This one is the pipeline-integrated renderer: canonical runtime paths,
pipeline boundary files, rotation tables, and a zero-dependency HTML template
owned here.

## What it produces

For each grower:

```
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower_slug>/maps/<grower_slug>_fields_map.html
```

The page contains:

- Every field polygon from every farm of that grower, in EPSG:4326, with
  coordinates rounded to 6 decimal places to keep the payload small.
- **Esri World Imagery** satellite basemap by default, plus an OpenStreetMap
  streets layer in the layer control. Tiles are fetched by the browser; nothing
  raster is embedded.
- Zoom in/out, pan, and an initial view fitted to all fields.
- Clickable fields with a popup showing field name, grower, farm, area, county
  and state, boundary source, and — when the farm has a crop-rotation table — the
  5-year rotation sequence and predicted next crop.
- A sidebar list of fields grouped by farm; clicking an entry zooms to that
  field and opens its popup. "Fit all fields" resets the view.
- A total field count and acreage in the header.

Vectors only. Typical output is 10–40 KB for a handful of fields; the size scales
with boundary complexity, not with imagery or time series.

## Command

Run from the installed runtime source copy:

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/reporting/generate_grower_web_map.py \
  --grower-slug il-grower --grower-slug ia-grower
```

Defaults:

- `--grower-slug` may be repeated. Omit it to render every grower that has at
  least one farm with a boundary file.
- `--output PATH` writes a single grower's map somewhere else; it requires
  exactly one `--grower-slug`.
- `--title TEXT` overrides the page title.
- `--no-rotation` omits rotation summaries even when the table exists.

Exit status is non-zero when a requested grower has no renderable fields, so the
command can be used as a test hook.

## Inputs (all pipeline-owned)

| Input | Path |
| --- | --- |
| Field polygons | `growers/<grower>/farms/<farm>/boundary/field_boundaries.geojson` |
| Grower name | `growers/<grower>/grower.json` (`display_name`, else slug) |
| Farm name | `growers/<grower>/farms/<farm>/farm.json` (`display_name`, else slug) |
| Rotation summary | `growers/<grower>/farms/<farm>/derived/tables/*_crop_rotation.csv` |

Geometry is the actual downloaded boundary — it is not simplified, so the map
shows the same polygon the reports and zonal statistics use. Only coordinate
precision is rounded for display.

## Dependencies

No new Python dependencies: `geopandas` and `pandas` are already in the runtime
venv. Leaflet 1.9.4 is loaded from the unpkg CDN, and basemap tiles come from
Esri and OpenStreetMap. A browser needs internet access to draw the basemap;
field polygons, popups and the field list work without it.

## Install into a runtime

The script lives in the canonical runtime tree
(`data-pipeline/src/scripts/reporting/`), so the normal installer seeds it:

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd my-farm-advisor/data-pipeline
./scripts/install.sh --non-interactive --force-refresh --no-install-deps
```

## Validation

- The root validator must stay green: `./scripts/validate.sh`.
- Generated HTML is runtime-only and must not be committed.
- A structural smoke test can assert the file size, the number of embedded
  features, and the presence of the grower, farm and field names:

  ```bash
  python3 - <<'PY'
  import json, re, pathlib
  p = pathlib.Path("/absolute/path/to/runtime/data-pipeline/growers/il-grower/maps/il_grower_fields_map.html")
  html = p.read_text()
  payload = json.loads(re.search(r"var payload = (\{.*?\});", html, re.S).group(1))
  print(p.stat().st_size // 1024, "KB", len(payload["features"]), "fields")
  PY
  ```
