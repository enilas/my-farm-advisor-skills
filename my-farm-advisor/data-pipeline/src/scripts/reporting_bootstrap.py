"""Helpers for loading local skill modules and canonical data-tree scaffolding."""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from pathlib import Path

from lib.runtime_paths import resolve_runtime_paths


_RUNTIME_PATHS = resolve_runtime_paths()
RUNTIME_BASE = _RUNTIME_PATHS.runtime_base
RUNTIME_SOURCE = _RUNTIME_PATHS.runtime_source
SOURCE_LOCATOR_PATH = RUNTIME_BASE / ".my-farm-advisor-source.json"
DATA_ROOT = RUNTIME_BASE
GROWERS_ROOT = DATA_ROOT / "growers"
SHARED_ROOT = DATA_ROOT / "shared"
DEFAULT_GROWER = os.environ.get("AG_GROWER_SLUG", "default-grower")
DEFAULT_FARM = os.environ.get("AG_FARM_SLUG", "default-farm")
DEFAULT_FARM_NAME = os.environ.get("AG_FARM_NAME", "Default Farm")
DEFAULT_FARM_STATE = os.environ.get("AG_FARM_STATE", "")
# "Default Farm" is the unset sentinel: it must never replace a real farm name.
_UNSET_FARM_NAME = "Default Farm"
DEFAULT_INVENTORY = Path(
    os.environ.get(
        "AG_INVENTORY_CSV",
        str(
            GROWERS_ROOT
            / DEFAULT_GROWER
            / "farms"
            / DEFAULT_FARM
            / "manifests"
            / "field-inventory.csv"
        ),
    )
)


def _candidate_skill_roots() -> list[Path]:
    roots: list[Path] = [RUNTIME_SOURCE]

    locator_root = _source_locator_skill_root()
    if locator_root is not None:
        roots.append(locator_root)

    for env_name in ("MY_FARM_ADVISOR_SKILL_ROOT", "MY_FARM_ADVISOR_ROOT"):
        raw = os.environ.get(env_name)
        if raw:
            roots.append(Path(raw).expanduser().resolve(strict=False))

    install_script = os.environ.get("DATA_PIPELINE_INSTALL_SCRIPT")
    if install_script:
        install_path = Path(install_script).expanduser().resolve(strict=False)
        if len(install_path.parents) >= 3:
            roots.append(install_path.parents[2])

    checkout_root = Path(__file__).resolve().parents[3]
    if (checkout_root / "SKILL.md").exists():
        roots.append(checkout_root)

    unique_roots: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        if root in seen:
            continue
        seen.add(root)
        unique_roots.append(root)
    return unique_roots


def _source_locator_skill_root() -> Path | None:
    if not SOURCE_LOCATOR_PATH.exists():
        return None
    try:
        payload = json.loads(SOURCE_LOCATOR_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = payload.get("my_farm_advisor_skill_root")
    if not isinstance(raw, str) or not raw.strip():
        return None
    candidate = Path(raw).expanduser().resolve(strict=False)
    if not (candidate / "SKILL.md").exists():
        return None
    return candidate


def ensure_skill_path(skill_name: str) -> Path:
    matches: list[Path] = []
    for root in _candidate_skill_roots():
        direct = root / skill_name / "src"
        if direct.exists():
            matches.append(direct)
        matches.extend(sorted(root.glob(f"**/{skill_name}/src")) if root.exists() else [])

    if not matches:
        searched = ", ".join(str(root) for root in _candidate_skill_roots())
        raise FileNotFoundError(
            f"Skill source path not found for '{skill_name}' under: {searched}"
        )

    skill_path = matches[0]
    resolved = str(skill_path)
    if resolved not in sys.path:
        sys.path.insert(0, resolved)
    return skill_path


def farm_root(grower_slug: str = DEFAULT_GROWER, farm_slug: str = DEFAULT_FARM) -> Path:
    return GROWERS_ROOT / grower_slug / "farms" / farm_slug


def fields_root(
    grower_slug: str = DEFAULT_GROWER, farm_slug: str = DEFAULT_FARM
) -> Path:
    return farm_root(grower_slug, farm_slug) / "fields"


def field_slugs_from_inventory(inventory_path: Path | None = None) -> list[str]:
    inventory = inventory_path or DEFAULT_INVENTORY
    inventory = inventory if inventory.is_absolute() else DATA_ROOT / inventory
    if not inventory.exists():
        return []
    rows = inventory.read_text(encoding="utf-8").splitlines()
    slugs: list[str] = []
    for line in rows[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) != 2:
            continue
        slugs.append(parts[1].strip())
    return slugs


def field_slug_map_from_inventory(inventory_path: Path | None = None) -> dict[str, str]:
    inventory = inventory_path or DEFAULT_INVENTORY
    inventory = inventory if inventory.is_absolute() else DATA_ROOT / inventory
    if not inventory.exists():
        return {}
    rows = inventory.read_text(encoding="utf-8").splitlines()
    mapping: dict[str, str] = {}
    for line in rows[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split(",")
        if len(parts) != 2:
            continue
        mapping[parts[0].strip()] = parts[1].strip()
    return mapping


def _write_json(path: Path, payload: dict, *, overwrite: bool = True) -> None:
    if path.exists() and not overwrite:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_text(path: Path, text: str, *, overwrite: bool = True) -> None:
    if path.exists() and not overwrite:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _read_json(path: Path) -> dict:
    """Best-effort dict load; unreadable or non-object payloads read as empty."""
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _merge_json(path: Path, updates: dict, *, drop: tuple[str, ...] = ()) -> dict:
    """Merge *updates* into an existing JSON object instead of replacing it.

    Canonical metadata files are shared surfaces: wholesale rewrites silently
    discard operator-set display names and provenance notes. Missing keys are
    created; keys not named in *updates* are preserved; *drop* removes keys that
    would otherwise go stale.
    """
    payload = _read_json(path)
    payload.update(updates)
    for key in drop:
        payload.pop(key, None)
    _write_json(path, payload)
    return payload


def _clean_state(value: object) -> str | None:
    """Normalize a two-letter USPS state code, or return None."""
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    if len(code) == 2 and code.isascii() and code.isalpha():
        return code
    return None


def derive_boundary_admin(boundary_path: Path) -> dict:
    """Derive ``state``/``state_fips``/``counties`` from a farm boundary file.

    Boundaries are written by the ingest legs and carry the authoritative
    per-field ``state``, ``state_fips`` and ``county`` properties. This keeps
    ``farm.json`` consistent with the geometry instead of a hardcoded literal.
    """
    payload = _read_json(boundary_path)
    features = payload.get("features")
    if not isinstance(features, list):
        return {}

    states: Counter[str] = Counter()
    fips: Counter[str] = Counter()
    counties: set[str] = set()
    for feature in features:
        if not isinstance(feature, dict):
            continue
        props = feature.get("properties")
        if not isinstance(props, dict):
            continue
        code = _clean_state(props.get("state"))
        if code:
            states[code] += 1
        raw_fips = props.get("state_fips")
        if raw_fips is not None and str(raw_fips).strip():
            fips[str(raw_fips).strip().zfill(2)] += 1
        county = props.get("county")
        if isinstance(county, str) and county.strip():
            counties.add(county.strip())

    derived: dict = {}
    if states:
        # Majority wins; ties break alphabetically for a deterministic result.
        derived["state"] = sorted(states.items(), key=lambda item: (-item[1], item[0]))[0][0]
        if len(states) > 1:
            derived["state_conflict"] = sorted(states)
    if fips:
        derived["state_fips"] = sorted(fips.items(), key=lambda item: (-item[1], item[0]))[0][0]
    if counties:
        derived["counties"] = sorted(counties)
    return derived


def _prefer_display_name(existing: object, requested: str) -> str:
    """Keep a real existing name unless the caller explicitly passed a new one."""
    if requested and requested != _UNSET_FARM_NAME:
        return requested
    if isinstance(existing, str) and existing.strip():
        return existing.strip()
    return requested or _UNSET_FARM_NAME


def sync_farm_metadata(
    grower_slug: str,
    farm_slug: str,
    farm_name: str | None = None,
    state: str | None = None,
) -> dict:
    """Reconcile ``farm.json`` with the farm boundary geometry.

    State resolution order: explicit argument, ``AG_FARM_STATE``, boundary
    properties, then the value already on disk. There is deliberately no
    hardcoded default -- a wrong literal silently mislabels every non-Iowa
    farm. The merge preserves operator-set display names and extra keys.
    """
    farm = farm_root(grower_slug, farm_slug)
    boundary_admin = derive_boundary_admin(farm / "boundary" / "field_boundaries.geojson")
    farm_payload = _read_json(farm / "farm.json")
    resolved_state = (
        _clean_state(state)
        or _clean_state(DEFAULT_FARM_STATE)
        or _clean_state(boundary_admin.get("state"))
        or _clean_state(farm_payload.get("state"))
    )
    requested_name = farm_name if farm_name is not None else DEFAULT_FARM_NAME
    updates: dict = {
        "grower_slug": grower_slug,
        "farm_slug": farm_slug,
        "display_name": _prefer_display_name(farm_payload.get("display_name"), requested_name),
        "country": farm_payload.get("country") or "US",
        "default_crs": farm_payload.get("default_crs") or "EPSG:4326",
        "notes": farm_payload.get("notes") or "Canonical farm metadata",
    }
    drop: list[str] = []
    if resolved_state:
        updates["state"] = resolved_state
        if (
            _clean_state(boundary_admin.get("state")) == resolved_state
            and boundary_admin.get("state_fips")
        ):
            updates["state_fips"] = boundary_admin["state_fips"]
        elif _clean_state(farm_payload.get("state")) != resolved_state:
            # The recorded FIPS described a different state; drop it rather
            # than keep a value that contradicts the resolved state.
            drop.append("state_fips")
    if boundary_admin.get("counties"):
        updates["counties"] = boundary_admin["counties"]
    if boundary_admin.get("state_conflict"):
        updates["state_conflict"] = boundary_admin["state_conflict"]
    return _merge_json(farm / "farm.json", updates, drop=tuple(drop))


def ensure_canonical_field_artifacts(
    field_slug: str, grower_slug: str = DEFAULT_GROWER, farm_slug: str = DEFAULT_FARM
) -> None:
    base = fields_root(grower_slug, farm_slug) / field_slug
    for rel in (
        "boundary",
        "soil",
        "weather",
        "satellite/landsat",
        "satellite/sentinel",
        "derived/tables",
        "derived/features",
        "derived/summaries",
        "derived/reports",
        "manifests",
        "logs",
    ):
        (base / rel).mkdir(parents=True, exist_ok=True)

    field_geojson = {
        "type": "FeatureCollection",
        "features": [],
    }
    field_payload = _read_json(base / "field.json")
    existing_field_name = field_payload.get("display_name")
    field_display = (
        existing_field_name.strip()
        if isinstance(existing_field_name, str) and existing_field_name.strip()
        else field_slug
    )
    _merge_json(
        base / "field.json",
        {
            "grower_slug": grower_slug,
            "farm_slug": farm_slug,
            "field_slug": field_slug,
            "display_name": field_display,
            # Canonical data ID: matches boundaries, weather, CDL, inventory,
            # and the field directory name. Do not uppercase or underscore it.
            "field_id": field_slug,
            "boundary_file": "boundary/field_boundary.geojson",
            "soil_file": "soil/ssurgo_soil_types.geojson",
            "weather_file": "weather/daily_weather.csv",
            "satellite_dir": "satellite/",
            "notes": field_payload.get("notes") or "Canonical field metadata",
        },
    )
    _write_json(
        base / "boundary" / "field_boundary.geojson", field_geojson, overwrite=False
    )
    _write_json(
        base / "soil" / "ssurgo_soil_types.geojson", field_geojson, overwrite=False
    )
    _write_text(
        base / "weather" / "daily_weather.csv",
        "field_id,date,T2M,T2M_MAX,T2M_MIN,PRECTOTCORR,ALLSKY_SFC_SW_DWN,RH2M,WS10M\n",
        overwrite=False,
    )

    _write_json(
        base / "soil" / "metadata.json",
        {
            "dataset_name": "ssurgo_soil_types",
            "source": "usda-sda",
            "spatial_scope": "field",
            "grower_slug": grower_slug,
            "farm_slug": farm_slug,
            "field_slug": field_slug,
            "format": "geojson",
            "crs": "EPSG:4326",
        },
        overwrite=False,
    )
    _write_json(
        base / "weather" / "metadata.json",
        {
            "dataset_name": "daily_weather",
            "source": "nasa-power",
            "spatial_scope": "field",
            "grower_slug": grower_slug,
            "farm_slug": farm_slug,
            "field_slug": field_slug,
            "format": "csv",
            "crs": "EPSG:4326",
        },
        overwrite=False,
    )

    _write_json(
        base / "satellite" / "landsat" / "manifest.json",
        {
            "dataset_name": "landsat",
            "field_slug": field_slug,
            "years": [],
        },
        overwrite=False,
    )
    _write_json(
        base / "satellite" / "sentinel" / "manifest.json",
        {
            "dataset_name": "sentinel",
            "field_slug": field_slug,
            "years": [],
        },
        overwrite=False,
    )
    _write_text(base / "logs" / "pipeline_runs.jsonl", "", overwrite=False)


def ensure_canonical_data_tree(
    grower_slug: str = DEFAULT_GROWER,
    farm_slug: str = DEFAULT_FARM,
    farm_name: str = DEFAULT_FARM_NAME,
    inventory_path: Path | None = None,
    include_farm: bool = True,
    state: str | None = None,
) -> list[str]:
    (DATA_ROOT / "shared" / "weather").mkdir(parents=True, exist_ok=True)
    (DATA_ROOT / "shared" / "geoadmin" / "l0_countries" / "raw").mkdir(
        parents=True, exist_ok=True
    )
    (DATA_ROOT / "shared" / "geoadmin" / "l1_states" / "raw").mkdir(
        parents=True, exist_ok=True
    )
    (DATA_ROOT / "shared" / "geoadmin" / "l2_counties" / "raw").mkdir(
        parents=True, exist_ok=True
    )
    (DATA_ROOT / "shared" / "corn_maturity" / "tables").mkdir(
        parents=True, exist_ok=True
    )
    (DATA_ROOT / "shared" / "corn_maturity" / "reports").mkdir(
        parents=True, exist_ok=True
    )
    (DATA_ROOT / "shared" / "corn_maturity" / "metadata").mkdir(
        parents=True, exist_ok=True
    )
    (DATA_ROOT / "shared" / "corn_maturity" / "manifests").mkdir(
        parents=True, exist_ok=True
    )
    (DATA_ROOT / "shared" / "corn_maturity" / "logs").mkdir(parents=True, exist_ok=True)
    (DATA_ROOT / "shared" / "soybean_maturity" / "tables").mkdir(
        parents=True, exist_ok=True
    )
    (DATA_ROOT / "shared" / "soybean_maturity" / "reports").mkdir(
        parents=True, exist_ok=True
    )
    (DATA_ROOT / "shared" / "soybean_maturity" / "metadata").mkdir(
        parents=True, exist_ok=True
    )
    (DATA_ROOT / "shared" / "soybean_maturity" / "manifests").mkdir(
        parents=True, exist_ok=True
    )
    (DATA_ROOT / "shared" / "soybean_maturity" / "logs").mkdir(
        parents=True, exist_ok=True
    )
    (DATA_ROOT / "shared" / "cdl" / "metadata").mkdir(parents=True, exist_ok=True)
    (DATA_ROOT / "shared" / "cdl" / "rasters").mkdir(parents=True, exist_ok=True)
    (DATA_ROOT / "shared" / "cdl" / "derived").mkdir(parents=True, exist_ok=True)
    (DATA_ROOT / "shared" / "cdl" / "derived" / "tables").mkdir(
        parents=True, exist_ok=True
    )
    (DATA_ROOT / "shared" / "cdl" / "derived" / "reports").mkdir(
        parents=True, exist_ok=True
    )
    (DATA_ROOT / "shared" / "cdl" / "manifests").mkdir(parents=True, exist_ok=True)
    (DATA_ROOT / "shared" / "cdl" / "logs").mkdir(parents=True, exist_ok=True)
    (DATA_ROOT / "shared" / "reference" / "crop_codes").mkdir(
        parents=True, exist_ok=True
    )
    (DATA_ROOT / "shared" / "reference" / "units").mkdir(parents=True, exist_ok=True)
    (DATA_ROOT / "shared" / "reference" / "schemas").mkdir(parents=True, exist_ok=True)
    (DATA_ROOT / "shared" / "manifests").mkdir(parents=True, exist_ok=True)
    (DATA_ROOT / "shared" / "logs").mkdir(parents=True, exist_ok=True)
    _write_json(
        DATA_ROOT / "shared" / "geoadmin" / "l0_countries" / "metadata.json",
        {
            "dataset_name": "geoadmin_countries",
            "source": "natural-earth",
            "spatial_scope": "global",
            "format": "geojson-or-parquet",
            "notes": "Shared Level 0 admin root for annual maturity and geoadmin workflows",
        },
        overwrite=False,
    )
    _write_json(
        DATA_ROOT / "shared" / "geoadmin" / "l1_states" / "metadata.json",
        {
            "dataset_name": "geoadmin_states",
            "source": "census-tiger-line",
            "spatial_scope": "usa-state",
            "format": "geojson-or-parquet",
            "notes": "Shared Level 1 admin root for annual maturity and geoadmin workflows",
        },
        overwrite=False,
    )
    _write_json(
        DATA_ROOT / "shared" / "geoadmin" / "l2_counties" / "metadata.json",
        {
            "dataset_name": "geoadmin_counties",
            "source": "census-tiger-line",
            "spatial_scope": "usa-county",
            "format": "geojson-or-parquet",
            "notes": "Shared county and FIPS root for annual maturity and geoadmin workflows",
        },
        overwrite=False,
    )
    _write_json(
        DATA_ROOT / "shared" / "corn_maturity" / "metadata" / "dataset.json",
        {
            "dataset_name": "corn_maturity",
            "status": "planned",
            "notes": "Annual heuristic corn RM and GDD outputs by FIPS live here",
        },
        overwrite=False,
    )
    _write_json(
        DATA_ROOT / "shared" / "soybean_maturity" / "metadata" / "dataset.json",
        {
            "dataset_name": "soybean_maturity",
            "status": "planned",
            "notes": "Annual heuristic soybean maturity-group outputs by FIPS live here",
        },
        overwrite=False,
    )

    if not include_farm:
        return []

    grower = DATA_ROOT / "growers" / grower_slug
    grower.mkdir(parents=True, exist_ok=True)
    (grower / "manifests").mkdir(parents=True, exist_ok=True)
    (grower / "logs").mkdir(parents=True, exist_ok=True)
    grower_payload = _read_json(grower / "grower.json")
    existing_grower_name = grower_payload.get("display_name")
    grower_display = (
        existing_grower_name.strip()
        if isinstance(existing_grower_name, str) and existing_grower_name.strip()
        else grower_slug
    )
    _merge_json(
        grower / "grower.json",
        {
            "grower_slug": grower_slug,
            "display_name": grower_display,
            "notes": grower_payload.get("notes") or "Canonical grower metadata",
        },
    )
    _write_text(grower / "logs" / "pipeline_runs.jsonl", "", overwrite=False)

    farm = farm_root(grower_slug, farm_slug)
    farm.mkdir(parents=True, exist_ok=True)
    (farm / "boundary").mkdir(parents=True, exist_ok=True)
    (farm / "manifests").mkdir(parents=True, exist_ok=True)
    (farm / "logs").mkdir(parents=True, exist_ok=True)
    (farm / "derived" / "reports").mkdir(parents=True, exist_ok=True)
    (farm / "derived" / "summaries").mkdir(parents=True, exist_ok=True)
    (farm / "derived" / "dashboards").mkdir(parents=True, exist_ok=True)
    (farm / "derived" / "tables").mkdir(parents=True, exist_ok=True)

    # State resolution order: explicit argument, AG_FARM_STATE, boundary
    # geometry, then the value already on disk. Never a hardcoded literal --
    # a wrong default silently mislabels every non-Iowa farm.
    sync_farm_metadata(grower_slug, farm_slug, farm_name=farm_name, state=state)
    _write_text(farm / "logs" / "pipeline_runs.jsonl", "", overwrite=False)

    slugs = field_slugs_from_inventory(inventory_path=inventory_path)
    if not slugs:
        root = fields_root(grower_slug, farm_slug)
        if root.exists():
            slugs = sorted([p.name for p in root.iterdir() if p.is_dir()])

    for slug in slugs:
        ensure_canonical_field_artifacts(slug, grower_slug, farm_slug)

    return slugs
