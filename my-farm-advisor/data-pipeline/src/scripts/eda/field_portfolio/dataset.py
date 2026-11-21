"""Runtime discovery and the tidy field frame every stage consumes.

The portfolio is the set of farms under
``${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/*/farms/*``. This module is
the only place that knows that layout, so the figure modules never touch the
filesystem.

Two invariants are enforced at load time rather than patched later:

* **Canonical field identity.** ``field_id`` in a boundary feature must equal
  the slug used by the field directory, the inventory, and the weather table.
  A mismatch means an older tree; we stop instead of joining on a guess.
* **Weather is gridded, not per-field.** Every field is snapped to its NASA
  POWER 0.5° cell. The cell id travels with the field row, so any downstream
  analysis can choose the honest unit (field vs cell) instead of rediscovering
  the clustering by accident.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

EQUAL_AREA_CRS = "EPSG:5070"
BOUNDARY_CRS = "EPSG:4326"
NASA_POWER_CELL_DEG = 0.5
BOUNDARY_PROPERTIES = ("field_id", "crop", "state", "state_fips", "county", "csb_id")

FIELD_TABLE_COLUMNS = (
    "grower_slug",
    "farm_slug",
    "state",
    "county",
    "field_id",
    "boundary_crop",
    "csb_id",
    "area_ha",
    "area_acres",
    "perimeter_km",
    "shape_index",
    "compactness",
    "bbox_fill_ratio",
    "vertex_count",
    "mean_lat",
    "mean_lon",
    "weather_cell_lat",
    "weather_cell_lon",
    "weather_cell",
)


class PortfolioError(RuntimeError):
    """Raised when the runtime cannot support the analysis as requested."""


# --------------------------------------------------------------------------
# runtime layout
# --------------------------------------------------------------------------


def runtime_base(root: str | Path | None = None) -> Path:
    """Resolve ``${DATA_PIPELINE_DATA_ROOT}/data-pipeline``.

    Mirrors ``lib.runtime_paths``: the root is required, must be absolute, and
    must not silently fall back to a checkout-relative path.
    """
    if root is not None:
        candidate = Path(root).expanduser()
    else:
        raw = os.environ.get("DATA_PIPELINE_DATA_ROOT")
        if not raw:
            raise PortfolioError(
                "DATA_PIPELINE_DATA_ROOT is required; export it or pass --root"
            )
        candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise PortfolioError(f"data root must be absolute: {candidate}")
    base = candidate / "data-pipeline" if candidate.name != "data-pipeline" else candidate
    if not base.is_dir():
        raise PortfolioError(f"runtime base not found: {base}")
    return base


def default_output_dir(base: Path) -> Path:
    """Canonical, stable output location for the workflow."""
    return base / "derived" / "eda" / "field-portfolio"


@dataclass(frozen=True)
class FarmRef:
    grower_slug: str
    farm_slug: str
    directory: Path

    @property
    def label(self) -> str:
        return f"{self.grower_slug}/{self.farm_slug}"

    @property
    def boundary_path(self) -> Path:
        return self.directory / "boundary" / "field_boundaries.geojson"

    @property
    def tables_dir(self) -> Path:
        return self.directory / "derived" / "tables"

    def weather_path(self) -> Path:
        matches = sorted(self.tables_dir.glob("*weather*.csv"))
        if not matches:
            raise PortfolioError(f"{self.label}: no farm weather table in {self.tables_dir}")
        return matches[-1]


def discover_farms(base: Path) -> list[FarmRef]:
    """Every farm under ``growers/<grower>/farms/<farm>``, sorted for determinism."""
    growers_root = base / "growers"
    if not growers_root.is_dir():
        raise PortfolioError(f"no growers directory under {base}")
    found: list[FarmRef] = []
    for grower_dir in sorted(p for p in growers_root.iterdir() if p.is_dir()):
        farms_dir = grower_dir / "farms"
        if not farms_dir.is_dir():
            continue
        for farm_dir in sorted(p for p in farms_dir.iterdir() if p.is_dir()):
            if not (farm_dir / "boundary" / "field_boundaries.geojson").exists():
                continue
            found.append(FarmRef(grower_dir.name, farm_dir.name, farm_dir))
    if not found:
        raise PortfolioError(f"no farms with boundaries under {growers_root}")
    return found


def cdl_years(farm: FarmRef) -> list[int]:
    years: set[int] = set()
    for path in farm.tables_dir.glob("*_cdl.csv"):
        match = re.search(r"_(\d{4})_cdl\.csv$", path.name)
        if match:
            years.add(int(match.group(1)))
    return sorted(years)


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


def nasa_power_cell(value: float, size: float = NASA_POWER_CELL_DEG) -> float:
    """Snap a coordinate to its cell *centre*.

    ``round()`` in Python is banker's rounding, so an exact .5 boundary would
    alternate direction. Half-up keeps the assignment monotone and stable.
    """
    return float(np.floor(value / size + 0.5) * size)


def _vertex_count(geometry) -> int:
    if geometry is None or geometry.is_empty:
        return 0
    if geometry.geom_type == "Polygon":
        return len(geometry.exterior.coords)
    if geometry.geom_type == "MultiPolygon":
        return sum(len(part.exterior.coords) for part in geometry.geoms)
    return 0


def boundary_metrics(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Attach size/shape metrics. Geometry stays as given; metrics use 5070.

    ``shape_index`` is perimeter divided by the perimeter of an equal-area
    circle: 1.0 is a perfect circle and larger is more elongated or more
    dissected. ``compactness`` is its algebraic inverse (1 / shape_index**2),
    which is exactly why B3 reports them as one axis rather than two.
    """
    if gdf.crs is None:
        raise PortfolioError("boundary layer has no CRS")
    metric = gdf.to_crs(EQUAL_AREA_CRS)
    area_m2 = metric.geometry.area.to_numpy(dtype=float)
    perimeter_m = metric.geometry.length.to_numpy(dtype=float)
    bounds = metric.geometry.bounds
    bbox_area = (bounds["maxx"] - bounds["minx"]) * (bounds["maxy"] - bounds["miny"])
    centroid = metric.geometry.centroid.to_crs(BOUNDARY_CRS)

    out = gdf.copy()
    out["area_ha"] = area_m2 / 10_000.0
    out["area_acres"] = area_m2 / 4046.8564224
    out["perimeter_km"] = perimeter_m / 1_000.0
    safe_perimeter = np.where(perimeter_m > 0, perimeter_m, np.nan)
    out["shape_index"] = perimeter_m / (2.0 * np.sqrt(np.pi * area_m2))
    out["compactness"] = 4.0 * np.pi * area_m2 / np.square(safe_perimeter)
    out["bbox_fill_ratio"] = np.where(bbox_area > 0, area_m2 / bbox_area, np.nan)
    out["vertex_count"] = [float(_vertex_count(geom)) for geom in gdf.geometry]
    out["mean_lon"] = centroid.x.to_numpy()
    out["mean_lat"] = centroid.y.to_numpy()
    return out


# --------------------------------------------------------------------------
# context layers (optional, never data)
# --------------------------------------------------------------------------


def geoadmin_states_path(base: Path) -> Path | None:
    """State outlines used only as map context; absence must not fail a run."""
    directory = base / "shared" / "geoadmin" / "l1_states"
    for name in ("states_usa.geojson", "states_usa.parquet"):
        candidate = directory / name
        if candidate.exists():
            return candidate
    return None


def load_states(base: Path) -> gpd.GeoDataFrame | None:
    """Load US states for locator context, or ``None`` when unavailable."""
    path = geoadmin_states_path(base)
    if path is None:
        return None
    try:
        if path.suffix == ".parquet":
            states = gpd.read_parquet(path)
        else:
            states = gpd.read_file(path)
    except Exception:  # malformed payload is context, not a reason to stop
        return None
    if states.crs is None or "state_code" not in states.columns:
        return None
    return states


# --------------------------------------------------------------------------
# weather
# --------------------------------------------------------------------------


def load_weather(farm: FarmRef) -> pd.DataFrame:
    path = farm.weather_path()
    frame = pd.read_csv(path)
    required = {"field_id", "date", "lat", "lon", "T2M", "T2M_MAX", "T2M_MIN", "PRECTOTCORR"}
    missing = required - set(frame.columns)
    if missing:
        raise PortfolioError(f"{farm.label}: weather table missing {sorted(missing)}")
    frame["date"] = pd.to_datetime(frame["date"])
    if frame.empty:
        raise PortfolioError(f"{farm.label}: weather table is empty")
    if frame[["lat", "lon"]].isna().any().any():
        raise PortfolioError(f"{farm.label}: weather table has null coordinates")
    return frame


def weather_cells(weather: pd.DataFrame) -> pd.DataFrame:
    """One row per field: mean sample point and the NASA POWER cell it sits in."""
    grouped = (
        weather.groupby("field_id", as_index=False)
        .agg(lat=("lat", "mean"), lon=("lon", "mean"))
    )
    grouped["weather_cell_lat"] = [nasa_power_cell(v) for v in grouped["lat"]]
    grouped["weather_cell_lon"] = [nasa_power_cell(v) for v in grouped["lon"]]
    grouped["weather_cell"] = [
        f"{lat:.1f},{lon:.1f}"
        for lat, lon in zip(grouped["weather_cell_lat"], grouped["weather_cell_lon"])
    ]
    # Keep the sample point under its own names so the join into the field
    # frame cannot collide with the boundary centroid columns.
    return grouped.rename(
        columns={"lat": "weather_sample_lat", "lon": "weather_sample_lon"}
    )


# --------------------------------------------------------------------------
# portfolio
# --------------------------------------------------------------------------


@dataclass
class Portfolio:
    base: Path
    farms: list[FarmRef]
    fields: gpd.GeoDataFrame
    fields_table: pd.DataFrame
    weather: pd.DataFrame
    cdl_years: dict[str, list[int]]

    @property
    def growers(self) -> list[str]:
        return sorted(self.fields_table["grower_slug"].unique())

    def grower_fields(self, grower_slug: str) -> gpd.GeoDataFrame:
        return self.fields[self.fields["grower_slug"] == grower_slug]

    @property
    def cell_count(self) -> int:
        return int(self.fields_table["weather_cell"].nunique())


def _read_farm_meta(farm: FarmRef) -> dict:
    path = farm.directory / "farm.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_boundaries(farm: FarmRef) -> gpd.GeoDataFrame:
    if not farm.boundary_path.exists():
        raise PortfolioError(f"{farm.label}: missing {farm.boundary_path}")
    gdf = gpd.read_file(farm.boundary_path)
    if gdf.crs is None:
        raise PortfolioError(f"{farm.label}: boundary file has no CRS")
    if gdf.crs.to_epsg() != 4326:
        gdf = gdf.to_crs(BOUNDARY_CRS)
    if "field_id" not in gdf.columns:
        raise PortfolioError(f"{farm.label}: boundary features lack field_id")
    gdf["field_id"] = gdf["field_id"].astype(str)
    duplicate = gdf["field_id"][gdf["field_id"].duplicated()].tolist()
    if duplicate:
        raise PortfolioError(f"{farm.label}: duplicate field_id {sorted(set(duplicate))}")
    for column in BOUNDARY_PROPERTIES:
        if column not in gdf.columns:
            gdf[column] = None
    return gdf


def build_portfolio(
    root: str | Path | None = None,
    require_layers: tuple[str, ...] = ("weather", "cdl"),
) -> Portfolio:
    """Load every farm into one field frame, enforcing the layer contract."""
    base = runtime_base(root)
    farms = discover_farms(base)

    geometry_parts: list[gpd.GeoDataFrame] = []
    weather_parts: list[pd.DataFrame] = []
    years_by_farm: dict[str, list[int]] = {}

    for farm in farms:
        meta = _read_farm_meta(farm)
        gdf = load_boundaries(farm)
        weather = load_weather(farm) if "weather" in require_layers else None
        years = cdl_years(farm)
        years_by_farm[farm.label] = years
        if "cdl" in require_layers and len(years) < 5:
            raise PortfolioError(
                f"{farm.label}: expected >=5 CDL years, found {years or 'none'}; "
                "build the crop-history layer or pass --require-layers to relax"
            )

        metrics = boundary_metrics(gdf)
        metrics["grower_slug"] = farm.grower_slug
        metrics["farm_slug"] = farm.farm_slug
        metrics["farm_display_name"] = meta.get("display_name") or farm.farm_slug
        # farm.json is authoritative; boundaries are the fallback (an old tree
        # may have no state property on the features).
        farm_state = meta.get("state")
        metrics["farm_state"] = (
            farm_state if farm_state else metrics["state"]
        )

        if weather is not None:
            cells = weather_cells(weather)
            cell_by_field = cells.set_index("field_id")
            absent = sorted(set(metrics["field_id"]) - set(cell_by_field.index))
            if absent:
                raise PortfolioError(
                    f"{farm.label}: {len(absent)} field(s) missing from the weather "
                    f"table: {absent}"
                )
            metrics = metrics.join(
                cell_by_field[["weather_cell_lat", "weather_cell_lon", "weather_cell"]],
                on="field_id",
            )
            weather_parts.append(weather.assign(grower_slug=farm.grower_slug, farm_slug=farm.farm_slug))
        else:
            metrics["weather_cell_lat"] = np.nan
            metrics["weather_cell_lon"] = np.nan
            metrics["weather_cell"] = None

        geometry_parts.append(metrics)

    fields = pd.concat(geometry_parts, ignore_index=True)
    fields = gpd.GeoDataFrame(fields, geometry="geometry", crs=geometry_parts[0].crs)

    if not fields.empty and fields["field_id"].duplicated().any():
        dupes = fields.loc[fields["field_id"].duplicated(), ["grower_slug", "field_id"]]
        raise PortfolioError(
            "field_id is not unique across growers; join keys would collide: "
            f"{dupes.to_dict('records')}"
        )

    table = pd.DataFrame(fields.drop(columns="geometry"))
    for column in FIELD_TABLE_COLUMNS:
        if column not in table.columns:
            table[column] = None
    table = table[list(FIELD_TABLE_COLUMNS)].sort_values(
        ["grower_slug", "farm_slug", "field_id"], kind="stable"
    ).reset_index(drop=True)

    weather_frame = (
        pd.concat(weather_parts, ignore_index=True) if weather_parts else pd.DataFrame()
    )

    return Portfolio(
        base=base,
        farms=farms,
        fields=fields,
        fields_table=table,
        weather=weather_frame,
        cdl_years=years_by_farm,
    )


# --------------------------------------------------------------------------
# output helpers
# --------------------------------------------------------------------------


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def relative_to_base(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def input_manifest(paths: list[Path], base: Path) -> list[dict]:
    """``*_rel`` first, per the runtime provenance contract."""
    entries: list[dict] = []
    for path in sorted(set(paths)):
        if not path.exists():
            continue
        entries.append(
            {
                "path_rel": relative_to_base(path, base),
                "bytes": path.stat().st_size,
                "mtime": datetime.fromtimestamp(
                    path.stat().st_mtime, timezone.utc
                ).isoformat(timespec="seconds"),
                "sha256": sha256_file(path),
            }
        )
    return entries


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_table(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, float_format="%.6f")
