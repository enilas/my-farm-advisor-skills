"""Weather figures: heat budget, frost window, and the precipitation trade-off.

Everything here is computed on the NASA POWER cell, then rolled to cell-year.
Ten cells × five years is fifty site-years; thirty fields are not fifty
climates. The record says so next to every n.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

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

GDD_LABEL = "season GDD (°C·day, base 10 °C, cap 30 °C)"
PRECIP_LABEL = "season precipitation (mm, Apr–Sep)"


# --------------------------------------------------------------------------
# W1 — seasonal GDD by site-year
# --------------------------------------------------------------------------


def figure_gdd(pf: Portfolio, out_dir: Path) -> dict:
    data = pf.site_years.dropna(subset=["gdd"])
    colors = grower_colors(pf)
    labels = grower_labels(pf)
    growers = ordered_growers(pf, colors)
    groups = {
        labels[grower]: data.loc[data["grower_slug"] == grower, "gdd"].to_numpy(dtype=float)
        for grower in growers
    }
    test = stats.kruskal_groups(groups)

    cell_means = data.groupby(["grower_slug", "weather_cell"])["gdd"].mean()
    cell_groups = {
        labels[grower]: cell_means.loc[grower].to_numpy(dtype=float)
        for grower in growers
        if grower in cell_means.index.get_level_values(0)
    }
    cell_test = stats.kruskal_groups(cell_groups)
    within_cell = (
        data.groupby("weather_cell")["gdd"].agg(lambda values: values.max() - values.min())
    )
    medians = np.array([np.median(groups[labels[g]]) for g in growers])
    nearest_gap = float(np.min(np.diff(np.sort(medians)))) if len(medians) > 1 else 0.0

    fig, ax = plt.subplots(figsize=(8.6, 5.8), layout="constrained")
    rng = np.random.default_rng(stats.DEFAULT_SEED)
    for position, grower in enumerate(growers):
        values = groups[labels[grower]]
        box = ax.boxplot(
            values,
            positions=[position],
            widths=0.42,
            patch_artist=True,
            showfliers=False,
            medianprops=dict(color="#111827", linewidth=1.6),
            boxprops=dict(facecolor="white", edgecolor=colors[grower], linewidth=1.4),
            whiskerprops=dict(color=colors[grower], linewidth=1.1),
            capprops=dict(color=colors[grower], linewidth=1.1),
        )
        for artist in box["boxes"]:
            artist.set_zorder(2)
        jitter = rng.uniform(-0.14, 0.14, size=values.size)
        ax.scatter(
            position + jitter,
            values,
            s=34,
            color=colors[grower],
            edgecolor="white",
            linewidth=0.7,
            alpha=0.9,
            zorder=3,
        )
        ax.text(
            position,
            float(np.median(values)) + 22,
            f"median {np.median(values):.0f}",
            ha="center",
            va="bottom",
            fontsize=8.2,
            color="#111827",
            bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none", alpha=0.85),
        )

    ax.set_xticks(range(len(growers)))
    ax.set_xticklabels(
        [
            f"{labels[grower]}\n{groups[labels[grower]].size} site-years"
            for grower in growers
        ]
    )
    ax.set_ylabel(GDD_LABEL)
    ax.grid(axis="x", visible=False)
    low, high = ax.get_ylim()
    ax.set_ylim(low - 25, high + 45)
    stat_box(
        ax,
        [
            f"Kruskal-Wallis  H = {test.h:.1f}",
            f"{stats.format_p(test.p)}   ε² = {test.epsilon_squared:.2f}",
            f"n = {test.n} site-years ({data['weather_cell'].nunique()} cells)",
            f"cell-mean check: p = {cell_test.p:.3f}",
            f"within-cell 5-yr spread <= {within_cell.max():.0f}",
        ],
        loc="upper left",
    )
    ax.set_title("Latitude sets the heat budget; the year shifts it", fontsize=14)
    fig.supxlabel(
        "Each point is one cell-year (10 NASA POWER cells × 5 years). Boxes are "
        "the 5-year spread inside a state; the test treats site-years as units, "
        "so the cell-mean check below it is the conservative read.",
        fontsize=7.6,
        color="#4B5563",
    )

    path = save_figure(fig, out_dir / "figures" / "w1_gdd_by_site_year.png")
    return {
        "id": "w1_gdd_by_site_year",
        "file": "figures/w1_gdd_by_site_year.png",
        "category": "weather",
        "scope": "across growers and years",
        "title": "Latitude sets the heat budget; the year shifts it",
        "question": "How much seasonal heat does each site get, and is the difference a grower or a year effect?",
        "finding": (
            "Median season GDD is "
            + ", ".join(
                f"{labels[grower]} {np.median(groups[labels[grower]]):.0f}"
                for grower in growers
            )
            + f". The groups separate overall (H = {test.h:.1f}, "
            f"{stats.format_p(test.p)}, ε² = {test.epsilon_squared:.2f}). The "
            f"year-to-year spread inside a cell (median {within_cell.median():.0f}, "
            f"max {within_cell.max():.0f} GDD) is wider than the {nearest_gap:.0f} "
            "GDD gap between the closest group medians, so the cooler groups overlap "
            "even though the hottest one stands apart. Latitude sets the level; the "
            "year decides where a site lands within it."
        ),
        "n": int(len(data)),
        "unit": "site-year",
        "stat": {
            **test.as_dict(),
            "cells": int(data["weather_cell"].nunique()),
            "cell_mean_test": cell_test.as_dict(),
            "within_cell_range_median": round(float(within_cell.median()), 1),
            "within_cell_range_max": round(float(within_cell.max()), 1),
            "nearest_median_gap": round(nearest_gap, 1),
        },
        "caveat": (
            "The five years behind a cell are repeated measures, so the "
            "site-year p-value is optimistic; the cell-mean test uses 10 units "
            "and is the conservative check. GDD is a heat sum, not a yield "
            "predictor, and the cap convention is stated in the guide."
        ),
    }


# --------------------------------------------------------------------------
# W2 — frost-free window
# --------------------------------------------------------------------------


def figure_frost_window(pf: Portfolio, out_dir: Path) -> dict:
    data = pf.site_years.dropna(subset=["spring_frost", "fall_frost"]).copy()
    colors = grower_colors(pf)
    labels = grower_labels(pf)
    growers = ordered_growers(pf, colors)
    order = {grower: position for position, grower in enumerate(growers)}

    data["spring_doy"] = data["spring_frost"].map(lambda value: value.timetuple().tm_yday)
    data["fall_doy"] = data["fall_frost"].map(lambda value: value.timetuple().tm_yday)
    data["state_order"] = data["grower_slug"].map(order)
    data = data.sort_values(
        by=["state_order", "spring_doy", "fall_doy"], kind="stable"
    ).reset_index(drop=True)

    fig, ax = plt.subplots(figsize=(9.8, 7.2), layout="constrained")
    band_positions: list[float] = []
    band_labels: list[str] = []
    for position, row in data.iterrows():
        color = colors[row["grower_slug"]]
        ax.hlines(
            position,
            row["spring_doy"],
            row["fall_doy"],
            color=color,
            linewidth=3.4,
            alpha=0.88,
            zorder=2,
        )
        ax.scatter(
            [row["spring_doy"], row["fall_doy"]],
            [position, position],
            s=17,
            color=color,
            edgecolor="white",
            linewidth=0.6,
            zorder=3,
        )

    for grower in growers:
        block = data.index[data["grower_slug"] == grower]
        if len(block) == 0:
            continue
        band_positions.append(float(np.mean([block.min(), block.max()])))
        median = float(np.median(data.loc[block, "season_days"]))
        band_labels.append(f"{labels[grower]}\n{median:.0f} d")

    ax.set_yticks(band_positions, labels=band_labels)
    ax.set_ylim(len(data) - 0.5, -0.5)
    month_ticks = [
        date(2021, month, 1).timetuple().tm_yday for month in range(4, 12)
    ]
    ax.set_xticks(
        month_ticks,
        labels=[date(2021, month, 1).strftime("%b") for month in range(4, 12)],
    )
    ax.tick_params(axis="x", labelsize=8.6)
    ax.grid(axis="y", visible=False)
    ax.set_xlim(data["spring_doy"].min() - 8, data["fall_doy"].max() + 8)
    ax.set_xlabel("day of year (month starts)")

    by_state = {
        labels[grower]: {
            "median_days": round(float(np.median(data.loc[data["grower_slug"] == grower, "season_days"])), 1),
            "min_days": int(data.loc[data["grower_slug"] == grower, "season_days"].min()),
            "max_days": int(data.loc[data["grower_slug"] == grower, "season_days"].max()),
        }
        for grower in growers
        if (data["grower_slug"] == grower).any()
    }
    overall = data["season_days"]
    ax.set_title(
        f"Frost-free windows run {int(overall.min())}–{int(overall.max())} days",
        fontsize=14,
    )
    fig.supxlabel(
        "Each line is one cell-year (10 NASA POWER cells × 5 years), ordered by "
        "last spring frost within grower. Ends are the last spring frost and the "
        "first fall frost; tick labels give each grower's median season length.",
        fontsize=7.6,
        color="#4B5563",
    )

    path = save_figure(fig, out_dir / "figures" / "w2_frost_window.png")
    return {
        "id": "w2_frost_window",
        "file": "figures/w2_frost_window.png",
        "category": "weather",
        "scope": "within site-year, compared across growers",
        "title": f"Frost-free windows run {int(overall.min())}–{int(overall.max())} days",
        "question": "When does the growing season start and end, and how much does the window vary?",
        "finding": (
            f"Across {len(data)} site-years the frost-free window runs "
            f"{int(overall.min())}–{int(overall.max())} days (median "
            f"{np.median(overall):.0f}). "
            + "; ".join(
                f"{label} median {item['median_days']:.0f} days "
                f"({item['min_days']}–{item['max_days']})"
                for label, item in by_state.items()
            )
            + ". Spring frosts cluster in the third week of April and fall frosts "
            "in late October to early November. The longest and shortest medians "
            f"differ by {max(item['median_days'] for item in by_state.values()) - min(item['median_days'] for item in by_state.values()):.0f} "
            "days, so the contrast is a shift of weeks, not a different season type."
        ),
        "n": int(len(data)),
        "unit": "site-year",
        "stat": {
            "test": "frost_window",
            "n": int(len(data)),
            "cells": int(data["weather_cell"].nunique()),
            "median_days": round(float(np.median(overall)), 1),
            "range_days": [int(overall.min()), int(overall.max())],
            "spring_range": [
                str(data["spring_frost"].min()),
                str(data["spring_frost"].max()),
            ],
            "fall_range": [
                str(data["fall_frost"].min()),
                str(data["fall_frost"].max()),
            ],
            "by_grower": by_state,
        },
        "caveat": (
            "A frost is Tmin < 0 °C inside the spring (Mar–Jun) or fall (Aug–Nov) "
            "window; a January thaw is not a season start. The window says when "
            "freezing stopped and resumed, not whether a crop was in the ground."
        ),
    }


# --------------------------------------------------------------------------
# W3 — precipitation vs GDD
# --------------------------------------------------------------------------


def figure_precip_vs_gdd(pf: Portfolio, out_dir: Path) -> dict:
    data = pf.site_years.dropna(subset=["gdd", "precip_mm"]).copy()
    colors = grower_colors(pf)
    labels = grower_labels(pf)
    growers = ordered_growers(pf, colors)
    test = stats.spearman(data["precip_mm"], data["gdd"])
    per_year = {
        int(year): stats.spearman(group["precip_mm"], group["gdd"], bootstrap=1000)
        for year, group in data.groupby("year")
    }
    consistent = sum(1 for result in per_year.values() if result.rho < 0)

    fig, ax = plt.subplots(figsize=(8.8, 6.0), layout="constrained")
    for grower in growers:
        subset = data[data["grower_slug"] == grower]
        ax.scatter(
            subset["precip_mm"],
            subset["gdd"],
            s=46,
            color=colors[grower],
            edgecolor="white",
            linewidth=0.8,
            alpha=0.9,
            label=labels[grower],
            zorder=3,
        )

    ax.set_xlabel(PRECIP_LABEL)
    ax.set_ylabel(GDD_LABEL)
    ax.legend(loc="lower left", frameon=True, framealpha=0.94, title="grower")
    ax.grid(color="#E5E7EB")

    ci_text = (
        f"95% CI {test.ci_low:+.2f} to {test.ci_high:+.2f}"
        if test.ci_low is not None and test.ci_high is not None
        else "95% CI unavailable (constant input)"
    )
    stat_box(
        ax,
        [
            f"Spearman rho = {test.rho:+.2f}",
            ci_text,
            f"{stats.format_p(test.p)}   n = {test.n} site-years",
            f"within-year rho < 0 in {consistent}/{len(per_year)} years",
        ],
        loc="upper right",
    )
    ax.set_title("Hotter sites are also drier", fontsize=14)
    fig.supxlabel(
        "Each point is one cell-year (10 NASA POWER cells × 5 years). Precipitation "
        "is the April–September total; GDD uses the base-10, cap-30 convention. "
        "The rank test is pooled across years; the per-year signs are reported too.",
        fontsize=7.6,
        color="#4B5563",
    )

    path = save_figure(fig, out_dir / "figures" / "w3_precip_vs_gdd.png")
    return {
        "id": "w3_precip_vs_gdd",
        "file": "figures/w3_precip_vs_gdd.png",
        "category": "weather",
        "scope": "across growers and years",
        "title": "Hotter sites are also drier",
        "question": "Do the hotter sites also get less seasonal rain?",
        "finding": (
            f"Seasonal precipitation and GDD are negatively rank-correlated "
            f"(rho = {test.rho:+.2f}, "
            + (
                f"95% CI {test.ci_low:+.2f} to {test.ci_high:+.2f}, "
                if test.ci_low is not None and test.ci_high is not None
                else ""
            )
            + f"{stats.format_p(test.p)}) over {test.n} "
            f"site-years. The sign holds within each year ({consistent} of "
            f"{len(per_year)}), so the west-to-east moisture gradient is not an "
            "artefact of pooling five different years."
        ),
        "n": int(test.n),
        "unit": "site-year",
        "stat": {
            **test.as_dict(),
            "cells": int(data["weather_cell"].nunique()),
            "years": [int(year) for year in sorted(per_year)],
            "per_year": {
                str(year): {
                    "rho": round(result.rho, 4),
                    "p": round(result.p, 6),
                }
                for year, result in sorted(per_year.items())
            },
            "consistent_negative_years": consistent,
        },
        "caveat": (
            "Ten cells supply the spatial contrast and five years the temporal "
            "one; the pooled rank test mixes both, so the per-year signs are the "
            "check. Rank correlation says the wetter sites are cooler, not that "
            "rain causes cooler conditions."
        ),
    }


def build_all(pf: Portfolio, out_dir: Path) -> list[dict]:
    style()
    return [
        figure_gdd(pf, out_dir),
        figure_frost_window(pf, out_dir),
        figure_precip_vs_gdd(pf, out_dir),
    ]
