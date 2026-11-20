#!/usr/bin/env python3
"""Validate grower/farm data layers against the canonical boundary geometry.

This is the dataset gate for Assignment work: it fails loudly on the defects
that are easy to miss by eye -- mislabelled states, ID drift between metadata
and tables, missing weather days, CDL years that do not cover every field, and
artifacts left over from an earlier, smaller field set.

Usage
-----
    python validate_dataset.py --root /path/to/runtime
    python validate_dataset.py --root /path/to/runtime --layers weather,cdl

Exit status: 0 when every check passes or only warns, 1 on any failure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REQUIRED_WEATHER_COLUMNS = (
    "field_id",
    "date",
    "T2M",
    "T2M_MAX",
    "T2M_MIN",
    "PRECTOTCORR",
    "ALLSKY_SFC_SW_DWN",
    "RH2M",
    "WS10M",
)

# Plausible physical ranges; deliberately loose so real extremes pass.
WEATHER_RANGES = {
    "T2M": (-70.0, 60.0),
    "T2M_MAX": (-70.0, 65.0),
    "T2M_MIN": (-75.0, 50.0),
    "PRECTOTCORR": (0.0, 800.0),
    "ALLSKY_SFC_SW_DWN": (0.0, 45.0),
    "RH2M": (0.0, 100.0),
    "WS10M": (0.0, 80.0),
}

STALE_TABLE_PATTERNS = (
    "fields_soil",
    "full_ssurgo",
    "ssurgo_summary",
    "ssurgo_restrictions",
    "dem_summary",
)

DEFAULT_LAYERS = ("metadata", "boundaries", "weather", "cdl", "staleness")


@dataclass
class Result:
    status: str
    check: str
    detail: str = ""

    def render(self) -> str:
        suffix = f" -- {self.detail}" if self.detail else ""
        return f"  [{self.status}] {self.check}{suffix}"


@dataclass
class FarmReport:
    grower: str
    farm: str
    results: list[Result] = field(default_factory=list)

    def add(self, status: str, check: str, detail: str = "") -> None:
        self.results.append(Result(status, check, detail))

    @property
    def failures(self) -> int:
        return sum(1 for item in self.results if item.status == "FAIL")

    @property
    def warnings(self) -> int:
        return sum(1 for item in self.results if item.status == "WARN")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=None,
        help="runtime data root (default: $DATA_PIPELINE_DATA_ROOT)",
    )
    parser.add_argument(
        "--grower", action="append", default=None, help="limit to grower slug(s)"
    )
    parser.add_argument(
        "--farm", action="append", default=None, help="limit to farm slug(s)"
    )
    parser.add_argument(
        "--layers",
        default=",".join(DEFAULT_LAYERS),
        help=f"comma-separated checks to run (default: {','.join(DEFAULT_LAYERS)})",
    )
    return parser.parse_args(argv)


def _load_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _state(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    if len(code) == 2 and code.isascii() and code.isalpha():
        return code
    return None


def _expected_cdl_years() -> tuple[int, ...]:
    """Five-year window ending at the repo's pinned CDL latest year.

    Read as text instead of imported so validation has no rasterio dependency
    and no import side effects.
    """
    latest = 2023
    cdl_script = Path(__file__).resolve().parent / "ingest" / "download_cdl.py"
    try:
        match = re.search(
            r"^DEFAULT_CDL_LATEST_YEAR\s*=\s*(\d{4})",
            cdl_script.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        if match:
            latest = int(match.group(1))
    except OSError:
        pass
    return tuple(range(latest - 4, latest + 1))


def _discover_farms(
    growers_root: Path, growers: list[str] | None, farms: list[str] | None
) -> list[tuple[str, str, Path]]:
    found: list[tuple[str, str, Path]] = []
    if not growers_root.is_dir():
        return found
    for grower_dir in sorted(p for p in growers_root.iterdir() if p.is_dir()):
        if growers and grower_dir.name not in growers:
            continue
        farms_dir = grower_dir / "farms"
        if not farms_dir.is_dir():
            continue
        for farm_dir in sorted(p for p in farms_dir.iterdir() if p.is_dir()):
            if farms and farm_dir.name not in farms:
                continue
            found.append((grower_dir.name, farm_dir.name, farm_dir))
    return found


def _check_metadata(report: FarmReport, farm_dir: Path, boundary) -> dict:
    """Farm/field/grower metadata must agree with boundary geometry."""
    farm_json = _load_json(farm_dir / "farm.json")
    if not farm_json:
        report.add("FAIL", "farm.json", "missing or unreadable")
    else:
        boundary_states = sorted({_state(v) for v in boundary["state"] if _state(v)})
        recorded = _state(farm_json.get("state"))
        if not boundary_states:
            report.add("WARN", "farm.json state", "boundary carries no state property")
        elif recorded not in boundary_states:
            report.add(
                "FAIL",
                "farm.json state",
                f"records {recorded!r} but boundary says {boundary_states}",
            )
        else:
            report.add("PASS", "farm.json state", f"{recorded} matches boundary")

        expected_fips = sorted({str(v).zfill(2) for v in boundary["state_fips"] if str(v)})
        if expected_fips and str(farm_json.get("state_fips", "")).zfill(2) not in expected_fips:
            report.add(
                "FAIL",
                "farm.json state_fips",
                f"records {farm_json.get('state_fips')!r} but boundary says {expected_fips}",
            )
        elif expected_fips:
            report.add("PASS", "farm.json state_fips", ",".join(expected_fips))

        expected_counties = sorted({str(v).strip() for v in boundary["county"] if str(v).strip()})
        recorded_counties = farm_json.get("counties")
        if expected_counties and isinstance(recorded_counties, list):
            if sorted(map(str, recorded_counties)) != expected_counties:
                report.add(
                    "FAIL",
                    "farm.json counties",
                    f"records {recorded_counties} but boundary says {expected_counties}",
                )
            else:
                report.add("PASS", "farm.json counties", ", ".join(expected_counties))

    grower_json = farm_dir.parents[1] / "grower.json"
    if not _load_json(grower_json):
        report.add("FAIL", "grower.json", f"missing or unreadable: {grower_json}")

    field_dirs = sorted(p.name for p in (farm_dir / "fields").iterdir() if p.is_dir())
    mismatched: list[str] = []
    for slug in field_dirs:
        field_json = _load_json(farm_dir / "fields" / slug / "field.json")
        if not field_json:
            mismatched.append(f"{slug}:missing")
        elif str(field_json.get("field_id")) != slug:
            mismatched.append(f"{slug}:{field_json.get('field_id')}")
    if mismatched:
        report.add(
            "FAIL",
            "field.json field_id",
            f"{len(mismatched)} of {len(field_dirs)} do not equal the field slug "
            f"(e.g. {', '.join(mismatched[:4])})",
        )
    elif field_dirs:
        report.add("PASS", "field.json field_id", f"{len(field_dirs)} fields canonical")
    return farm_json


def _check_boundaries(report: FarmReport, farm_dir: Path, boundary) -> None:
    ids = [str(v) for v in boundary["field_id"]]
    if len(ids) != len(set(ids)):
        report.add("FAIL", "boundary field_id", "duplicate field_id values")
    csb = [str(v) for v in boundary.get("csb_id", []) if str(v).strip()]
    if len(csb) != len(set(csb)):
        report.add("FAIL", "boundary csb_id", "duplicate csb_id values")

    areas = boundary.get("area_acres") or []
    if areas and any(value <= 0 or value != value for value in areas):
        report.add("FAIL", "boundary area_acres", "non-positive or null area present")

    field_dirs = {p.name for p in (farm_dir / "fields").iterdir() if p.is_dir()}
    missing_dirs = sorted(set(ids) - field_dirs)
    extra_dirs = sorted(field_dirs - set(ids))
    if missing_dirs or extra_dirs:
        report.add(
            "FAIL",
            "field directories",
            f"missing={missing_dirs or '-'} extra={extra_dirs or '-'}",
        )
    else:
        report.add("PASS", "field directories", f"{len(field_dirs)} match boundary ids")

    inventory = farm_dir / "manifests" / "field-inventory.csv"
    if not inventory.exists():
        report.add("FAIL", "field-inventory.csv", "missing")
    else:
        rows = [line for line in inventory.read_text(encoding="utf-8").splitlines() if line.strip()]
        header = rows[0].split(",") if rows else []
        if header[:2] != ["field_id", "field_slug"]:
            report.add("FAIL", "field-inventory.csv", f"unexpected header {header}")
        else:
            inv_ids = {line.split(",")[0].strip() for line in rows[1:]}
            if inv_ids != set(ids):
                report.add(
                    "FAIL",
                    "field-inventory.csv",
                    f"ids differ from boundary (only inventory: {sorted(inv_ids - set(ids))}, "
                    f"only boundary: {sorted(set(ids) - inv_ids)})",
                )
            else:
                report.add("PASS", "field-inventory.csv", f"{len(inv_ids)} ids match boundary")

    per_field_issues: list[str] = []
    for slug in sorted(field_dirs):
        path = farm_dir / "fields" / slug / "boundary" / "field_boundary.geojson"
        if not path.exists():
            per_field_issues.append(f"{slug}:missing")
            continue
        payload = _load_json(path)
        if payload.get("crs", {}).get("type") == "name":
            crs_name = str(payload["crs"]["properties"].get("name", "")).upper()
            # GeoJSON writers emit either EPSG:4326 or the equivalent OGC CRS84.
            if "4326" not in crs_name and "CRS84" not in crs_name:
                per_field_issues.append(f"{slug}:crs={crs_name}")
        feats = payload.get("features") or []
        if len(feats) != 1:
            per_field_issues.append(f"{slug}:features={len(feats)}")
    if per_field_issues:
        report.add(
            "FAIL",
            "per-field boundaries",
            f"{len(per_field_issues)} issue(s): {', '.join(per_field_issues[:5])}",
        )
    else:
        report.add("PASS", "per-field boundaries", f"{len(field_dirs)} present, single-feature")


def _check_weather(report: FarmReport, farm_dir: Path, ids: set[str]) -> None:
    import pandas as pd

    candidates = sorted((farm_dir / "derived" / "tables").glob("*weather*.csv"))
    if not candidates:
        report.add("FAIL", "farm weather table", "missing")
        return
    path = candidates[0]
    match = re.search(r"_(\d{4})_(\d{4})\.csv$", path.name)
    if match:
        start_year, end_year = int(match.group(1)), int(match.group(2))
    else:
        start_year, end_year = 2021, 2025
    expected_days = (pd.Timestamp(f"{end_year}-12-31") - pd.Timestamp(f"{start_year}-01-01")).days + 1
    expected_dates = pd.date_range(f"{start_year}-01-01", f"{end_year}-12-31", freq="D")

    frame = pd.read_csv(path)
    missing_cols = [c for c in REQUIRED_WEATHER_COLUMNS if c not in frame.columns]
    if missing_cols:
        report.add("FAIL", f"weather columns ({path.name})", f"missing {missing_cols}")
        return
    if frame.isna().any().any():
        nulls = {k: int(v) for k, v in frame.isna().sum().items() if v}
        report.add("FAIL", "weather nulls", str(nulls))
    if set(frame["field_id"].astype(str)) != ids:
        report.add(
            "FAIL",
            "weather field coverage",
            f"table has {frame['field_id'].nunique()} fields, boundary has {len(ids)}",
        )
    per_field_bad: list[str] = []
    for field_id, group in frame.groupby(frame["field_id"].astype(str)):
        if len(group) != expected_days:
            per_field_bad.append(f"{field_id}:rows={len(group)}")
            continue
        dates = pd.to_datetime(group["date"])
        if not dates.is_monotonic_increasing or not dates.reset_index(drop=True).equals(
            pd.Series(expected_dates)
        ):
            per_field_bad.append(f"{field_id}:dates")
    if per_field_bad:
        report.add(
            "FAIL",
            "weather coverage",
            f"{len(per_field_bad)} field(s) off: {', '.join(per_field_bad[:5])}",
        )
    else:
        report.add(
            "PASS",
            "weather coverage",
            f"{len(ids)} fields x {expected_days} days ({start_year}-{end_year})",
        )

    range_issues: list[str] = []
    for column, (low, high) in WEATHER_RANGES.items():
        series = pd.to_numeric(frame[column], errors="coerce")
        if series.min() < low or series.max() > high:
            range_issues.append(
                f"{column}={series.min():.1f}..{series.max():.1f}"
            )
    if range_issues:
        report.add("WARN", "weather ranges", "; ".join(range_issues))
    else:
        report.add("PASS", "weather ranges", "all variables within plausible bounds")

    field_files = sorted((farm_dir / "fields").glob("*/weather/daily_weather.csv"))
    short = [p.parent.parent.name for p in field_files if len(pd.read_csv(p)) != expected_days]
    missing = ids - {p.parent.parent.name for p in field_files}
    if missing or short:
        report.add(
            "FAIL",
            "per-field weather",
            f"missing={sorted(missing) or '-'} wrong_rows={short or '-'}",
        )
    else:
        report.add("PASS", "per-field weather", f"{len(field_files)} files x {expected_days} rows")


def _check_cdl(report: FarmReport, farm_dir: Path, ids: set[str]) -> None:
    import pandas as pd

    tables = farm_dir / "derived" / "tables"
    expected_years = _expected_cdl_years()
    found_years: dict[int, Path] = {}
    for path in sorted(tables.glob("*_cdl.csv")):
        match = re.search(r"_(\d{4})_cdl\.csv$", path.name)
        if match:
            found_years[int(match.group(1))] = path
    missing_years = [year for year in expected_years if year not in found_years]
    if missing_years:
        report.add("FAIL", "cdl years", f"missing {missing_years}")

    problems: list[str] = []
    for year, path in sorted(found_years.items()):
        frame = pd.read_csv(path)
        required = {"field_id", "year", "crop_code", "crop_name", "pct"}
        if not required.issubset(frame.columns):
            problems.append(f"{year}:columns")
            continue
        if set(frame["field_id"].astype(str)) != ids:
            problems.append(f"{year}:fields={frame['field_id'].nunique()}")
            continue
        if frame["crop_code"].isna().any() or frame["crop_name"].isna().any():
            problems.append(f"{year}:null_crop")
        pct = pd.to_numeric(frame["pct"], errors="coerce")
        if pct.isna().any() or (pct < 0).any() or (pct > 100.5).any():
            problems.append(f"{year}:pct_out_of_range")
        totals = frame.assign(_pct=pct).groupby("field_id")["_pct"].sum()
        # A field can have unclassified pixels, so sums may sit slightly below
        # 100; anything above 100.5 means overlapping classes.
        over = totals[totals > 100.5]
        if not over.empty:
            problems.append(f"{year}:pct_over_100={over.round(2).to_dict()}")
        low = totals[totals < 95.0]
        if not low.empty:
            problems.append(f"{year}:pct_low={low.round(2).to_dict()}")
    if problems:
        report.add("FAIL", "cdl year tables", "; ".join(problems[:5]))
    elif not missing_years:
        report.add(
            "PASS",
            "cdl year tables",
            f"{len(found_years)} years x {len(ids)} fields, pct within range",
        )

    rotation = sorted(tables.glob("*crop_rotation.csv"))
    if not rotation:
        report.add("FAIL", "crop rotation table", "missing")
    else:
        frame = pd.read_csv(rotation[0])
        if "field_id" not in frame.columns:
            report.add("FAIL", "crop rotation table", "no field_id column")
        elif set(frame["field_id"].astype(str)) != ids:
            report.add(
                "FAIL",
                "crop rotation table",
                f"covers {frame['field_id'].nunique()} of {len(ids)} fields",
            )
        else:
            report.add("PASS", "crop rotation table", f"{len(frame)} rows, all fields")


def _check_staleness(report: FarmReport, farm_dir: Path, ids: set[str]) -> None:
    if len(ids) <= 3:
        return
    first_three = {f"f{i}" for i in range(1, 4)}

    tables = farm_dir / "derived" / "tables"
    stale_tables: list[str] = []
    for path in sorted(tables.glob("*.csv")):
        if not any(pattern in path.name for pattern in STALE_TABLE_PATTERNS):
            continue
        try:
            frame_header = path.open("r", encoding="utf-8").readline()
        except OSError:
            continue
        if "field_id" not in frame_header:
            continue
        try:
            import pandas as pd

            covered = pd.read_csv(path, usecols=["field_id"])["field_id"].astype(str)
        except Exception:
            continue
        if set(covered) and set(covered) < ids:
            stale_tables.append(f"{path.name} ({covered.nunique()}/{len(ids)})")
    if stale_tables:
        report.add("WARN", "stale farm tables", "; ".join(stale_tables[:5]))
    else:
        report.add("PASS", "stale farm tables", "none")

    stale_reports: list[str] = []
    for path in sorted((farm_dir / "derived" / "reports").glob("*")):
        if not path.is_file() or path.stat().st_size > 8_000_000:
            continue
        if path.suffix.lower() not in {".md", ".html", ".json", ".csv"}:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        referenced = set(re.findall(r"\b([a-z]{2}-f\d+)\b", text))
        if referenced and referenced <= {f"{report.grower[:2]}-{suffix}" for suffix in first_three}:
            stale_reports.append(path.name)
    if stale_reports:
        report.add("WARN", "stale farm reports", "; ".join(stale_reports[:5]))


def _grower_map_check(report: FarmReport, grower_dir: Path, ids: set[str]) -> None:
    if len(ids) <= 3:
        return
    maps_dir = grower_dir / "maps"
    if not maps_dir.is_dir():
        report.add("WARN", "grower map", "no maps directory")
        return
    maps = sorted(maps_dir.glob("*_fields_map.html"))
    if not maps:
        report.add("WARN", "grower map", "no grower map HTML")
        return
    stale: list[str] = []
    for path in maps:
        text = path.read_text(encoding="utf-8", errors="ignore")
        referenced = set(re.findall(r"\b([a-z]{2}-f\d+)\b", text))
        if referenced and len(referenced) < len(ids) and referenced <= ids:
            stale.append(f"{path.name} ({len(referenced)}/{len(ids)})")
    if stale:
        report.add("WARN", "grower map coverage", "; ".join(stale))
    else:
        report.add("PASS", "grower map coverage", f"{len(maps)} map(s)")


def validate_farm(grower: str, farm: str, farm_dir: Path, layers: set[str]) -> FarmReport:
    import geopandas as gpd

    report = FarmReport(grower, farm)
    boundary_path = farm_dir / "boundary" / "field_boundaries.geojson"
    if not boundary_path.exists():
        report.add("FAIL", "farm boundary", "missing")
        return report

    frame = gpd.read_file(boundary_path)
    if frame.crs is None or frame.crs.to_epsg() != 4326:
        report.add("FAIL", "boundary CRS", f"{frame.crs}")
    else:
        report.add("PASS", "boundary CRS", "EPSG:4326")
    invalid = int((~frame.geometry.is_valid).sum())
    if invalid:
        report.add("FAIL", "boundary geometry", f"{invalid} invalid polygon(s)")
    else:
        report.add("PASS", "boundary geometry", f"{len(frame)} valid polygons")

    ids = {str(v) for v in frame["field_id"]}
    boundary = {
        "field_id": frame["field_id"].astype(str).tolist(),
        "state": frame["state"].tolist() if "state" in frame else [],
        "state_fips": frame["state_fips"].tolist() if "state_fips" in frame else [],
        "county": frame["county"].tolist() if "county" in frame else [],
        "csb_id": frame["csb_id"].astype(str).tolist() if "csb_id" in frame else [],
        "area_acres": [
            float(value) for value in frame["area_acres"] if value is not None
        ]
        if "area_acres" in frame
        else [],
    }

    if "metadata" in layers:
        _check_metadata(report, farm_dir, boundary)
    if "boundaries" in layers:
        _check_boundaries(report, farm_dir, boundary)
    if "weather" in layers:
        _check_weather(report, farm_dir, ids)
    if "cdl" in layers:
        _check_cdl(report, farm_dir, ids)
    if "staleness" in layers:
        _check_staleness(report, farm_dir, ids)
        _grower_map_check(report, farm_dir.parents[1], ids)
    return report


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.root:
        os.environ["DATA_PIPELINE_DATA_ROOT"] = str(
            Path(args.root).expanduser().resolve(strict=False)
        )
    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from lib.paths import GROWERS_ROOT

    layers = {item.strip().lower() for item in args.layers.split(",") if item.strip()}
    unknown = layers - set(DEFAULT_LAYERS)
    if unknown:
        print(f"error: unknown layers {sorted(unknown)}", file=sys.stderr)
        return 2

    farms = _discover_farms(GROWERS_ROOT, args.grower, args.farm)
    if not farms:
        print(f"error: no farms found under {GROWERS_ROOT}", file=sys.stderr)
        return 2

    failures = 0
    warnings = 0
    for grower, farm, farm_dir in farms:
        report = validate_farm(grower, farm, farm_dir, layers)
        print(f"\n{grower}/{farm}")
        for result in report.results:
            print(result.render())
        failures += report.failures
        warnings += report.warnings

    print(f"\n{failures} failure(s), {warnings} warning(s) across {len(farms)} farm(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
