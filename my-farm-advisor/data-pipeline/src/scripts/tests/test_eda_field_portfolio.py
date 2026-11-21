"""Offline tests for the field-portfolio EDA (Stage 1: boundaries).

The fixture is a two-grower, two-field runtime with the minimum layers the
loader insists on: boundaries, a farm weather table, and five CDL years. No
network, no real grower data, no optional layers.

Run: python -m pytest src/scripts/tests/test_eda_field_portfolio.py -q
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
for candidate in (SCRIPTS, SCRIPTS / "eda"):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from field_portfolio import dataset, metrics, stats  # noqa: E402
from field_portfolio.dataset import PortfolioError  # noqa: E402

WEATHER_COLUMNS = (
    "field_id,lat,lon,date,T2M,T2M_MAX,T2M_MIN,PRECTOTCORR,"
    "ALLSKY_SFC_SW_DWN,RH2M,WS10M"
)


# --------------------------------------------------------------------------
# fixture
# --------------------------------------------------------------------------


def _polygon(lon: float, lat: float, delta: float = 0.01) -> dict:
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [lon, lat],
                [lon + delta, lat],
                [lon + delta, lat + delta],
                [lon, lat + delta],
                [lon, lat],
            ]
        ],
    }


def _feature(field_id: str, lon: float, lat: float, crop: str, state: str, acres: float) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "field_id": field_id,
            "crop": crop,
            "state": state,
            "state_fips": "17" if state == "IL" else "31",
            "county": "Test",
            "csb_id": f"csb-{field_id}",
            "area_acres": acres,
        },
        "geometry": _polygon(lon, lat),
    }


def _write_farm(root: Path, grower: str, farm: str, state: str, specs, *, skip_cdl: bool = False, skip_weather_field: str | None = None) -> Path:
    farm_dir = root / "data-pipeline" / "growers" / grower / "farms" / farm
    (farm_dir / "boundary").mkdir(parents=True, exist_ok=True)
    (farm_dir / "derived" / "tables").mkdir(parents=True, exist_ok=True)
    (farm_dir / "manifests").mkdir(parents=True, exist_ok=True)

    features = [_feature(fid, lon, lat, crop, state, acres) for fid, lon, lat, crop, acres in specs]
    (farm_dir / "boundary" / "field_boundaries.geojson").write_text(
        json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
    )
    (farm_dir / "farm.json").write_text(
        json.dumps(
            {
                "grower_slug": grower,
                "farm_slug": farm,
                "display_name": f"{state} Test Farm",
                "state": state,
            }
        ),
        encoding="utf-8",
    )

    rows = [WEATHER_COLUMNS]
    for field_id, lon, lat, _crop, _acres in specs:
        if field_id == skip_weather_field:
            continue
        for day in range(10):
            date = pd.Timestamp("2021-01-01") + pd.Timedelta(days=day)
            rows.append(
                f"{field_id},{lat + 0.001},{lon + 0.001},{date.date()},"
                "2.0,6.0,-2.0,1.5,8.0,80.0,3.0"
            )
    (farm_dir / "derived" / "tables" / f"{farm}_weather_2021_2025.csv").write_text(
        "\n".join(rows) + "\n", encoding="utf-8"
    )

    if not skip_cdl:
        for year in range(2019, 2024):
            rows = []
            for index, (field_id, *_rest) in enumerate(specs):
                # One continuous-corn field plus one alternating field keeps
                # every CDL branch (sequence, transition, monocrop) exercised.
                if index == 0:
                    crop = "Corn"
                else:
                    crop = "Corn" if (year - 2019 + index) % 2 == 0 else "Soybeans"
                rows.append(f"{field_id},{year},1,{crop},100,100.0,test")
            (farm_dir / "derived" / "tables" / f"{farm}_{year}_cdl.csv").write_text(
                "field_id,year,crop_code,crop_name,pixel_count,pct,crop_source\n"
                + "".join(f"{row}\n" for row in rows),
                encoding="utf-8",
            )
    return farm_dir


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_PIPELINE_DATA_ROOT", str(tmp_path))
    _write_farm(
        tmp_path,
        "il-grower",
        "il-farm",
        "IL",
        [
            ("il-f1", -88.00, 40.70, "Corn", 250.0),
            ("il-f2", -87.90, 40.80, "Soybeans", 500.0),
        ],
    )
    _write_farm(
        tmp_path,
        "ne-grower",
        "ne-farm",
        "NE",
        [
            ("ne-f1", -97.80, 41.20, "Corn", 300.0),
            ("ne-f2", -97.70, 41.30, "Corn", 450.0),
        ],
    )
    return tmp_path


# --------------------------------------------------------------------------
# metrics
# --------------------------------------------------------------------------


def test_gdd_applies_the_upper_cap():
    # 36 C max is capped at 30: (30 + 20) / 2 - 10 = 15, not 18.
    result = metrics.growing_degree_days([20.0], [36.0])
    assert result[0] == pytest.approx(15.0)

    # Unrounded mid-point: (30 + 19) / 2 - 10 = 14.5
    assert metrics.growing_degree_days([19.0], [31.0])[0] == pytest.approx(14.5)

    # Below base: floored at zero, never negative.
    assert metrics.growing_degree_days([-5.0], [5.0])[0] == 0.0


def test_frost_window_ignores_midwinter_thaws():
    dates = pd.date_range("2021-01-01", "2021-12-31", freq="D")
    tmin = np.full(len(dates), 2.0)
    index = {day.date(): position for position, day in enumerate(dates)}
    tmin[index[pd.Timestamp("2021-01-15").date()]] = -9.0   # midwinter thaw
    tmin[index[pd.Timestamp("2021-03-20").date()]] = -4.0
    tmin[index[pd.Timestamp("2021-04-05").date()]] = -1.0   # last spring frost
    tmin[index[pd.Timestamp("2021-06-10").date()]] = -0.5   # summer dip, still spring window
    tmin[index[pd.Timestamp("2021-09-15").date()]] = -2.0   # first fall frost
    tmin[index[pd.Timestamp("2021-10-01").date()]] = -6.0

    window = metrics.frost_window(dates, tmin)
    assert window.last_spring_frost == pd.Timestamp("2021-06-10").date()
    assert window.first_fall_frost == pd.Timestamp("2021-09-15").date()
    assert window.season_length_days == (window.first_fall_frost - window.last_spring_frost).days


def test_season_precip_respects_the_month_window():
    dates = pd.date_range("2021-01-01", "2021-12-31", freq="D")
    precip = np.ones(len(dates))
    # April through September is 30+31+30+31+31+30 = 183 days.
    assert metrics.season_precip(dates, precip) == pytest.approx(183.0)
    assert metrics.season_precip(dates, precip, start_month=1, end_month=12) == pytest.approx(365.0)


# --------------------------------------------------------------------------
# stats
# --------------------------------------------------------------------------


def test_chi_square_matches_hand_calculation():
    result = stats.chi_square([[8, 4], [4, 0]])
    assert result.n == 16
    assert result.dof == 1
    assert result.chi2 == pytest.approx(16 / 9, rel=1e-6)
    assert result.cramers_v == pytest.approx(1 / 3, rel=1e-6)
    assert 0 < result.p < 1
    assert result.min_expected == pytest.approx(1.0)


def test_chi_square_rejects_a_1d_table():
    with pytest.raises(ValueError, match="2-D table"):
        stats.chi_square([1, 2, 3])


def test_cliffs_delta_is_signed_and_bounded():
    # No ties across groups -> the full +/-1 range.
    assert stats.cliffs_delta([4, 5, 6], [1, 2, 3]) == pytest.approx(1.0)
    assert stats.cliffs_delta([1, 2, 3], [4, 5, 6]) == pytest.approx(-1.0)
    assert stats.cliffs_delta([1, 2], [2, 1]) == pytest.approx(0.0)
    # A tie is worth zero, not a win.
    assert stats.cliffs_delta([3, 4, 5], [1, 2, 3]) == pytest.approx(8 / 9)


def test_permutation_test_detects_a_clear_shift():
    result = stats.permutation_mean_difference([10, 11, 12, 13], [1, 2, 3, 4], permutations=2000)
    assert result.observed == pytest.approx(9.0)
    assert result.p < 0.05
    assert result.cliffs_delta == pytest.approx(1.0)


def test_kruskal_effect_size_is_high_for_separated_groups():
    result = stats.kruskal_groups(
        {"a": np.array([1.0, 2.0, 3.0]), "b": np.array([8.0, 9.0, 10.0]), "c": np.array([15.0, 16.0, 17.0])}
    )
    assert result.p < 0.1
    assert 0.5 <= result.epsilon_squared <= 1.0
    assert result.n == 9 and result.groups == 3


def test_spearman_bootstrap_interval_brackets_a_monotone_relation():
    x = np.arange(1, 11, dtype=float)
    y = x**2
    result = stats.spearman(x, y, bootstrap=200)
    assert result.rho == pytest.approx(1.0)
    assert result.ci_low is not None and result.ci_low <= result.rho <= result.ci_high


# --------------------------------------------------------------------------
# dataset
# --------------------------------------------------------------------------


def test_nasa_power_cell_snaps_to_the_cell_centre_half_up():
    assert dataset.nasa_power_cell(40.74) == 40.5
    assert dataset.nasa_power_cell(40.76) == 41.0
    assert dataset.nasa_power_cell(-87.76) == -88.0
    # An exact boundary resolves upward, never alternating as round() would.
    assert dataset.nasa_power_cell(41.25) == 41.5


def test_boundary_metrics_match_a_known_square():
    import geopandas as gpd
    from shapely.geometry import box

    square = gpd.GeoDataFrame(
        {"field_id": ["sq"]},
        geometry=[box(0, 0, 1000, 1000)],
        crs="EPSG:5070",
    )
    result = dataset.boundary_metrics(square)
    row = result.iloc[0]
    assert row["area_ha"] == pytest.approx(100.0)
    assert row["perimeter_km"] == pytest.approx(4.0)
    assert row["shape_index"] == pytest.approx(1.128379, rel=1e-5)
    assert row["compactness"] == pytest.approx(0.785398, rel=1e-5)
    assert row["bbox_fill_ratio"] == pytest.approx(1.0)
    assert row["vertex_count"] == 5.0


def test_portfolio_loads_fields_and_cells(runtime):
    portfolio = dataset.build_portfolio(root=runtime)
    assert len(portfolio.fields_table) == 4
    assert portfolio.growers == ["il-grower", "ne-grower"]
    assert portfolio.cell_count == 4
    assert set(portfolio.fields_table["weather_cell"]).issubset(
        {"40.5,-88.0", "41.0,-88.0", "41.0,-98.0", "41.5,-97.5"}
    )
    assert list(portfolio.fields_table.columns) == list(dataset.FIELD_TABLE_COLUMNS)


def test_missing_cdl_years_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_PIPELINE_DATA_ROOT", str(tmp_path))
    _write_farm(
        tmp_path,
        "il-grower",
        "il-farm",
        "IL",
        [("il-f1", -88.0, 40.7, "Corn", 250.0)],
        skip_cdl=True,
    )
    with pytest.raises(PortfolioError, match="expected >=5 CDL years"):
        dataset.build_portfolio(root=tmp_path)


def test_field_absent_from_weather_fails_loudly(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_PIPELINE_DATA_ROOT", str(tmp_path))
    _write_farm(
        tmp_path,
        "il-grower",
        "il-farm",
        "IL",
        [("il-f1", -88.0, 40.7, "Corn", 250.0), ("il-f2", -87.9, 40.8, "Corn", 260.0)],
        skip_weather_field="il-f2",
    )
    with pytest.raises(PortfolioError, match="missing from the weather table"):
        dataset.build_portfolio(root=tmp_path)


def test_duplicate_field_id_across_growers_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_PIPELINE_DATA_ROOT", str(tmp_path))
    for grower, state in (("a-grower", "IL"), ("b-grower", "NE")):
        _write_farm(
            tmp_path,
            grower,
            f"{grower}-farm",
            state,
            [("shared-f1", -88.0, 40.7, "Corn", 250.0)],
        )
    with pytest.raises(PortfolioError, match="not unique across growers"):
        dataset.build_portfolio(root=tmp_path)


# --------------------------------------------------------------------------
# crop history
# --------------------------------------------------------------------------


def test_crop_sequence_uses_the_dominant_class(runtime):
    portfolio = dataset.build_portfolio(root=runtime)
    assert len(portfolio.cdl) == 20  # one class per field-year in the fixture
    assert len(portfolio.sequence) == 20
    assert portfolio.sequence.groupby("field_id")["year"].nunique().eq(5).all()
    assert portfolio.sequence["dominant_pct"].between(50, 100).all()
    wide = portfolio.sequence.pivot(
        index="field_id", columns="year", values="crop_name"
    )
    assert int((wide.nunique(axis=1) > 1).sum()) == 2


def test_incomplete_crop_grid_fails_loudly(runtime):
    path = (
        runtime
        / "data-pipeline"
        / "growers"
        / "il-grower"
        / "farms"
        / "il-farm"
        / "derived"
        / "tables"
        / "il-farm_2021_cdl.csv"
    )
    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    with pytest.raises(PortfolioError, match="missing 1 field-year"):
        dataset.build_portfolio(root=runtime)


# --------------------------------------------------------------------------
# runner
# --------------------------------------------------------------------------


def test_run_writes_every_file_it_declares(runtime):
    from field_portfolio import run

    out_dir = runtime / "analysis-out"
    exit_code = run.main(
        ["--root", str(runtime), "--out-dir", str(out_dir), "--skip-validate"]
    )
    assert exit_code == 0

    analysis = json.loads((out_dir / "analysis.json").read_text(encoding="utf-8"))
    manifest = json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))

    assert analysis["dataset"]["fields"] == 4
    assert [figure["id"] for figure in analysis["figures"]] == [
        "m0_portfolio_overview",
        "b1_area_distribution",
        "b2_area_vs_shape",
        "b3_geometry_correlation",
        "m1_fields_by_grower",
    ]
    overview_stat = analysis["figures"][0]["stat"]
    assert overview_stat["crs"] == "EPSG:5070"
    assert overview_stat["states_source"] == "unavailable"
    assert overview_stat["graticule_lines"] > 0
    for record in analysis["figures"]:
        assert (out_dir / record["file"]).exists()
        assert record["n"] == 4
        assert record["caveat"]

    for entry in manifest["outputs"]:
        assert (runtime / "data-pipeline" / entry["path_rel"]).exists() or (
            out_dir / Path(entry["path_rel"]).name
        ).exists()
    assert manifest["validation"]["status"] == "skipped"
    assert manifest["inputs"], "manifest must hash the inputs it read"
    assert all("sha256" in entry and entry["path_rel"] for entry in manifest["inputs"])

    table = pd.read_csv(out_dir / "tables" / "table_fields.csv")
    assert len(table) == 4
    assert table["area_ha"].gt(0).all()
    assert table["weather_cell"].notna().all()


def test_cdl_stage_writes_three_records(runtime):
    from field_portfolio import run

    out_dir = runtime / "cdl-out"
    exit_code = run.main(
        [
            "--root",
            str(runtime),
            "--out-dir",
            str(out_dir),
            "--stage",
            "cdl",
            "--skip-validate",
        ]
    )
    assert exit_code == 0

    analysis = json.loads((out_dir / "analysis.json").read_text(encoding="utf-8"))
    assert [figure["id"] for figure in analysis["figures"]] == [
        "c1_crop_mix_by_year",
        "c2_crop_sequence",
        "c3_crop_transitions",
    ]
    assert [figure["n"] for figure in analysis["figures"]] == [20, 4, 16]
    for record in analysis["figures"]:
        assert (out_dir / record["file"]).exists()
        assert record["caveat"]
        assert record["category"] == "crop history"


def test_run_refuses_an_unbuilt_stage(runtime):
    from field_portfolio import run

    assert run.main(["--root", str(runtime), "--stage", "weather"]) == 2
