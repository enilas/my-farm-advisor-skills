# Grower Web Map Provenance

This file records where the map's data and rendering dependencies come from, so
future changes keep attribution and licensing intact. It is a policy record, not
a data snapshot: no boundaries, tiles, or generated HTML are stored here.

## Status

- **Origin:** new skill in this repository (Assignment 1). Not imported, copied,
  or vendored from another skill package.
- **Relationship to other skills:** independent of `admin/interactive-web-map/`.
  Only the public Leaflet library and public tile services are shared, which any
  web map uses. No code is reused from that package.
- **Generated output:** runtime-only at
  `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/maps/`. Never commit
  generated HTML.

## Field boundaries

- **Source:** the data pipeline's own boundary ingestion (USDA Crop Sequence
  Boundaries GeoParquet for US farms, per
  `data-pipeline/references/field-boundary-sources.md`). This skill reads the
  already-materialised file at
  `growers/<grower>/farms/<farm>/boundary/field_boundaries.geojson`; it does not
  contact a boundary provider itself.
- **License note:** USDA CSB is public-domain US Government data. The boundary
  file also carries its own `source` field, which the map surfaces in the popup.

## Basemap tiles

- **Esri World Imagery** (default layer):
  `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}`
  with the attribution "Tiles © Esri — Source: Esri, Maxar, Earthstar
  Geographics, and the GIS User Community".
  Used as an online reference basemap only. The skill does not download, cache,
  or redistribute tiles.
- **OpenStreetMap** (optional streets layer):
  `https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png`, data © OpenStreetMap
  contributors, ODbL. Subject to the OSM Foundation tile usage policy; this is
  interactive human browsing, not bulk tile harvesting.

Both layers may be swapped in the running map; neither is bundled into the HTML.

## Rendering library

- **Leaflet 1.9.4**, BSD-2-Clause, loaded from
  `https://unpkg.com/leaflet@1.9.4/dist/leaflet.{css,js}`.
- No JavaScript library is vendored into this repository. If the CDN is
  unavailable the page still parses, but the map will not initialise; that is an
  accepted trade-off for keeping the HTML small.

## Python dependencies

`geopandas` and `pandas` only, both already present in the runtime virtualenv
created by `scripts/install.sh`. No new dependency is introduced, so no
`requirements.txt` change is required.

## Privacy and size policy

- Only field-boundary vertices, field/farm/grower names, acreage, county/state,
  and optional rotation labels are embedded.
- No imagery, raster values, time series, or farm financial data are embedded.
- Coordinates are rounded to 6 decimal places (about 11 cm) for display.
