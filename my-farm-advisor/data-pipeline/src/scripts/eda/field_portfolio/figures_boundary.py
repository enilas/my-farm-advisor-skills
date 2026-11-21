"""Boundary-layer figures: two statistical views, one correlation, two maps.

Each figure answers one question and writes one analysis record. The record is
the contract with the later report: it carries the number, the test, the
effect size, and the caveat, so no prose has to be re-derived downstream.
"""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import geopandas as gpd
import matplotlib.patches as mpatches
import matplotlib.patheffects as path_effects
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.colors import Normalize, to_rgba
from matplotlib.ticker import FuncFormatter
from shapely.geometry import LineString, box as shape_box

from lib.field_frame import Frame, crs_note, decorate, frame_for, view_extent
from lib.palettes import categorical

from . import dataset, stats
from .dataset import Portfolio

SEED = stats.DEFAULT_SEED

# Stable colour per state so the same grower is the same colour in every
# figure and in the later report. Unknown states fall back to Okabe-Ito.
_STATE_COLORS = {
    "IL": "#0072B2",  # blue
    "IA": "#E69F00",  # orange
    "NE": "#009E73",  # green
    "MN": "#CC79A7",
    "IN": "#56B4E9",
    "MO": "#D55E0E",
    "KS": "#F0E442",
}
GROWER_ORDER_HINT = ("IL", "IA", "NE")

GEOMETRY_METRICS = [
    ("area_ha", "area (ha)"),
    ("perimeter_km", "perimeter (km)"),
    ("shape_index", "shape index"),
    ("compactness", "compactness"),
    ("bbox_fill_ratio", "bbox fill"),
    ("vertex_count", "vertices"),
    ("mean_lat", "latitude"),
]


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


def grower_labels(pf: Portfolio) -> dict[str, str]:
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


def grower_colors(pf: Portfolio) -> dict[str, str]:
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
        colors[grower] = _STATE_COLORS.get(labels[grower]) or next(spare)
    return colors


def _ordered_growers(pf: Portfolio, colors: dict[str, str]) -> list[str]:
    labels = grower_labels(pf)
    return sorted(
        pf.growers,
        key=lambda g: (
            GROWER_ORDER_HINT.index(labels[g]) if labels[g] in GROWER_ORDER_HINT else 99,
            labels[g],
        ),
    )


def _save(fig, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return path


def _stat_box(ax, lines: list[str], loc: str = "upper left") -> None:
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


def _north_arrow_spot(framed, extent) -> tuple[str, tuple[float, float]]:
    """Put the north arrow where it is farthest from any field centroid.

    At farm scale a hardcoded corner lands on a field on some layouts (and the
    rotated frames move north, so the arrow's footprint changes too). Candidate
    spots are the four corners plus the four edge midpoints; the winner is the
    one whose nearest field is furthest away in axes-fraction space.
    """
    x0, x1, y0, y1 = extent
    points = framed.geometry.representative_point()
    xs = (points.x.to_numpy() - x0) / (x1 - x0)
    ys = (points.y.to_numpy() - y0) / (y1 - y0)
    choices = {
        "upper left": (0.14, 0.86),
        "upper right": (0.86, 0.86),
        "lower left": (0.14, 0.14),
        "lower right": (0.86, 0.14),
        "top middle": (0.50, 0.86),
        "bottom middle": (0.50, 0.14),
        "left middle": (0.14, 0.50),
        "right middle": (0.86, 0.50),
    }

    def clearance(center: tuple[float, float]) -> float:
        cx, cy = center
        return float(np.min(np.hypot(xs - cx, ys - cy)))

    name = max(choices, key=lambda key: (clearance(choices[key]), -list(choices).index(key)))
    return name, choices[name]


def _headroom(ax, frac: float = 0.20) -> None:
    """Reserve empty vertical space so a stat box never covers a data point."""
    low, high = ax.get_ylim()
    span = high - low
    ax.set_ylim(low, high + frac * span)


# --------------------------------------------------------------------------
# B1 — size distribution
# --------------------------------------------------------------------------


def figure_area_distribution(pf: Portfolio, out_dir: Path) -> dict:
    colors = grower_colors(pf)
    labels = grower_labels(pf)
    growers = _ordered_growers(pf, colors)
    table = pf.fields_table

    fig, ax = plt.subplots(figsize=(9.6, 6.2))
    rng = np.random.default_rng(SEED)
    positions = np.arange(len(growers))
    values_by_grower: dict[str, np.ndarray] = {}

    for position, grower in zip(positions, growers):
        subset = table[table["grower_slug"] == grower].reset_index(drop=True)
        values = subset["area_ha"].to_numpy(dtype=float)
        values_by_grower[labels[grower]] = values
        color = colors[grower]

        ax.boxplot(
            [values],
            positions=[position],
            widths=0.46,
            patch_artist=True,
            showfliers=False,
            medianprops=dict(color="#111827", lw=2.0),
            boxprops=dict(facecolor=to_rgba(color, 0.16), edgecolor=color, lw=1.5),
            whiskerprops=dict(color=color, lw=1.2),
            capprops=dict(color=color, lw=1.2),
        )
        jitter = rng.uniform(-0.15, 0.15, size=values.size)
        ax.scatter(
            position + jitter,
            values,
            s=52,
            color=color,
            edgecolor="white",
            linewidth=0.9,
            zorder=4,
        )

        # Jittered dots are self-explanatory; labelling every extreme around
        # the box invites collisions, so the range is named in the stat box.

    overall_median = float(table["area_ha"].median())
    ax.axhline(
        overall_median,
        color="#6B7280",
        lw=1.0,
        ls=(0, (5, 4)),
        zorder=1,
        label=f"all-field median {overall_median:.0f} ha",
    )

    test = stats.kruskal_groups(values_by_grower)
    within = float(np.median([np.subtract(*np.percentile(v, [75, 25])) for v in values_by_grower.values()]))
    between = float(max(np.median(v) for v in values_by_grower.values()) - min(np.median(v) for v in values_by_grower.values()))
    verdict = (
        "spread inside a grower is wider than the gap between growers"
        if within >= between
        else "grower medians differ by more than the typical in-grower spread"
    )

    ax.set_xticks(positions)
    ax.set_xticklabels(
        [f"{labels[g]}\n{len(values_by_grower[labels[g]])} fields" for g in growers]
    )
    ax.set_ylabel("field area (ha)")
    ax.set_xlim(-0.55, len(growers) - 0.45)
    ax.set_title("Grower medians differ, but ranges overlap")
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper right", frameon=True, framealpha=0.95)
    _headroom(ax, 0.22)

    _stat_box(
        ax,
        [
            f"Kruskal-Wallis  H={test.h:.2f}  {stats.format_p(test.p)}",
            f"epsilon^2 = {test.epsilon_squared:.2f}   n = {test.n} fields",
            f"median in-grower IQR {within:.0f} ha",
            f"grower-median gap    {between:.0f} ha",
            "smallest {} {:.0f} ha   largest {} {:.0f} ha".format(
                table.loc[table["area_ha"].idxmin(), "field_id"],
                table["area_ha"].min(),
                table.loc[table["area_ha"].idxmax(), "field_id"],
                table["area_ha"].max(),
            ),
        ],
    )
    ax.text(
        0.015,
        0.02,
        f"Verdict: {verdict}.",
        transform=ax.transAxes,
        fontsize=8.6,
        style="italic",
        color="#374151",
        bbox=dict(boxstyle="round,pad=0.35", fc="#F9FAFB", ec="#E5E7EB", lw=0.8),
    )
    fig.text(
        0.012,
        0.005,
        "Each dot is one field. Box = IQR, line = median, whiskers = 1.5 IQR.",
        fontsize=7.6,
        color="#6B7280",
    )

    path = _save(fig, out_dir / "figures" / "b1_area_distribution.png")
    return {
        "id": "b1_area_distribution",
        "file": "figures/b1_area_distribution.png",
        "category": "boundaries",
        "scope": "across growers",
        "title": "Grower medians differ, but ranges overlap",
        "question": "Are the three growers' fields distinguishable by size alone?",
        "finding": (
            f"Areas span {table['area_ha'].min():.0f}-{table['area_ha'].max():.0f} ha. "
            f"The typical in-grower IQR is {within:.0f} ha against a "
            f"{between:.0f} ha span between grower medians, so {verdict}. "
            "The groups separate as samples, not as individuals: size alone "
            "does not say which grower a field belongs to."
        ),
        "n": test.n,
        "unit": "field",
        "stat": test.as_dict(),
        "caveat": (
            "n=30 fields across three farms (10 each). The test describes "
            "field-to-field variation, not a designed experiment; the "
            "grower comparison is 10 vs 10 vs 10."
        ),
    }


# --------------------------------------------------------------------------
# B2 — size vs shape
# --------------------------------------------------------------------------


def figure_area_vs_shape(pf: Portfolio, out_dir: Path) -> dict:
    colors = grower_colors(pf)
    labels = grower_labels(pf)
    growers = _ordered_growers(pf, colors)
    table = pf.fields_table

    fig, ax = plt.subplots(figsize=(9.4, 6.4))
    pooled = stats.spearman(table["area_ha"], table["shape_index"])

    for grower in growers:
        subset = table[table["grower_slug"] == grower].reset_index(drop=True)
        # Per-grower rho is context, not the test; with fewer than three fields
        # there is nothing to correlate and the legend says so.
        if len(subset) >= 3:
            rho_text = f"rho={stats.spearman(subset['area_ha'], subset['shape_index'], bootstrap=400).rho:+.2f}"
        else:
            rho_text = "rho n/a"
        ax.scatter(
            subset["area_ha"],
            subset["shape_index"],
            s=68,
            color=colors[grower],
            edgecolor="white",
            linewidth=0.9,
            label=f"{labels[grower]} (n={len(subset)}, {rho_text})",
            zorder=4,
        )
        # Anchor the two extremes per grower; labelling all 30 would be noise.
        for index in (int(subset["area_ha"].idxmax()), int(subset["shape_index"].idxmax())):
            ax.annotate(
                subset.loc[index, "field_id"],
                xy=(subset.loc[index, "area_ha"], subset.loc[index, "shape_index"]),
                xytext=(7, 7),
                textcoords="offset points",
                fontsize=7.2,
                color="#4B5563",
            )

    ax.set_xlabel("field area (ha)")
    ax.set_ylabel("shape index (1.0 = circle)")
    ax.set_title("Field size does not predict shape")
    ax.legend(loc="lower right", frameon=True, framealpha=0.95)
    _headroom(ax, 0.24)

    _stat_box(
        ax,
        [
            f"pooled Spearman rho = {pooled.rho:+.2f}",
            f"95% CI [{pooled.ci_low:+.2f}, {pooled.ci_high:+.2f}]  {stats.format_p(pooled.p)}",
            f"n = {pooled.n} fields",
        ],
        loc="upper left",
    )
    fig.text(
        0.012,
        0.005,
        "Shape index is scale-free: each perimeter is compared with that of an "
        "equal-area circle, so a trend here reflects layout, not units.",
        fontsize=7.6,
        color="#6B7280",
    )

    path = _save(fig, out_dir / "figures" / "b2_area_vs_shape.png")
    if pooled.ci_low is not None and pooled.ci_low <= 0.0 <= pooled.ci_high:
        direction = "no evidence of a monotone size-shape trend (the 95% CI includes zero)"
    elif pooled.rho > 0:
        direction = "larger fields tend to be more elongated or more dissected"
    else:
        direction = "larger fields tend to be more compact"
    return {
        "id": "b2_area_vs_shape",
        "file": "figures/b2_area_vs_shape.png",
        "category": "boundaries",
        "scope": "across fields within and across growers",
        "title": "Field size does not predict shape",
        "question": "Does field size predict boundary complexity?",
        "finding": (
            f"Pooled Spearman rho = {pooled.rho:+.2f} "
            f"(95% CI {pooled.ci_low:+.2f} to {pooled.ci_high:+.2f}) over {pooled.n} fields: "
            f"{direction}."
        ),
        "n": pooled.n,
        "unit": "field",
        "stat": pooled.as_dict(),
        "caveat": (
            "Rank correlation over 30 fields resolves only strong monotone "
            "trends; a value near zero means no monotone relation, not proof "
            "of independence for every possible shape."
        ),
    }


# --------------------------------------------------------------------------
# B3 — geometry metric collinearity
# --------------------------------------------------------------------------


def figure_geometry_correlation(pf: Portfolio, out_dir: Path) -> dict:
    table = pf.fields_table
    columns = [column for column, _ in GEOMETRY_METRICS]
    display = [label for _, label in GEOMETRY_METRICS]
    matrix = table[columns].astype(float)
    matrix.columns = display
    rho = matrix.corr(method="spearman")

    fig, ax = plt.subplots(figsize=(8.4, 7.0))
    image = ax.imshow(rho.to_numpy(), cmap="RdBu_r", vmin=-1.0, vmax=1.0)
    ax.set_xticks(range(len(display)))
    ax.set_yticks(range(len(display)))
    ax.set_xticklabels(display, rotation=38, ha="right")
    ax.set_yticklabels(display)
    ax.grid(False)

    for row in range(len(display)):
        for column in range(len(display)):
            value = float(rho.iloc[row, column])
            ax.text(
                column,
                row,
                f"{value:+.2f}",
                ha="center",
                va="center",
                fontsize=8.2,
                color="white" if abs(value) > 0.62 else "#111827",
            )

    # Box the algebraically identical pair: same information, opposite framing.
    pair = (display.index("shape index"), display.index("compactness"))
    for row, column in (pair, pair[::-1]):
        ax.add_patch(
            mpatches.Rectangle(
                (column - 0.5, row - 0.5),
                1.0,
                1.0,
                fill=False,
                edgecolor="#111827",
                linewidth=2.0,
            )
        )

    colorbar = fig.colorbar(image, ax=ax, fraction=0.042, pad=0.03)
    colorbar.set_label("Spearman rho", fontsize=9)
    ax.set_title("Boundary metrics collapse to size and shape", pad=12)

    size_pair = float(rho.loc["area (ha)", "perimeter (km)"])
    identity_pair = float(rho.loc["shape index", "compactness"])
    # Compactness is the inverted duplicate, so it is excluded from the shape
    # axis range; including it would just report the -1.00 already named below.
    shape_axis = ["perimeter (km)", "shape index", "vertices"]
    block = rho.loc[shape_axis, shape_axis].abs().to_numpy()
    off_diagonal = block[~np.eye(len(shape_axis), dtype=bool)]
    shape_low, shape_high = float(off_diagonal.min()), float(off_diagonal.max())
    ax.text(
        0.0,
        -0.21,
        "\n".join(
            [
                f"area ~ perimeter         rho = {size_pair:+.2f}",
                f"shape axis (3 metrics)   |rho| = {shape_low:.2f} to {shape_high:.2f}",
                f"shape ~ compactness      rho = {identity_pair:+.2f}   (same number, inverted)",
                f"n = {len(table)} fields",
                "",
                "Perimeter, shape index and vertices move together: one shape axis.",
                "Boxed cells mark the exact duplicate. Report area plus one shape",
                "metric; the other metrics add no independent information.",
            ]
        ),
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=8.5,
        family="DejaVu Sans Mono",
        linespacing=1.5,
        bbox=dict(boxstyle="round,pad=0.5", fc="#F9FAFB", ec="#D1D5DB", lw=0.9),
    )

    path = _save(fig, out_dir / "figures" / "b3_geometry_correlation.png")
    return {
        "id": "b3_geometry_correlation",
        "file": "figures/b3_geometry_correlation.png",
        "category": "boundaries",
        "scope": "correlation across all 30 fields",
        "title": "Boundary metrics collapse to size and shape",
        "question": "Which boundary metrics carry distinct information?",
        "finding": (
            f"Area is the only independent size metric (rho with perimeter "
            f"{size_pair:+.2f}). Perimeter, shape index and vertices co-vary at "
            f"|rho| = {shape_low:.2f} to {shape_high:.2f}; compactness is shape "
            f"index inverted (rho = {identity_pair:+.2f}). Quote area plus one "
            "shape metric; the rest are duplicates."
        ),
        "n": len(table),
        "unit": "field",
        "stat": {
            "test": "spearman_matrix",
            "n": len(table),
            "area_perimeter": round(size_pair, 4),
            "shape_index_compactness": round(identity_pair, 4),
            "shape_axis_abs_rho": [round(shape_low, 4), round(shape_high, 4)],
        },
        "caveat": (
            "The near-1.0 entries are properties of the metric definitions, not "
            "of these particular fields; they would hold for any boundary set."
        ),
    }


# --------------------------------------------------------------------------
# M0 — shared-frame overview
# --------------------------------------------------------------------------

_OVERVIEW_EPSG = 5070
_NON_CONUS = {"AK", "HI", "PR", "VI", "GU", "AS", "MP"}


def _true_north_vector(lon: float, lat: float, epsg: int) -> tuple[float, float]:
    """Unit vector of TRUE north in a projected map's axes.

    Albers x/y are not aligned with true north away from the central
    meridian, so the arrow is measured rather than assumed to point up.
    """
    probe = gpd.GeoDataFrame(
        geometry=[
            gpd.points_from_xy([lon], [lat])[0],
            gpd.points_from_xy([lon], [lat + 1e-4])[0],
        ],
        crs=4326,
    ).to_crs(epsg)
    dx = probe.geometry.iloc[1].x - probe.geometry.iloc[0].x
    dy = probe.geometry.iloc[1].y - probe.geometry.iloc[0].y
    magnitude = math.hypot(dx, dy) or 1.0
    return dx / magnitude, dy / magnitude


def _graticule(ax, extent, step: float = 2.0) -> int:
    """Draw a geographic graticule that shows the Albers gridding curvature."""
    x0, x1, y0, y1 = extent
    window = gpd.GeoSeries([shape_box(x0, y0, x1, y1)], crs=_OVERVIEW_EPSG).to_crs(4326)
    lon_lo, lat_lo, lon_hi, lat_hi = window.total_bounds

    lines = []
    for lon in np.arange(math.ceil(lon_lo / step) * step, lon_hi, step):
        lats = np.linspace(lat_lo, lat_hi, 60)
        lines.append(LineString(np.column_stack([np.full(lats.size, lon), lats])))
    for lat in np.arange(math.ceil(lat_lo / step) * step, lat_hi, step):
        lons = np.linspace(lon_lo, lon_hi, 60)
        lines.append(LineString(np.column_stack([lons, np.full(lons.size, lat)])))

    projected = gpd.GeoSeries(lines, crs=4326).to_crs(_OVERVIEW_EPSG)
    for line in projected:
        xs, ys = line.xy
        ax.plot(xs, ys, color="#9CA3AF", lw=0.55, alpha=0.55, zorder=1)
    return len(projected)


def _locator_inset(ax, states, bounds) -> str:
    """Small CONUS panel showing where the three clusters sit."""
    inset = ax.inset_axes([0.745, 0.60, 0.235, 0.33])
    inset.set_xticks([])
    inset.set_yticks([])
    for spine in inset.spines.values():
        spine.set_color("#9CA3AF")
        spine.set_linewidth(0.7)

    if states is None:
        inset.text(
            0.5,
            0.5,
            "CONUS locator\nunavailable",
            transform=inset.transAxes,
            ha="center",
            va="center",
            fontsize=6.6,
            color="#6B7280",
        )
        return "unavailable"

    conus = states[~states["state_code"].isin(_NON_CONUS)].to_crs(_OVERVIEW_EPSG)
    conus.plot(ax=inset, facecolor="#F3F4F6", edgecolor="#9CA3AF", lw=0.45, zorder=1)
    inset.set_xlabel("")
    inset.set_ylabel("")
    bx0, by0, bx1, by1 = bounds
    inset.add_patch(
        mpatches.Rectangle(
            (bx0, by0),
            bx1 - bx0,
            by1 - by0,
            fill=False,
            edgecolor="#B91C1C",
            lw=1.1,
            zorder=5,
        )
    )
    cx0, cy0, cx1, cy1 = conus.total_bounds
    inset.set_xlim(cx0, cx1)
    inset.set_ylim(cy0, cy1)
    inset.set_aspect("equal")
    inset.set_title("CONUS locator", fontsize=6.6, pad=2.5, color="#4B5563")
    return "geoadmin states"


def figure_portfolio_overview(pf: Portfolio, out_dir: Path) -> dict:
    """One shared frame for all three farms, at the cost of field detail."""
    colors = grower_colors(pf)
    labels = grower_labels(pf)
    growers = _ordered_growers(pf, colors)
    table = pf.fields_table

    states = dataset.load_states(pf.base)
    fields = pf.fields.to_crs(_OVERVIEW_EPSG)
    x0, y0, x1, y1 = fields.total_bounds
    extent = view_extent(x0, x1, y0, y1, pad_frac=0.08)

    fig, ax = plt.subplots(figsize=(11.4, 6.0), layout="constrained")
    if states is not None:
        states.to_crs(_OVERVIEW_EPSG).plot(
            ax=ax, facecolor="#F9FAFB", edgecolor="#D1D5DB", lw=0.5, zorder=0
        )

    drawn = _graticule(ax, extent)

    handles = []
    clusters = []
    for grower in growers:
        subset = pf.grower_fields(grower).to_crs(_OVERVIEW_EPSG)
        subset.plot(
            ax=ax,
            color=colors[grower],
            edgecolor="none",
            linewidth=0.0,
            zorder=4,
        )
        handles.append(
            mpatches.Patch(
                facecolor=colors[grower],
                edgecolor="white",
                linewidth=0.8,
                label=f"{labels[grower]} · {len(subset)} fields",
            )
        )
        center = subset.geometry.union_all().centroid
        ax.annotate(
            f"{labels[grower]} · {len(subset)} fields",
            xy=(center.x, center.y),
            xytext=(0, 15),
            textcoords="offset points",
            ha="center",
            va="bottom",
            fontsize=9.2,
            fontweight="bold",
            color="#111827",
            zorder=8,
            path_effects=[path_effects.withStroke(linewidth=2.6, foreground="white")],
        )
        clusters.append(
            {
                "label": labels[grower],
                "fields": len(subset),
                "center_lon": round(
                    float(subset.to_crs(4326).geometry.union_all().centroid.x), 3
                ),
                "center_lat": round(
                    float(subset.to_crs(4326).geometry.union_all().centroid.y), 3
                ),
            }
        )

    ax.set_xlim(extent[0], extent[1])
    ax.set_ylim(extent[2], extent[3])
    ax.set_aspect("equal")
    ax.grid(False)
    ax.xaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value / 1000:,.0f}"))
    ax.yaxis.set_major_formatter(FuncFormatter(lambda value, _: f"{value / 1000:,.0f}"))
    ax.tick_params(labelsize=8.2)
    ax.set_xlabel("easting (km) — EPSG:5070 Albers equal-area", fontsize=8.6)
    ax.set_ylabel("northing (km)", fontsize=8.6)

    lon_c, lat_c = (
        fields.to_crs(4326).geometry.union_all().centroid.x,
        fields.to_crs(4326).geometry.union_all().centroid.y,
    )
    adx, ady = _true_north_vector(lon_c, lat_c, _OVERVIEW_EPSG)
    frame = Frame(
        geometry=fields,
        epsg=_OVERVIEW_EPSG,
        rot=0.0,
        e0=0.0,
        n0=0.0,
        lon0=lon_c,
        lat0=lat_c,
        adx=adx,
        ady=ady,
    )
    decorate(ax, frame, arrow_r=0.07, arrow_fs=9.5, callout=False, arrow_xy=(0.055, 0.905))

    ax.legend(
        handles=handles,
        loc="lower left",
        frameon=True,
        framealpha=0.94,
        fontsize=7.8,
        title="grower",
        title_fontsize=7.8,
        borderpad=0.5,
    )
    states_source = _locator_inset(ax, states, fields.total_bounds)

    width_km = (x1 - x0) / 1000
    height_km = (y1 - y0) / 1000
    median_width_km = float(np.sqrt(table["area_ha"] * 1e4).median()) / 1000
    width_share = 100 * median_width_km / width_km

    title = f"One frame, {len(table)} fields across {width_km:.0f} km"
    ax.set_title(title, fontsize=13.5, pad=10)
    fig.supxlabel(
        "EPSG:5070, axes in km. At this scale a median field is "
        f"{median_width_km:.1f} km across — {width_share:.1f}% of the panel "
        "width — so field shape is unreadable here; M1 is the field-shape view. "
        "Graticule at 2°; the locator inset places the portfolio in CONUS.",
        fontsize=7.4,
        color="#4B5563",
    )

    path = _save(fig, out_dir / "figures" / "m0_portfolio_overview.png")
    return {
        "id": "m0_portfolio_overview",
        "file": "figures/m0_portfolio_overview.png",
        "category": "boundaries",
        "scope": "across growers",
        "title": title,
        "question": "Where does the whole portfolio sit, and how far apart are the farms?",
        "finding": (
            f"All {len(table)} fields fall into {len(growers)} clusters spanning "
            f"{width_km:.0f} km east-west and {height_km:.0f} km north-south. A median "
            f"field is {median_width_km:.1f} km across, {width_share:.1f}% of the panel "
            "width, so the shared frame is a location view, not a shape view."
        ),
        "n": len(table),
        "unit": "field",
        "stat": {
            "test": "descriptive",
            "n": len(table),
            "crs": f"EPSG:{_OVERVIEW_EPSG}",
            "x_span_km": round(width_km, 1),
            "y_span_km": round(height_km, 1),
            "median_field_width_km": round(median_width_km, 2),
            "panel_width_share_pct": round(width_share, 2),
            "graticule_step_deg": 2,
            "graticule_lines": drawn,
            "states_source": states_source,
            "clusters": clusters,
        },
        "caveat": (
            "The shared frame compresses each farm to a dot: distance between "
            "clusters is honest, field shape and layout are not. Read shape in "
            "M1. A single true-north arrow is drawn at the portfolio centre; "
            "grid convergence shifts north by a few degrees across the panel."
        ),
    }


# --------------------------------------------------------------------------
# M1 — layout map
# --------------------------------------------------------------------------


def figure_layout_map(pf: Portfolio, out_dir: Path) -> dict:
    colors = grower_colors(pf)
    labels = grower_labels(pf)
    growers = _ordered_growers(pf, colors)
    table = pf.fields_table

    fig, axes = plt.subplots(
        1,
        len(growers),
        figsize=(5.0 * len(growers), 5.6),
        layout="constrained",
    )
    if len(growers) == 1:
        axes = np.array([axes])
    norm = Normalize(
        vmin=float(table["area_ha"].min()),
        vmax=float(table["area_ha"].max()),
    )
    cmap = matplotlib.colormaps["viridis"]
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)

    recorded: list[dict] = []
    for panel_index, (ax, grower) in enumerate(zip(axes, growers)):
        subset = pf.grower_fields(grower)
        frame = frame_for(subset, panel_h_in=4.4, panel_w_in=4.0)
        framed = frame.geometry.reset_index(drop=True)
        framed.plot(
            ax=ax,
            column="area_ha",
            cmap=cmap,
            norm=norm,
            edgecolor="#111827",
            linewidth=1.0,
        )

        # At farm scale each field is a few percent of the panel, so labelling
        # all ten collides. Label the two that set the range and let the area
        # ramp carry the rest; the full inventory is table_fields.csv.
        areas = framed["area_ha"].to_numpy(dtype=float)
        points = framed.geometry.representative_point()
        bounds = framed.total_bounds
        x_mid = (bounds[0] + bounds[2]) / 2
        for index in (int(np.argmin(areas)), int(np.argmax(areas))):
            point = points.iloc[index]
            on_left = point.x < x_mid
            ax.annotate(
                f"{framed.loc[index, 'field_id']} · {areas[index]:.0f} ha",
                xy=(point.x, point.y),
                xytext=(-9 if on_left else 9, 12),
                textcoords="offset points",
                ha="right" if on_left else "left",
                va="bottom",
                fontsize=7.0,
                color="#111827",
                zorder=9,
                arrowprops=dict(
                    arrowstyle="-", lw=0.7, color="#4B5563", shrinkA=0, shrinkB=2
                ),
                path_effects=[path_effects.withStroke(linewidth=2.4, foreground="white")],
            )

        extent = view_extent(bounds[0], bounds[2], bounds[1], bounds[3], pad_frac=0.15)
        arrow_corner, arrow_xy = _north_arrow_spot(framed, extent)
        ax.set_xlim(extent[0], extent[1])
        ax.set_ylim(extent[2], extent[3])
        ax.set_aspect("equal")
        decorate(
            ax,
            frame,
            arrow_r=0.065,
            arrow_fs=10.5,
            callout=False,
            arrow_xy=arrow_xy,
        )
        ax.set_xlabel(crs_note(frame), fontsize=6.8)
        ax.set_ylabel("m from SW corner" if panel_index == 0 else "", fontsize=8.5)
        ax.grid(color="#E5E7EB", lw=0.6)
        median = float(framed["area_ha"].median())
        ax.set_title(
            f"{labels[grower]} — {len(subset)} fields, "
            f"{subset['area_acres'].sum():,.0f} ac\n"
            f"{int(subset['weather_cell'].nunique())} weather cells · "
            f"median {median:.0f} ha",
            fontsize=9.4,
            pad=8,
        )
        recorded.append(
            {
                "grower_slug": grower,
                "label": labels[grower],
                "fields": len(subset),
                "acres": round(float(subset["area_acres"].sum()), 1),
                "median_ha": round(median, 1),
                "tilt_deg": round(frame.rot, 1),
                "utm_epsg": frame.epsg,
                "weather_cells": int(subset["weather_cell"].nunique()),
                "north_arrow_corner": arrow_corner,
            }
        )

    colorbar = fig.colorbar(
        sm,
        ax=list(axes),
        orientation="horizontal",
        fraction=0.045,
        pad=0.06,
        aspect=45,
    )
    colorbar.set_label("field area (ha)", fontsize=9)
    fig.suptitle("Thirty fields, three layouts", fontsize=14, fontweight="bold")
    fig.supxlabel(
        "Each panel is one grower's farm in its own field-local frame: metre axes, "
        "auto-rotated, true north by arrow. Only the smallest and largest field per "
        "panel are labelled.",
        fontsize=7.6,
        color="#4B5563",
    )

    path = _save(fig, out_dir / "figures" / "m1_fields_by_grower.png")
    total_cells = int(table["weather_cell"].nunique())
    return {
        "id": "m1_fields_by_grower",
        "file": "figures/m1_fields_by_grower.png",
        "category": "boundaries",
        "scope": "across growers and within grower",
        "title": "Thirty fields, three layouts",
        "question": "Where are the fields, and how does each grower's layout differ?",
        "finding": (
            f"{len(table)} fields across {len(growers)} growers, "
            f"{table['area_acres'].sum():,.0f} acres. Panels are independent "
            f"field-local frames; the {total_cells} weather cells behind them are "
            f"fewer than the {len(table)} fields, so field-level weather is not "
            "independent."
        ),
        "n": len(table),
        "unit": "field",
        "stat": {"test": "descriptive", "n": len(table), "panels": recorded},
        "caveat": (
            "Each panel has its own tilt, so compare layout and shape, not "
            "absolute orientation. Fill is a continuous area ramp, not a class. "
            "Only the smallest and largest field per panel are labelled; the "
            "rest are identified in tables/table_fields.csv."
        ),
    }


def build_all(pf: Portfolio, out_dir: Path) -> list[dict]:
    style()
    return [
        figure_portfolio_overview(pf, out_dir),
        figure_area_distribution(pf, out_dir),
        figure_area_vs_shape(pf, out_dir),
        figure_geometry_correlation(pf, out_dir),
        figure_layout_map(pf, out_dir),
    ]
