---
name: grower-web-map
description: >
  Grower-level interactive field web map for My Farm Advisor. Use when a grower
  needs one lightweight, self-contained HTML map of every field across all of
  their farms, drawn over an online satellite basemap with clickable field
  metadata and a zoom-to-field list.
license: Apache-2.0
metadata:
  author: Clayton Young / Superior Byte Works, LLC (@borealBytes)
  version: "0.1.0"
  skill-author: Clayton Young / Superior Byte Works, LLC (@borealBytes)
  skill-version: "0.1.0"
---

# Grower Web Map

Use this nested skill to render one interactive web map per **grower** from the
field polygons the data pipeline already produced. The map is a single HTML file:
boundaries are embedded as GeoJSON, and satellite imagery is streamed by the
browser from Esri World Imagery — no rasters or imagery are copied into the page.

## Start Here

- Read [README.md](README.md) for the output contract, CLI, basemap and size policy.
- Read [INDEX.md](INDEX.md) for file-level navigation.
- Read [PROVENANCE.md](PROVENANCE.md) before changing tiles, libraries, or attribution.

## Routing Notes

- Invoke when the request mentions a grower map, farm map, all fields on one map,
  field boundaries over satellite imagery, clickable field popups, or a shareable
  HTML map for a grower.
- Use the runtime command below from the installed data-pipeline source copy, not
  from the skill checkout:

  ```bash
  export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
  cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
  "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
    scripts/reporting/generate_grower_web_map.py \
    --grower-slug il-grower --grower-slug ia-grower
  ```

- One map covers **all farms of a grower**. Field polygons come from each
  `growers/<grower>/farms/<farm>/boundary/field_boundaries.geojson`, the same
  file the reporting pipeline uses.
- Output stays runtime-only under
  `growers/<grower>/maps/<grower>_fields_map.html`, per the repository asset policy.
- For a single-field static figure or a generic folium recipe, use the field
  renderers in `data-pipeline/src/scripts/` or `admin/interactive-web-map/`
  instead; this skill is the pipeline-integrated grower map.
