"""Crop-history figures: mix, within-field sequence, and transitions.

The unit is the field-year, never the field-day. The three views answer
different questions on the same 5-year grid: how the portfolio's crop mix is
distributed, what each field actually did year by year, and whether the next
crop depends on the current one.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.colors import ListedColormap
from matplotlib.patches import Patch

from . import stats
from .dataset import Portfolio
from .viz import (
    grower_colors,
    grower_labels,
    ordered_growers,
    save_figure,
    stat_box,
    style,
)

CROP_ORDER = ("Corn", "Soybeans", "Other")
CROP_COLORS = {
    "Corn": "#E0A23C",
    "Soybeans": "#6F9B62",
    "Other": "#9CA3AF",
}
CROP_TEXT = {"Corn": "white", "Soybeans": "white", "Other": "#111827"}


def _field_wide(pf: Portfolio) -> pd.DataFrame:
    """field_id × year crop matrix, with grower kept alongside."""
    wide = pf.sequence.pivot(index="field_id", columns="year", values="crop_name")
    return wide.reindex(sorted(wide.columns), axis=1)


def _patterns(wide: pd.DataFrame) -> pd.Series:
    return wide.apply(lambda row: " → ".join(str(v) for v in row.tolist()), axis=1)


def _ordered_fields(wide: pd.DataFrame, patterns: pd.Series) -> list[str]:
    # Rotating fields first (the rule), continuous fields last (the exception).
    return sorted(
        wide.index,
        key=lambda field: (
            -int(wide.loc[field].nunique()),
            patterns.loc[field],
            field,
        ),
    )


def _transition_counts(pf: Portfolio) -> pd.DataFrame:
    records: list[dict[str, str]] = []
    for field, group in pf.sequence.groupby("field_id", sort=True):
        crops = group.sort_values("year")["crop_name"].tolist()
        for current, following in zip(crops, crops[1:]):
            records.append(
                {"field_id": field, "from": current, "to": following}
            )
    pairs = pd.DataFrame.from_records(records)
    return pd.crosstab(pairs["from"], pairs["to"]).reindex(
        index=list(CROP_ORDER), columns=list(CROP_ORDER), fill_value=0
    )


# --------------------------------------------------------------------------
# C1 — crop mix by grower and year
# --------------------------------------------------------------------------


def figure_crop_mix(pf: Portfolio, out_dir: Path) -> dict:
    labels = grower_labels(pf)
    growers = ordered_growers(pf, grower_colors(pf))
    years = sorted(pf.sequence["year"].unique())
    present = [crop for crop in CROP_ORDER if crop in set(pf.sequence["crop_group"])]

    fig, axes = plt.subplots(
        1,
        len(growers),
        figsize=(4.3 * len(growers), 4.9),
        sharey=True,
        layout="constrained",
    )
    if len(growers) == 1:
        axes = np.array([axes])

    per_grower: dict[str, dict] = {}
    for panel_index, (ax, grower) in enumerate(zip(axes, growers)):
        subset = pf.sequence[pf.sequence["grower_slug"] == grower]
        counts = (
            subset.pivot_table(
                index="year",
                columns="crop_group",
                values="field_id",
                aggfunc="count",
                fill_value=0,
            )
            .reindex(columns=present, fill_value=0)
            .reindex(years, fill_value=0)
        )
        totals = counts.sum(axis=1).to_numpy(dtype=float)
        bottom = np.zeros(len(years), dtype=float)

        for crop in present:
            values = counts[crop].to_numpy(dtype=float)
            shares = np.divide(values, totals, out=np.zeros_like(values), where=totals > 0) * 100
            ax.bar(
                years,
                shares,
                bottom=bottom,
                width=0.72,
                color=CROP_COLORS[crop],
                edgecolor="white",
                linewidth=1.4,
                label=crop,
            )
            for year, share, value, base in zip(years, shares, values, bottom):
                if value <= 0:
                    continue
                ax.text(
                    year,
                    base + share / 2,
                    f"{int(value)}",
                    ha="center",
                    va="center",
                    fontsize=8.6,
                    fontweight="bold",
                    color=CROP_TEXT[crop],
                )
            bottom += shares

        corn = int(counts["Corn"].sum()) if "Corn" in counts else 0
        total = int(totals.sum())
        ax.set_xticks(years)
        ax.set_ylim(0, 118)
        if panel_index == 0:
            ax.set_yticks([0, 25, 50, 75, 100])
            ax.set_ylabel("share of field-years (%)")
            ax.legend(
                handles=[
                    Patch(facecolor=CROP_COLORS[crop], edgecolor="none", label=crop)
                    for crop in present
                ],
                loc="upper left",
                ncol=len(present),
                frameon=False,
                fontsize=8.6,
                handlelength=1.1,
                columnspacing=1.0,
                borderaxespad=0.2,
            )
        ax.set_title(
            f"{labels[grower]} — corn {corn}/{total} field-years",
            fontsize=10.4,
        )
        ax.grid(axis="x", visible=False)
        per_grower[labels[grower]] = {
            "corn": corn,
            "soybeans": int(counts["Soybeans"].sum()) if "Soybeans" in counts else 0,
            "field_years": total,
            "corn_share": round(corn / total, 4) if total else None,
            "by_year": {
                int(year): {
                    "corn": int(counts.loc[year, "Corn"]) if "Corn" in counts else 0,
                    "field_years": int(totals[index]),
                }
                for index, year in enumerate(years)
            },
        }

    fig.suptitle("Corn and soy split the portfolio; Nebraska leans corn", fontsize=14)
    fig.supxlabel(
        "Each bar is 10 field-years (10 fields × 1 year); numbers are field counts. "
        "The same fields repeat across years, so this is a composition, not a trend test.",
        fontsize=7.6,
        color="#4B5563",
    )

    path = save_figure(fig, out_dir / "figures" / "c1_crop_mix_by_year.png")
    pooled_corn = sum(item["corn"] for item in per_grower.values())
    pooled_total = sum(item["field_years"] for item in per_grower.values())
    return {
        "id": "c1_crop_mix_by_year",
        "file": "figures/c1_crop_mix_by_year.png",
        "category": "crop history",
        "scope": "across growers and years",
        "title": "Corn and soy split the portfolio; Nebraska leans corn",
        "question": "How is the planted crop mix distributed across growers and years?",
        "finding": (
            f"Corn covers {pooled_corn} of {pooled_total} field-years "
            f"({pooled_corn / pooled_total:.0%}). "
            + "; ".join(
                f"{label} {item['corn']}/{item['field_years']}"
                for label, item in per_grower.items()
            )
            + ". Soybeans are the counterweight everywhere except Nebraska, and no "
            "year departs from the same two-crop split."
        ),
        "n": int(pooled_total),
        "unit": "field-year",
        "stat": {
            "test": "composition",
            "n": int(pooled_total),
            "years": [int(year) for year in years],
            "pooled_corn_share": round(pooled_corn / pooled_total, 4),
            "by_grower": per_grower,
        },
        "caveat": (
            "Field-years are not independent: the same 10 fields appear in all "
            "five years, so the bar is a composition, not a trend. The dominant "
            "class is used; minority edge classes (grass, forest, wheat) never win."
        ),
    }


# --------------------------------------------------------------------------
# C2 — within-field sequence
# --------------------------------------------------------------------------


def figure_crop_sequence(pf: Portfolio, out_dir: Path) -> dict:
    labels = grower_labels(pf)
    growers = ordered_growers(pf, grower_colors(pf))
    years = sorted(pf.sequence["year"].unique())
    wide = _field_wide(pf)
    patterns = _patterns(wide)
    present = [crop for crop in CROP_ORDER if crop in set(pf.sequence["crop_group"])]
    color_map = ListedColormap([CROP_COLORS[crop] for crop in present])
    index_of = {crop: position for position, crop in enumerate(present)}

    fig, axes = plt.subplots(
        1,
        len(growers),
        figsize=(3.7 * len(growers), 5.4),
        sharex=True,
        layout="constrained",
    )
    if len(growers) == 1:
        axes = np.array([axes])

    per_grower: dict[str, dict] = {}
    all_patterns: dict[str, int] = {}
    for panel_index, (ax, grower) in enumerate(zip(axes, growers)):
        subset = pf.sequence[pf.sequence["grower_slug"] == grower]
        fields = _ordered_fields(wide.loc[subset["field_id"].unique()], patterns)
        matrix = np.array(
            [
                [index_of[wide.loc[field, year]] for year in years]
                for field in fields
            ],
            dtype=float,
        )
        ax.imshow(
            matrix,
            aspect="auto",
            cmap=color_map,
            vmin=-0.5,
            vmax=len(present) - 0.5,
            interpolation="nearest",
        )

        ax.set_xticks(range(len(years)), labels=[str(year) for year in years])
        ax.set_yticks(range(len(fields)), labels=fields, fontsize=8.2)
        ax.set_xticks(np.arange(-0.5, len(years), 1), minor=True)
        ax.set_yticks(np.arange(-0.5, len(fields), 1), minor=True)
        ax.grid(which="minor", color="white", linewidth=1.4)
        ax.grid(which="major", visible=False)
        ax.tick_params(which="minor", length=0)
        ax.tick_params(axis="x", labelsize=8.4)
        for spine in ax.spines.values():
            spine.set_visible(False)

        # A thicker rule separates sequence groups; the eye should see blocks.
        for row in range(1, len(fields)):
            if patterns.loc[fields[row]] != patterns.loc[fields[row - 1]]:
                ax.axhline(row - 0.5, color="white", linewidth=2.6, zorder=3)

        block = patterns.loc[fields]
        n_rotating = int((wide.loc[fields].nunique(axis=1) > 1).sum())
        n_continuous = len(fields) - n_rotating
        if n_continuous:
            panel_title = (
                f"{labels[grower]} — {n_rotating} rotating, "
                f"{n_continuous} continuous corn"
            )
        else:
            panel_title = f"{labels[grower]} — all {n_rotating} rotate"
        ax.set_title(panel_title, fontsize=9.8)
        for pattern, count in block.value_counts().items():
            all_patterns[pattern] = all_patterns.get(pattern, 0) + int(count)
        per_grower[labels[grower]] = {
            "rotating_fields": n_rotating,
            "continuous_corn_fields": int(
                (wide.loc[fields].nunique(axis=1) == 1).sum()
            ),
            "patterns": {pattern: int(count) for pattern, count in block.value_counts().items()},
        }

    fig.suptitle("Almost every field rotates; four stay in corn", fontsize=14)
    fig.supxlabel(
        "Amber is corn, green is soybeans. Rows are fields, ordered by their "
        "5-year sequence; columns are years. White rules separate distinct "
        "sequences, and a row that never changes color is a monocrop.",
        fontsize=7.6,
        color="#4B5563",
    )

    path = save_figure(fig, out_dir / "figures" / "c2_crop_sequence.png")
    rotating = int(
        sum(item["rotating_fields"] for item in per_grower.values())
    )
    total = int(len(wide))
    return {
        "id": "c2_crop_sequence",
        "file": "figures/c2_crop_sequence.png",
        "category": "crop history",
        "scope": "within field",
        "title": "Almost every field rotates; four stay in corn",
        "question": "What crop sequence did each field actually follow from 2019 to 2023?",
        "finding": (
            f"{rotating} of {total} fields change crop at least once; "
            f"{total - rotating} stay in continuous corn and none stay in soybeans. "
            f"{len(all_patterns)} distinct 5-year sequences appear. The repeating "
            "Corn–Soybeans beat is the norm, with corn–corn runs concentrated in Nebraska."
        ),
        "n": total,
        "unit": "field",
        "stat": {
            "test": "sequence",
            "n": total,
            "years": [int(year) for year in years],
            "rotating_fields": rotating,
            "continuous_corn_fields": total - rotating,
            "patterns": dict(sorted(all_patterns.items())),
            "by_grower": per_grower,
        },
        "caveat": (
            "Five years and thirty fields: the grid shows what happened, not what "
            "is agronomically intended. A one-year switch back to the same crop "
            "still counts as rotation here; read the rows, not a summary label."
        ),
    }


# --------------------------------------------------------------------------
# C3 — transition matrix and grower comparison
# --------------------------------------------------------------------------


def figure_crop_transitions(pf: Portfolio, out_dir: Path) -> dict:
    labels = grower_labels(pf)
    colors = grower_colors(pf)
    growers = ordered_growers(pf, colors)
    matrix = _transition_counts(pf)
    crops = [crop for crop in CROP_ORDER if matrix.loc[crop].sum() > 0]
    table = matrix.loc[crops, crops].to_numpy(dtype=int)
    assoc = stats.chi_square(table)

    row_totals = table.sum(axis=1, keepdims=True)
    row_share = np.divide(
        table,
        row_totals,
        out=np.zeros(table.shape, dtype=float),
        where=row_totals > 0,
    )

    fig, (ax, ax_bar) = plt.subplots(
        1,
        2,
        figsize=(11.0, 4.9),
        gridspec_kw={"width_ratios": [1.05, 1.0]},
        layout="constrained",
    )
    ax.imshow(row_share, cmap="Blues", vmin=0, vmax=1.0, aspect="auto")
    for row in range(len(crops)):
        for column in range(len(crops)):
            count = int(table[row, column])
            share = row_share[row, column]
            ax.text(
                column,
                row,
                f"{count}\n{share:.0%}",
                ha="center",
                va="center",
                fontsize=13,
                fontweight="bold",
                color="white" if share > 0.55 else "#111827",
            )
    ax.set_xticks(range(len(crops)), labels=crops)
    ax.set_yticks(range(len(crops)), labels=crops)
    ax.set_xlabel("crop in year t + 1")
    ax.set_ylabel("crop in year t")
    ax.grid(False)
    for spine in ax.spines.values():
        spine.set_visible(False)
    # Reserve a strip below the matrix for the test, so it never covers a cell.
    ax.set_ylim(len(crops) - 0.5 + 0.75, -0.5)
    ax.text(
        0.0,
        len(crops) - 0.08,
        "\n".join(
            [
                f"chi2 = {assoc.chi2:.1f}   df = {assoc.dof}",
            f"p {stats.format_p(assoc.p)[2:]}   "
            f"Cramer's V = {assoc.cramers_v:.2f}",
                f"n = {assoc.n} transitions   min expected = {assoc.min_expected:.1f}",
            ]
        ),
        ha="left",
        va="top",
        fontsize=8.3,
        family="DejaVu Sans Mono",
        linespacing=1.5,
        bbox=dict(boxstyle="round,pad=0.4", fc="#F9FAFB", ec="#D1D5DB", lw=0.8),
    )
    ax.set_title("Soybeans never follow soybeans", fontsize=11.6, pad=8)

    switch_rows: list[dict] = []
    for grower in growers:
        subset = pf.sequence[pf.sequence["grower_slug"] == grower]
        pairs = 0
        switches = 0
        for field, group in subset.groupby("field_id", sort=True):
            series = group.sort_values("year")["crop_name"].tolist()
            for current, following in zip(series, series[1:]):
                pairs += 1
                switches += int(current != following)
        switch_rows.append(
            {
                "label": labels[grower],
                "grower": grower,
                "switches": switches,
                "transitions": pairs,
                "rate": switches / pairs if pairs else 0.0,
            }
        )

    positions = np.arange(len(switch_rows))
    rates = [row["rate"] * 100 for row in switch_rows]
    ax_bar.barh(
        positions,
        rates,
        color=[colors[row["grower"]] for row in switch_rows],
        edgecolor="white",
        linewidth=1.0,
        height=0.58,
    )
    for position, row in zip(positions, switch_rows):
        ax_bar.text(
            row["rate"] * 100 + 1.5,
            position,
            f"{row['rate']:.0%}  ({row['switches']}/{row['transitions']})",
            va="center",
            fontsize=8.8,
        )
    pooled = float(np.mean([row["rate"] for row in switch_rows]))
    ax_bar.axvline(
        pooled * 100,
        color="#6B7280",
        linestyle="--",
        linewidth=1.1,
        label=f"pooled {pooled:.0%}",
    )
    ax_bar.set_yticks(positions, labels=[row["label"] for row in switch_rows])
    ax_bar.invert_yaxis()
    ax_bar.set_xlim(0, 108)
    ax_bar.set_xlabel("year-to-year transitions that change crop (%)")
    ax_bar.grid(axis="y", visible=False)
    ax_bar.legend(loc="lower right", frameon=True, fontsize=8.2)
    ax_bar.set_title("Nebraska keeps more corn in place", fontsize=11.6, pad=8)

    fig.supxlabel(
        "Each transition is a consecutive-year pair inside one field: 30 fields × 4 pairs = 120, "
        "not 120 independent fields. Counts and row shares use the dominant CDL class; "
        "the two panels test the pooled table and display the grower split.",
        fontsize=7.5,
        color="#4B5563",
    )

    path = save_figure(fig, out_dir / "figures" / "c3_crop_transitions.png")
    same = int(np.trace(table))
    switch_share = 1.0 - same / assoc.n
    conditional = {
        f"P({to_}|{from_})": round(row_share[i, j], 4)
        for i, from_ in enumerate(crops)
        for j, to_ in enumerate(crops)
    }
    return {
        "id": "c3_crop_transitions",
        "file": "figures/c3_crop_transitions.png",
        "category": "crop history",
        "scope": "within field, compared across growers",
        "title": "Soybeans never follow soybeans",
        "question": "Does the next crop depend on the current crop, and does every grower rotate alike?",
        "finding": (
            f"{switch_share:.1%} of {assoc.n} year-to-year transitions change crop "
            f"({assoc.n - same} of {assoc.n}). Soybeans are followed by corn in "
            f"{conditional.get('P(Corn|Soybeans)', 0):.0%} of cases, while corn is "
            f"followed by corn {conditional.get('P(Corn|Corn)', 0):.0%} of the time. "
            f"Switch rates run "
            + ", ".join(
                f"{row['label']} {row['rate']:.0%} ({row['switches']}/{row['transitions']})"
                for row in switch_rows
            )
            + "; same-crop transitions are "
            + ", ".join(
                f"{row['label']} {row['transitions'] - row['switches']}"
                for row in switch_rows
            )
            + " (all corn), concentrated in Nebraska."
        ),
        "n": assoc.n,
        "unit": "field transition",
        "stat": {
            **assoc.as_dict(),
            "rows": crops,
            "columns": crops,
            "table": {
                f"{crops[i]}->{crops[j]}": int(table[i, j])
                for i in range(len(crops))
                for j in range(len(crops))
            },
            "row_shares": {
                f"{crops[i]}->{crops[j]}": round(float(row_share[i, j]), 4)
                for i in range(len(crops))
                for j in range(len(crops))
            },
            "switch_rate": round(switch_share, 4),
            "by_grower": {
                row["label"]: {
                    "switches": row["switches"],
                    "transitions": row["transitions"],
                    "rate": round(row["rate"], 4),
                }
                for row in switch_rows
            },
        },
        "caveat": (
            "The 120 transitions overlap: four consecutive pairs share each field, "
            "so the chi-square p-value is anti-conservative. The zero soybean→soybean "
            "cell and the grower split are the robust facts; treat the p as a "
            "description of this window, not a population test."
        ),
    }


def build_all(pf: Portfolio, out_dir: Path) -> list[dict]:
    style()
    return [
        figure_crop_mix(pf, out_dir),
        figure_crop_sequence(pf, out_dir),
        figure_crop_transitions(pf, out_dir),
    ]
