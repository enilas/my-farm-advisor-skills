# Local Instructions

## Purpose

This folder owns the field-portfolio EDA workflow: cross-grower comparison of
boundaries, crop history, and climate, with static outputs and a machine-
readable analysis contract.

## Safe edit scope

Edits should stay in this folder and its children unless the user explicitly
asks for a broader skill change. Runnable code lives in
`../../data-pipeline/src/scripts/eda/field_portfolio/`; changing it from this
folder is expected for this workflow. Do not change parent `SKILL.md`, sibling
EDA workflows, or root policy from a subskill task unless explicitly requested.

## Read nearby docs first

Read `GUIDE.md` first. If routing context is needed, read `../INDEX.md` and
`../../SKILL.md`. Read `../../data-pipeline/AGENTS.md` before touching runtime
paths, the installer, or output locations.

## Local rules

- Stages `boundaries`, `cdl`, and `weather` are built. Review each stage on
  its own: a new figure belongs in its stage's `figures_*.py`, not bolted onto
  another stage's module.
- Every figure writes one `analysis.json` record with `n`, `unit`, `stat`,
  `finding`, and `caveat`. A figure without a record, or a record whose finding
  disagrees with the render, is a bug.
- Effect size travels with every p-value. Do not add a test that reports only p.
- Weather inference is at cell-year, never field-day. Keep `weather_cell` on
  every field row.
- `lib/field_frame.py` and `lib/palettes.py` are consumed read-only. If one
  needs changing, change the library and re-review every figure it draws.
- Outputs go to `<runtime>/derived/eda/field-portfolio/` with stable relative
  paths. Never write into the skill checkout.
- The report is out of scope. This workflow stops at `analysis.json`.

## Local validation

```bash
uv run --with pytest --with geopandas --with pandas --with scipy \
  --with matplotlib python -m pytest \
  src/scripts/tests/test_eda_field_portfolio.py -q
```

Then refresh the runtime source and run the stage against the real runtime:

```bash
cd ../../data-pipeline
DATA_PIPELINE_DATA_ROOT="$DATA_PIPELINE_DATA_ROOT" \
  ./scripts/install.sh --non-interactive --force-refresh --no-install-deps
cd "$DATA_PIPELINE_DATA_ROOT/data-pipeline/src"
"$DATA_PIPELINE_DATA_ROOT/data-pipeline/.venv/bin/python" \
  scripts/eda/field_portfolio/run.py --stage all
```

Run `./scripts/validate.sh` from the repository root after structural changes.
Open every written PNG and pass the figure review in `../../../CONTRIBUTING.md`
before committing; record the verdict in the commit message.

## Local-delta-only reminder

This nested AGENTS.md only records instructions that differ from the parent or
root files. Do not duplicate root-wide asset, vendor, or validation policy here
except this pointer to `../../../AGENTS.md`.
