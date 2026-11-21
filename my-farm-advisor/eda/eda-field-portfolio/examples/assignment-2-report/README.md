# Assignment 2 report fixture

A committed copy of the one-command field-portfolio run, so a reader can open
the result offline without a runtime.

```
output/report.html     the single-page report
output/figures/*.png    the 11 figures the page references
```

Open `output/report.html` in any browser. Use the browser's **Print → Save as
PDF** for a paginated A4 copy; the page carries print CSS for that.

## What this demonstrates

`./scripts/run-eda-field-portfolio.sh` refreshes the runtime source, runs the
three EDA stages (boundaries, crop history, weather), and renders
`report.html` from `analysis.json`. The page is a *renderer*: every number is
copied from the analysis contract, nothing is recomputed.

Each card carries the figure's question, finding, test and effect size, sample
size and unit, and its caveat. The footer lists the dataset-validator status
and a sha256 prefix per figure so a regenerated copy can be compared.

## Regenerate

From a runtime that holds the three-grower dataset:

```bash
./build.sh /absolute/path/to/my-farm-advisor-runtime
```

The script runs the wrapper into a temp directory and copies only `report.html`
and `figures/*.png` here. `analysis.json`, `manifest.json`, and
`tables/table_fields.csv` are deliberately not committed: they carry
timestamps, mtimes, and per-field rows, and the page already carries the
figure hashes.

## Why it is committed

`ASSET_POLICY.md` allows generated outputs at exactly one place:
`examples/**/output/`. Every file here is small (the page is under 20 KB, the
largest PNG is under 200 KB), so the fixture stays well under the 1 MB
per-file cap and needs no Git LFS.
