"""Render ``analysis.json`` as a single static HTML page.

This module is a *renderer*, not an analyst. Every number it prints comes from
the contract that ``run.py`` already wrote; nothing is recomputed and nothing
is inferred. Two consequences shape the code:

* It reads only ``analysis.json`` plus the figure files on disk (for hashes).
* It never embeds volatile metadata — no ``generated_at``, no git head, no
  mtimes, no absolute paths — so two runs over the same data produce
  byte-identical HTML and a committed copy stays stable.

The page is self-contained CSS with figures referenced relatively, which keeps
it small, diffable, and printable to PDF from any browser.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
from pathlib import Path

CATEGORY_ORDER = ("boundaries", "crop history", "weather")

CATEGORY_STORY = {
    "boundaries": (
        "Field size and shape from the ingested boundaries. The unit is the "
        "field; there is no time dimension here."
    ),
    "crop history": (
        "What each field actually grew, using the dominant CDL class per "
        "field-year. Consecutive years are a sequence, not independent samples."
    ),
    "weather": (
        "Seasonal heat, frost, and rain on the NASA POWER cell, rolled to "
        "cell-year. Thirty fields are not thirty climates."
    ),
}

TEST_LABELS = {
    "kruskal-wallis": "Kruskal–Wallis",
    "spearman": "Spearman ρ",
    "spearman_matrix": "Spearman matrix",
    "permutation_mean_difference": "Permutation test",
    "chi_square_independence": "χ² independence",
    "descriptive": "Descriptive",
    "composition": "Composition",
    "sequence": "Sequence",
    "frost_window": "Frost window",
}

FACT_LABELS = (
    ("fields", "fields"),
    ("growers", "growers"),
    ("weather_cells", "weather cells"),
)

STYLE = """
:root {
  --ink: #111827;
  --muted: #4b5563;
  --line: #e5e7eb;
  --card: #f9fafb;
  --accent: #0f766e;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: #fff;
  color: var(--ink);
  font: 16px/1.55 ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto,
        Helvetica, Arial, sans-serif;
}
.wrap { max-width: 1060px; margin: 0 auto; padding: 44px 24px 72px; }
.eyebrow {
  margin: 0 0 6px;
  font-size: 12px;
  letter-spacing: .12em;
  text-transform: uppercase;
  color: var(--accent);
  font-weight: 600;
}
h1 { margin: 0 0 10px; font-size: 34px; line-height: 1.15; letter-spacing: -.01em; }
.lede { margin: 0 0 18px; font-size: 17px; color: var(--muted); max-width: 62ch; }
.howto {
  margin: 22px 0 0;
  padding: 14px 16px;
  border-left: 3px solid var(--accent);
  background: var(--card);
  border-radius: 0 8px 8px 0;
  font-size: 14px;
  color: var(--muted);
  max-width: 70ch;
}
.howto b { color: var(--ink); }
.command {
  display: inline-block;
  padding: 8px 12px;
  border: 1px solid var(--line);
  border-radius: 6px;
  background: var(--card);
  font: 13px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  color: var(--muted);
}
.facts {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
  margin: 28px 0 8px;
}
.fact {
  flex: 1 1 130px;
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px 14px;
  background: #fff;
}
.fact b { display: block; font-size: 22px; line-height: 1.2; }
.fact span { font-size: 12px; color: var(--muted); text-transform: uppercase; letter-spacing: .06em; }
nav.toc { display: flex; gap: 18px; flex-wrap: wrap; margin: 18px 0 8px; font-size: 14px; }
nav.toc a { color: var(--accent); text-decoration: none; border-bottom: 1px solid currentColor; }
section { margin-top: 44px; }
section > h2 {
  margin: 0 0 4px;
  font-size: 13px;
  letter-spacing: .12em;
  text-transform: uppercase;
  color: var(--accent);
}
section > .story { margin: 0 0 18px; color: var(--muted); max-width: 70ch; }
.card {
  border: 1px solid var(--line);
  border-radius: 10px;
  overflow: hidden;
  background: #fff;
  margin-bottom: 22px;
}
.card img { display: block; width: 100%; height: auto; background: #fff; }
.card .body { padding: 16px 18px 18px; }
.card h3 { margin: 0 0 2px; font-size: 19px; line-height: 1.3; }
.question { margin: 0 0 10px; font-size: 13px; color: var(--muted); font-style: italic; }
.finding { margin: 0 0 12px; }
.chips { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 12px; padding: 0; list-style: none; }
.chips li {
  font: 12px/1.4 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  background: var(--card);
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 3px 9px;
  color: #374151;
}
.caveat {
  margin: 0;
  padding-left: 12px;
  border-left: 3px solid var(--line);
  font-size: 13.5px;
  color: var(--muted);
}
.caveat b { color: var(--ink); font-weight: 600; }
.methods { margin-top: 48px; border-top: 2px solid var(--ink); padding-top: 20px; }
.methods h2 { margin: 0 0 10px; font-size: 18px; }
.methods dl { display: grid; grid-template-columns: max-content 1fr; gap: 6px 18px; margin: 0; font-size: 14px; }
.methods dt { font-weight: 600; }
.methods dd { margin: 0; color: var(--muted); }
footer { margin-top: 36px; border-top: 1px solid var(--line); padding-top: 16px; font-size: 13px; color: var(--muted); }
footer code { color: var(--ink); }
.hashes { width: 100%; border-collapse: collapse; margin: 12px 0 16px; font: 12px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
.hashes th, .hashes td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--line); }
.hashes th { color: var(--muted); font-weight: 600; }
.note { margin: 10px 0 0; }
@media (max-width: 720px) {
  h1 { font-size: 27px; }
}
@media print {
  @page { size: A4; margin: 14mm; }
  body { font-size: 11pt; }
  .wrap { max-width: none; padding: 0; }
  nav.toc { display: none; }
  section { break-before: page; margin-top: 0; }
  .card { break-inside: avoid; margin-bottom: 16px; }
  .card img { max-height: 175mm; object-fit: contain; }
  a { color: inherit; }
}
"""


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _p_text(value: float) -> str:
    if value < 0.001:
        return "p < 0.001"
    return f"p = {value:.3f}"


def _signed(value: float, digits: int = 2) -> str:
    return f"{value:+.{digits}f}"


def _stat_chips(figure: dict) -> list[str]:
    stat = figure.get("stat") or {}
    chips: list[str] = []
    test = stat.get("test")
    if test:
        chips.append(TEST_LABELS.get(str(test), str(test).replace("_", " ")))

    if "rho" in stat:
        chips.append(f"ρ = {_signed(float(stat['rho']))}")
    if isinstance(stat.get("ci95"), list) and len(stat["ci95"]) == 2:
        low, high = stat["ci95"]
        chips.append(f"95% CI {_signed(float(low))} to {_signed(float(high))}")
    if "h" in stat:
        chips.append(f"H = {float(stat['h']):.1f}")
    if "chi2" in stat:
        chips.append(f"χ² = {float(stat['chi2']):.1f}")
    if "dof" in stat:
        chips.append(f"df = {int(stat['dof'])}")
    effect = stat.get("effect")
    if "effect_value" in stat and effect:
        value = float(stat["effect_value"])
        if effect == "epsilon_squared":
            chips.append(f"ε² = {value:.2f}")
        elif effect == "cramers_v":
            chips.append(f"V = {value:.2f}")
        elif effect == "cliffs_delta":
            chips.append(f"Cliff's δ = {_signed(value)}")
        else:
            chips.append(f"{effect} = {value:.2f}")
    if "observed" in stat:
        chips.append(f"mean difference = {float(stat['observed']):.2f}")
    if "p" in stat:
        chips.append(_p_text(float(stat["p"])))

    if "area_perimeter" in stat:
        chips.append(f"area↔perimeter ρ = {_signed(float(stat['area_perimeter']))}")
    if "shape_index_compactness" in stat:
        chips.append(f"shape↔compactness ρ = {_signed(float(stat['shape_index_compactness']))}")

    if "x_span_km" in stat and "y_span_km" in stat:
        chips.append(
            f"span {float(stat['x_span_km']):.0f} × {float(stat['y_span_km']):.0f} km"
        )
    if "median_field_width_km" in stat:
        chips.append(f"median field {float(stat['median_field_width_km']):.1f} km")
    if "pooled_corn_share" in stat:
        chips.append(f"corn share {float(stat['pooled_corn_share']):.0%}")
    if "rotating_fields" in stat and "continuous_corn_fields" in stat:
        chips.append(
            f"{int(stat['rotating_fields'])} rotating / "
            f"{int(stat['continuous_corn_fields'])} continuous"
        )
    if "median_days" in stat:
        chips.append(f"median {float(stat['median_days']):.0f} d")
    if isinstance(stat.get("range_days"), list) and len(stat["range_days"]) == 2:
        low, high = stat["range_days"]
        chips.append(f"range {int(low)}–{int(high)} d")
    if "crs" in stat:
        chips.append(str(stat["crs"]))
    if "consistent_negative_years" in stat:
        chips.append(
            f"negative in {int(stat['consistent_negative_years'])}"
            f"/{len(stat.get('per_year') or {})} years"
        )
    if "cells" in stat and str(stat.get("test")) != "descriptive":
        chips.append(f"{int(stat['cells'])} cells")
    return chips


def _figure_card(figure: dict) -> str:
    chips = _stat_chips(figure)
    chip_html = ""
    if chips:
        chip_html = (
            '<ul class="chips">'
            + "".join(f"<li>{_e(chip)}</li>" for chip in chips)
            + "</ul>"
        )
    return f"""
<figure class="card">
  <img src="{_e(figure['file'])}" alt="{_e(figure['title'])}" loading="lazy">
  <div class="body">
    <h3>{_e(figure['title'])}</h3>
    <p class="question">{_e(figure['question'])}</p>
    <p class="finding">{_e(figure['finding'])}</p>
    {chip_html}
    <p class="caveat"><b>Caveat.</b> {_e(figure['caveat'])}</p>
  </div>
</figure>"""


def _category_section(category: str, figures: list[dict]) -> str:
    story = CATEGORY_STORY.get(category, "")
    cards = "".join(_figure_card(figure) for figure in figures)
    anchor = category.replace(" ", "-")
    return f"""
<section id="{_e(anchor)}">
  <h2>{_e(category)} · {len(figures)} figure{"s" if len(figures) != 1 else ""}</h2>
  <p class="story">{_e(story)}</p>
  {cards}
</section>"""


def _cdl_window(dataset: dict) -> str:
    years = [
        year
        for values in (dataset.get("cdl_years") or {}).values()
        for year in values
    ]
    if not years:
        return "CDL unavailable"
    return f"CDL {min(years)}–{max(years)}"


def _hash_table(records: list[dict], out_dir: Path) -> str:
    rows = []
    for figure in records:
        path = out_dir / figure["file"]
        digest = (
            hashlib.sha256(path.read_bytes()).hexdigest()[:12]
            if path.is_file()
            else "missing"
        )
        rows.append(
            f"<tr><td>{_e(figure['id'])}</td>"
            f"<td>{_e(figure['file'])}</td><td>{digest}</td></tr>"
        )
    return (
        '<table class="hashes"><thead><tr><th>figure</th><th>file</th>'
        "<th>sha256</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )


def render_report(
    out_dir: Path,
    analysis: dict | None = None,
    validation: dict | None = None,
    html_path: Path | None = None,
) -> Path:
    """Write ``report.html`` from the contract already in ``out_dir``."""
    out_dir = Path(out_dir)
    if analysis is None:
        analysis = json.loads((out_dir / "analysis.json").read_text(encoding="utf-8"))
    if validation is None:
        manifest_path = out_dir / "manifest.json"
        if manifest_path.is_file():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            validation = manifest.get("validation") or {}
        else:
            validation = {}
    validation = validation or {}

    dataset = analysis.get("dataset") or {}
    records = analysis.get("figures") or []
    stage = analysis.get("stage") or "unknown"

    by_category: dict[str, list[dict]] = {}
    for figure in records:
        by_category.setdefault(str(figure.get("category")), []).append(figure)
    categories = [name for name in CATEGORY_ORDER if name in by_category]
    categories += [name for name in by_category if name not in CATEGORY_ORDER]

    facts = []
    for key, label in FACT_LABELS:
        value = dataset.get(key)
        if isinstance(value, list):
            value = len(value)
        if value is None:
            continue
        facts.append((f"{int(value):,}", label))
    facts.append((_cdl_window(dataset), "crop-history window"))
    fact_html = "".join(
        f'<div class="fact"><b>{_e(value)}</b><span>{_e(label)}</span></div>'
        for value, label in facts
    )

    sections = "".join(
        _category_section(name, by_category[name]) for name in categories
    )
    toc = "".join(
        f'<a href="#{_e(name.replace(" ", "-"))}">{_e(name)}</a>'
        for name in categories
    )

    units = analysis.get("unit_of_analysis") or {}
    unit_rows = "".join(
        f"<dt>{_e(name)}</dt><dd>{_e(text)}</dd>" for name, text in units.items()
    )

    partial = ""
    if stage != "all":
        partial = (
            f'<p class="note"><b>Partial report.</b> This run wrote the '
            f"<code>{_e(stage)}</code> stage only, so the other categories are "
            "absent by design. Run <code>--stage all</code> for the full set.</p>"
        )

    def _count(key: str) -> int:
        value = dataset.get(key, 0)
        return len(value) if isinstance(value, list) else int(value)

    lead = (
        f"{_count('fields')} fields across {_count('growers')} growers, "
        "compared on boundaries, crop history, and weather."
        if dataset
        else "Field-portfolio comparison."
    )

    validator = str(validation.get("status", "unknown"))
    detail = validation.get("detail")
    validator_text = f"Dataset validator: {validator}"
    if detail:
        validator_text += f" — {detail}"

    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Field portfolio report · My Farm Advisor</title>
<style>{STYLE}</style>
</head>
<body>
<div class="wrap">
<header>
  <p class="eyebrow">My Farm Advisor · eda-field-portfolio</p>
  <h1>Field portfolio report</h1>
  <p class="lede">{_e(lead)}</p>
  <code class="command">./scripts/run-eda-field-portfolio.sh --stage {_e(stage)}</code>
</header>

<div class="facts">{fact_html}</div>

<nav class="toc">{toc}</nav>

<p class="howto"><b>Reading this report.</b> Each card answers one question and
carries the finding, the test and effect size, the sample size, and the caveat
that limits it — read the caveat before quoting a number. Nothing here is
recomputed: every value is copied from the <code>analysis.json</code> the run
already wrote. Print the page to PDF from a browser for a paginated copy.</p>

{sections}

<section class="methods" id="methods">
  <h2>Conventions and units</h2>
  <dl>
    {unit_rows}
    <dt>GDD</dt><dd>Seasonal sum, base 10 °C, daily maximum capped at 30 °C, April–September.</dd>
    <dt>Frost</dt><dd>Tmin below 0 °C; last spring frost searched from March, first fall frost from August.</dd>
    <dt>Precipitation</dt><dd>April–September total, millimetres.</dd>
    <dt>Crop class</dt><dd>Dominant CDL class per field-year; minority edge classes never win.</dd>
  </dl>
</section>

<footer>
  <p>{_e(validator_text)}. {len(records)} figure records, {len(categories)} categories.
  Units and effect sizes are copied from <code>analysis.json</code>; this page does not
  recompute them.</p>
  {partial}
  <p>Reproduce: <code>./scripts/run-eda-field-portfolio.sh</code> from
  <code>data-pipeline/</code>. Outputs live under
  <code>&lt;runtime&gt;/derived/eda/field-portfolio/</code>
  (<code>figures/</code>, <code>tables/table_fields.csv</code>,
  <code>analysis.json</code>, <code>manifest.json</code>). Print this page to PDF
  from a browser if a paginated copy is needed.</p>
  {_hash_table(records, out_dir)}
</footer>
</div>
</body>
</html>
"""
    path = Path(html_path) if html_path else out_dir / "report.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page, encoding="utf-8")
    return path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render analysis.json as report.html")
    parser.add_argument("--out-dir", required=True, help="directory holding analysis.json")
    parser.add_argument("--html", default=None, help="output path (default: <out-dir>/report.html)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).expanduser().resolve()
    if not (out_dir / "analysis.json").is_file():
        print(f"error: no analysis.json in {out_dir}", file=sys.stderr)
        return 2
    path = render_report(
        out_dir, html_path=Path(args.html).expanduser() if args.html else None
    )
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
