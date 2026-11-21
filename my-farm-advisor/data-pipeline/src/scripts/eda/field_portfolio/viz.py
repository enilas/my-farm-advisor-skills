"""Shared plotting conventions for the field-portfolio figure modules.

Colour, typography and the stat box are defined once so a grower keeps the
same colour and a number keeps the same look in every stage and in the report.
Figure-specific layout stays in ``figures_*``.
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt

from lib.palettes import categorical

# Stable colour per state so the same grower is the same colour in every
# figure and in the later report. Unknown states fall back to Okabe-Ito.
STATE_COLORS = {
    "IL": "#0072B2",  # blue
    "IA": "#E69F00",  # orange
    "NE": "#009E73",  # green
    "MN": "#CC79A7",
    "IN": "#56B4E9",
    "MO": "#D55E0E",
    "KS": "#F0E442",
}
GROWER_ORDER_HINT = ("IL", "IA", "NE")


def style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "axes.edgecolor": "#4B5563",
            "axes.labelcolor": "#111827",
            "text.color": "#111827",
            "xtick.color": "#374151",
            "ytick.color": "#374151",
            "axes.titlesize": 13,
            "axes.titleweight": "bold",
            "axes.labelsize": 10,
            "font.size": 10,
            "legend.fontsize": 9,
            "axes.grid": True,
            "grid.color": "#E5E7EB",
            "grid.linewidth": 0.7,
            "figure.dpi": 150,
            "savefig.dpi": 150,
            "savefig.bbox": "tight",
        }
    )


def grower_labels(pf) -> dict[str, str]:
    """Display label per grower: the state code when the tree is consistent."""
    labels: dict[str, str] = {}
    for grower in pf.growers:
        states = [
            str(value)
            for value in pf.fields_table.loc[
                pf.fields_table["grower_slug"] == grower, "state"
            ].dropna()
            if str(value).strip()
        ]
        labels[grower] = states[0] if states else grower
    return labels


def grower_colors(pf) -> dict[str, str]:
    labels = grower_labels(pf)
    ordered = sorted(
        labels,
        key=lambda g: (
            GROWER_ORDER_HINT.index(labels[g]) if labels[g] in GROWER_ORDER_HINT else 99,
            labels[g],
        ),
    )
    fallback = categorical(max(len(ordered), 1))
    colors: dict[str, str] = {}
    spare = iter(fallback)
    for grower in ordered:
        colors[grower] = STATE_COLORS.get(labels[grower]) or next(spare)
    return colors


def ordered_growers(pf, colors: dict[str, str]) -> list[str]:
    labels = grower_labels(pf)
    return sorted(
        pf.growers,
        key=lambda g: (
            GROWER_ORDER_HINT.index(labels[g]) if labels[g] in GROWER_ORDER_HINT else 99,
            labels[g],
        ),
    )


def save_figure(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return path


def stat_box(ax, lines: list[str], loc: str = "upper left") -> None:
    ax.text(
        0.015 if "left" in loc else 0.985,
        0.975 if "upper" in loc else 0.025,
        "\n".join(lines),
        transform=ax.transAxes,
        ha="left" if "left" in loc else "right",
        va="top" if "upper" in loc else "bottom",
        fontsize=8.4,
        family="DejaVu Sans Mono",
        linespacing=1.45,
        bbox=dict(boxstyle="round,pad=0.45", fc="white", ec="#D1D5DB", lw=0.9, alpha=0.95),
        zorder=6,
    )
