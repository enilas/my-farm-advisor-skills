---
name: eda-field-portfolio
description: Compare a grower's field portfolio across boundaries, crop history, and climate with reproducible static figures, tables, and an analysis contract a report can consume.
version: 0.1.0
author: Boreal Bytes
tags: [eda, portfolio, boundaries, cdl, weather, comparison, static-figures]
---

# Workflow: eda-field-portfolio

## Description

Answer portfolio-scale questions about a set of farms: how do field sizes and
shapes differ, how has the crop mix moved, and how does the climate differ
across the sites? Unlike the other EDA guides, this one is **runnable code
plus a fixed output contract**, not a recipe to copy. Every figure is a static
PNG, every claim in `analysis.json` names its test, effect size, sample size,
and caveat, and `manifest.json` hashes every input and output so a rerun is
auditable.

The workflow is deliberately staged. Stage 1 (boundaries) is implemented and
reviewed; CDL and weather land as their own reviewable stages.

## When to Use This Workflow

- Several farms/growers are already ingested under `growers/*/farms/*`.
- The question is comparative: across fields, across growers, or over time.
- The output is a static figure set for a report, not an interactive dashboard.
- You need reproducibility (same inputs -> same figures) rather than one-off
  exploration.

Do not use it for a single field's agronomy, soil analysis, yield analysis, or
imagery; those belong to their own subskills.

## What It Compares

| Axis | Evidence | Unit of analysis |
| --- | --- | --- |
| Within a field over time | CDL 2019–2023, weather 2021–2025 | field-year / site-year |
| Across fields within a grower | boundary geometry, crop history | field |
| Across growers | boundary geometry, crop history, climate | field, or cell for weather |

Boundaries have no time dimension, so "within a field over time" is carried by
crop history and weather only. There is no yield, soil, DEM, or satellite layer
in the current runtime, so nothing here should be read as a productivity claim.

## Data Contract

Read from `${DATA_PIPELINE_DATA_ROOT}/data-pipeline`:

- `growers/<grower>/farms/<farm>/boundary/field_boundaries.geojson` — EPSG:4326,
  one feature per field, `field_id` equal to the slug used everywhere else.
- `growers/<grower>/farms/<farm>/derived/tables/*weather*.csv` — per field, per
  day: `field_id, date, lat, lon, T2M, T2M_MAX, T2M_MIN, PRECTOTCORR`.
- `growers/<grower>/farms/<farm>/derived/tables/<farm>_<year>_cdl.csv` — five
  years by default.
- `growers/<grower>/farms/<farm>/farm.json` — display name and state.

The loader fails loudly instead of guessing: duplicate `field_id` across
growers, a field missing from the weather table, fewer than five CDL years, or
a boundary without CRS all stop the run. Weather is then snapped to NASA POWER
0.5° cells and the cell id is carried on every field row.

`validate_dataset.py` runs first and aborts on any failure, so analysis cannot
silently consume a dataset the validator rejects. `--skip-validate` exists for
deliberate forensics and is recorded in the manifest.

## Run It

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/eda/field_portfolio/run.py --stage boundaries
```

- `--root` overrides the environment variable.
- `--out-dir` writes somewhere other than
  `<runtime>/data-pipeline/derived/eda/field-portfolio`.
- `--require-layers weather,cdl|none` relaxes the layer gate (recorded).
- `--skip-validate` is recorded in the manifest; avoid it for real runs.
- The `cdl`, `weather`, and `all` stages exit with a message until built.

Install/refresh the runtime source before a run when the checkout changed:

```bash
cd my-farm-advisor/data-pipeline
DATA_PIPELINE_DATA_ROOT="$DATA_PIPELINE_DATA_ROOT" \
  ./scripts/install.sh --non-interactive --force-refresh --no-install-deps
```

## Output Contract

```
derived/eda/field-portfolio/
  figures/                 b1_*.png b2_*.png b3_*.png m1_*.png
  tables/table_fields.csv  one row per field, stable column order
  analysis.json            the contract the report consumes
  manifest.json            inputs/outputs with sha256, validator status, git head
```

`analysis.json` carries `dataset` (farms, growers, fields, cells, CDL years),
`unit_of_analysis` per category, and one record per figure:

```json
{
  "id": "b2_area_vs_shape",
  "category": "boundaries",
  "scope": "across fields within and across growers",
  "title": "Field size does not predict shape",
  "question": "Does field size predict boundary complexity?",
  "finding": "Pooled Spearman rho = +0.35 (95% CI -0.07 to +0.68) ...",
  "n": 30,
  "unit": "field",
  "stat": {"test": "spearman", "rho": 0.3495, "p": 0.058339, "n": 30,
           "ci95": [-0.072, 0.6799]},
  "caveat": "Rank correlation over 30 fields resolves only strong ..."
}
```

A report renders this; it must not recompute or re-derive the numbers. If a
figure's message changes, change the record here, not in the report.

## Figures and What They Tell

### B1 — `b1_area_distribution.png`

Box strip of field area per grower with a Kruskal-Wallis test, ε², the median
in-grower IQR, and the span between grower medians. Tells whether grower
identity predicts field size. On the assignment-2 portfolio: H = 18.60,
p < 0.001, ε² = 0.61; medians IL 245, IA 243, NE 208 ha; in-grower IQR 30 ha
against a 37 ha median gap. The groups separate as samples, but IL and IA
overlap over most of their range, so size alone does not identify a field's
grower.

### B2 — `b2_area_vs_shape.png`

Scatter of area against shape index, pooled and per grower, with a bootstrap
confidence interval on the pooled Spearman ρ. Tells whether big fields are
also oddly shaped. Pooled ρ = +0.35 (95% CI −0.07 to +0.68, p = 0.058): no
evidence of a monotone trend. Shape index is scale-free, so this is a real
result, not an artefact of units.

### B3 — `b3_geometry_correlation.png`

Spearman matrix over seven boundary metrics. Tells which metrics carry
independent information. Area↔perimeter ρ = +0.57; perimeter, shape index and
vertices co-vary at |ρ| = 0.64–0.95; compactness is exactly 1 / shape index²,
so ρ = −1.00 (boxed). Report area plus one shape metric — the rest are
duplicates, and the near-1.0 cells are properties of the definitions, not of
these fields.

### M1 — `m1_fields_by_grower.png`

One farm-layout panel per grower in that panel's own field-local frame
(auto-rotated, metre axes, north arrow placed in the emptiest corner), fields
filled by a continuous area ramp, smallest and largest labelled. Tells how
layouts differ and how many weather cells hide behind 10 fields. On the
assignment-2 portfolio: 30 fields, 17,909 acres, 10 NASA POWER cells — IL 3,
IA 4, NE 3 — so field-level weather is not independent.

## Conventions

- **GDD**: base 10 °C, upper cap 30 °C on Tmax,
  `max(0, (min(Tmax, 30) + Tmin) / 2 − 10)`. A 36 °C day counts as 30 °C.
- **Frost**: `Tmin < 0 °C`, last spring frost inside Mar 1–Jun 30, first fall
  frost inside Aug 1–Nov 30. A January thaw is not the season start.
- **Season precipitation**: April–September inclusive by default.
- **Area/shape**: metrics computed in EPSG:5070 equal-area; shape index =
  perimeter / equal-area-circle perimeter.

## Inference Rules

- Boundary statistics use the field as the unit.
- Weather uses the NASA POWER cell (or site-year), never the field: 30 fields
  can sit in 10 cells, and treating them as independent inflates significance.
  The cell id travels in `table_fields.csv` for this reason.
- CDL uses field-year. Consecutive years of the same crop are a rotation
  signal, not independent replicates.
- Tests are rank-based (Kruskal-Wallis, Spearman, permutation) because group
  sizes are small and distributions are not normal. Every record carries an
  effect size and an explicit caveat.

## Extending

- Stage 2 (CDL): add `figures_cdl.py`, register it in `run.py`, and add the
  records. Follow the same four-figure shape: two statistical views, one
  correlation/comparison, one map.
- Stage 3 (weather): add `figures_weather.py`, aggregate to cell-year before
  testing, and report the cell count next to every n.
- Reuse `metrics.py` for GDD/frost so all stages use one definition.
- Keep `lib/field_frame.py` and `lib/palettes.py` read-only; if a frame or
  palette change is needed, change the library and re-review every figure it
  draws.

## Tests

```bash
uv run --with pytest --with geopandas --with pandas --with scipy \
  --with matplotlib python -m pytest \
  src/scripts/tests/test_eda_field_portfolio.py -q
```

Offline fixtures cover GDD capping, frost windows, season precipitation,
effect sizes, cell snapping, the known-square geometry check, loader failures,
and the full runner contract.
