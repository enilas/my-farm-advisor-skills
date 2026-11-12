# Grower Web Map Index

Use this package when a grower needs one interactive HTML map covering all
fields across all of their farms.

## Entry points

- [SKILL.md](SKILL.md) - compact routing entrypoint for agents.
- [README.md](README.md) - output contract, CLI, inputs, dependencies, install,
  and validation.
- [PROVENANCE.md](PROVENANCE.md) - boundary source, basemap attribution,
  library license, and embedded-data policy.

## Runtime code

- [`../../src/scripts/reporting/generate_grower_web_map.py`](../../src/scripts/reporting/generate_grower_web_map.py) -
  the generator: reads pipeline boundaries and rotation tables, builds the
  payload, and writes the single-file HTML map.
- [`../../src/scripts/lib/paths.py`](../../src/scripts/lib/paths.py) -
  `grower_maps_dir()` and `grower_map_path()` define the canonical output
  location (`growers/<grower>/maps/`).

## Related

- [../README.md](../README.md) - data-pipeline runtime setup and shared
  conventions.
- [../../../admin/interactive-web-map/GUIDE.md](../../admin/interactive-web-map/GUIDE.md) -
  generic folium/Leaflet recipe for ad-hoc maps, separate from this skill.
