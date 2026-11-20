"""Offline tests for canonical metadata writes in reporting_bootstrap.

Covers the defects found after the Assignment 2 field expansion:

* ``farm.json`` was written with a hardcoded ``"state": "IA"``, so Illinois and
  Nebraska farms were mislabelled.
* A rerun without ``AG_FARM_NAME`` replaced real display names with
  ``"Default Farm"``.
* ``field.json`` wrote an uppercased, underscored ``field_id`` (``IL_F1``)
  while every data table keyed on the lowercase slug (``il-f1``).

Run: python -m pytest src/scripts/tests/test_reporting_bootstrap.py -q
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
ENV_KEYS = (
    "AG_GROWER_SLUG",
    "AG_FARM_SLUG",
    "AG_FARM_NAME",
    "AG_FARM_STATE",
    "AG_INVENTORY_CSV",
)


def _feature(field_id: str, state: str, state_fips: str, county: str) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "field_id": field_id,
            "state": state,
            "state_fips": state_fips,
            "county": county,
        },
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[-88.0, 40.0], [-87.9, 40.0], [-87.9, 40.1], [-88.0, 40.1], [-88.0, 40.0]]
            ],
        },
    }


def _write_boundary(root: Path, grower: str, farm: str, features: list[dict]) -> Path:
    path = root / "data-pipeline" / "growers" / grower / "farms" / farm / "boundary" / "field_boundaries.geojson"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
    )
    return path


def _write_farm_json(root: Path, grower: str, farm: str, payload: dict) -> Path:
    path = root / "data-pipeline" / "growers" / grower / "farms" / farm / "farm.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def bootstrap(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_PIPELINE_DATA_ROOT", str(tmp_path))
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    sys.modules.pop("reporting_bootstrap", None)
    module = importlib.import_module("reporting_bootstrap")
    yield module, tmp_path
    sys.modules.pop("reporting_bootstrap", None)


def test_state_derived_from_boundary_instead_of_hardcoded_iowa(bootstrap):
    module, root = bootstrap
    _write_boundary(
        root,
        "il-grower",
        "il-grower-illinois",
        [
            _feature("il-f1", "IL", "17", "Iroquois"),
            _feature("il-f2", "IL", "17", "Ford"),
        ],
    )

    module.ensure_canonical_data_tree(
        grower_slug="il-grower", farm_slug="il-grower-illinois"
    )

    payload = _read(
        root / "data-pipeline/growers/il-grower/farms/il-grower-illinois/farm.json"
    )
    assert payload["state"] == "IL"
    assert payload["state_fips"] == "17"
    assert payload["counties"] == ["Ford", "Iroquois"]


def test_boundary_state_heals_a_wrong_recorded_state(bootstrap):
    module, root = bootstrap
    _write_boundary(
        root, "ne-grower", "ne-grower-nebraska", [_feature("ne-f1", "NE", "31", "York")]
    )
    _write_farm_json(
        root,
        "ne-grower",
        "ne-grower-nebraska",
        {"state": "IA", "state_fips": "19", "display_name": "Nebraska Demo Farm"},
    )

    module.ensure_canonical_data_tree(
        grower_slug="ne-grower", farm_slug="ne-grower-nebraska"
    )

    payload = _read(
        root / "data-pipeline/growers/ne-grower/farms/ne-grower-nebraska/farm.json"
    )
    assert payload["state"] == "NE"
    assert payload["state_fips"] == "31"
    assert payload["display_name"] == "Nebraska Demo Farm"


def test_explicit_state_argument_wins_over_boundary(bootstrap):
    module, root = bootstrap
    _write_boundary(
        root, "il-grower", "il-grower-illinois", [_feature("il-f1", "IL", "17", "Iroquois")]
    )

    module.ensure_canonical_data_tree(
        grower_slug="il-grower", farm_slug="il-grower-illinois", state="IA"
    )

    payload = _read(
        root / "data-pipeline/growers/il-grower/farms/il-grower-illinois/farm.json"
    )
    assert payload["state"] == "IA"
    # The recorded FIPS would describe Illinois; it must not survive.
    assert "state_fips" not in payload


def test_real_display_name_survives_a_default_rerun(bootstrap):
    module, root = bootstrap
    _write_boundary(
        root, "il-grower", "il-grower-illinois", [_feature("il-f1", "IL", "17", "Iroquois")]
    )
    _write_farm_json(
        root,
        "il-grower",
        "il-grower-illinois",
        {"display_name": "Illinois Demo Farm", "state": "IL"},
    )

    module.ensure_canonical_data_tree(
        grower_slug="il-grower", farm_slug="il-grower-illinois"
    )

    payload = _read(
        root / "data-pipeline/growers/il-grower/farms/il-grower-illinois/farm.json"
    )
    assert payload["display_name"] == "Illinois Demo Farm"


def test_farm_without_derivable_state_does_not_invent_one(bootstrap):
    module, root = bootstrap
    _write_boundary(
        root,
        "xx-grower",
        "xx-farm",
        [
            {
                "type": "Feature",
                "properties": {"field_id": "xx-f1"},
                "geometry": {"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 0]]]},
            }
        ],
    )

    module.ensure_canonical_data_tree(grower_slug="xx-grower", farm_slug="xx-farm")

    payload = _read(root / "data-pipeline/growers/xx-grower/farms/xx-farm/farm.json")
    assert "state" not in payload


def test_existing_uppercase_field_id_is_healed_in_place(bootstrap):
    module, root = bootstrap
    farm_dir = root / "data-pipeline/growers/xx-grower/farms/xx-farm"
    field_dir = farm_dir / "fields/xx-f1"
    field_dir.mkdir(parents=True, exist_ok=True)
    (field_dir / "field.json").write_text(
        json.dumps({"field_id": "XX_F1", "custom_key": "keep-me"}), encoding="utf-8"
    )

    module.ensure_canonical_field_artifacts("xx-f1", "xx-grower", "xx-farm")

    payload = _read(field_dir / "field.json")
    assert payload["field_id"] == "xx-f1"
    assert payload["custom_key"] == "keep-me"

def test_field_json_uses_canonical_slug(bootstrap):
    module, root = bootstrap
    farm_dir = root / "data-pipeline/growers/ia-grower/farms/ia-grower-iowa"
    (farm_dir / "manifests").mkdir(parents=True, exist_ok=True)
    (farm_dir / "manifests/field-inventory.csv").write_text(
        "field_id,field_slug\nia-f1,ia-f1\nia-f10,ia-f10\n", encoding="utf-8"
    )
    _write_boundary(
        root,
        "ia-grower",
        "ia-grower-iowa",
        [_feature("ia-f1", "IA", "19", "Kossuth"), _feature("ia-f10", "IA", "19", "Humboldt")],
    )

    module.ensure_canonical_data_tree(
        grower_slug="ia-grower",
        farm_slug="ia-grower-iowa",
        inventory_path=farm_dir / "manifests/field-inventory.csv",
    )

    for slug in ("ia-f1", "ia-f10"):
        payload = _read(farm_dir / "fields" / slug / "field.json")
        assert payload["field_id"] == slug
        assert payload["field_slug"] == slug


def test_grower_display_name_survives(bootstrap):
    module, root = bootstrap
    grower_json = root / "data-pipeline/growers/ia-grower/grower.json"
    grower_json.parent.mkdir(parents=True, exist_ok=True)
    grower_json.write_text(json.dumps({"display_name": "Iowa Grower"}), encoding="utf-8")
    _write_boundary(
        root, "ia-grower", "ia-grower-iowa", [_feature("ia-f1", "IA", "19", "Kossuth")]
    )

    module.ensure_canonical_data_tree(grower_slug="ia-grower", farm_slug="ia-grower-iowa")

    assert _read(grower_json)["display_name"] == "Iowa Grower"
