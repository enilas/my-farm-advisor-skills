#!/usr/bin/env python3
"""Normalize canonical grower/farm/field metadata against boundary geometry.

Why this exists
---------------
``farm.json`` used to be written with a hardcoded ``"state": "IA"`` and
``field.json`` used to uppercase its ``field_id`` (``IL_F1``) while every data
table keyed on the lowercase slug (``il-f1``). Both were writer bugs; the
writers are fixed, and this tool repairs trees that were produced before the
fix. It is idempotent: run it twice and the second run reports no changes.

Rules
-----
* ``state`` / ``state_fips`` / ``counties`` come from the farm boundary file,
  which carries the per-feature properties written by the ingest legs.
* ``display_name`` and ``notes`` are never invented or downgraded.
* ``field.json.field_id`` becomes the canonical lowercase-hyphen field slug.
* Extra keys in any metadata file are preserved.

Usage
-----
    python normalize_dataset_metadata.py --root /path/to/runtime        # dry run
    python normalize_dataset_metadata.py --root /path/to/runtime --apply

Exit status is 0 when the tree is (or became) consistent, 2 when a farm cannot
be normalized -- missing boundary file, zero boundary features, or a boundary
with no usable state property and no state on disk.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        default=None,
        help="runtime data root (default: $DATA_PIPELINE_DATA_ROOT)",
    )
    parser.add_argument(
        "--grower",
        action="append",
        default=None,
        help="grower slug to normalize; repeat for several (default: all)",
    )
    parser.add_argument(
        "--farm",
        action="append",
        default=None,
        help="farm slug to normalize; repeat for several (default: all)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the changes (default is a dry run)",
    )
    return parser.parse_args(argv)


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _state(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    code = value.strip().upper()
    if len(code) == 2 and code.isascii() and code.isalpha():
        return code
    return None


def _collect_farms(
    growers_root: Path, growers: list[str] | None, farms: list[str] | None
) -> list[tuple[str, str, Path]]:
    selected: list[tuple[str, str, Path]] = []
    if not growers_root.is_dir():
        return selected
    for grower_dir in sorted(p for p in growers_root.iterdir() if p.is_dir()):
        if growers and grower_dir.name not in growers:
            continue
        farms_dir = grower_dir / "farms"
        if not farms_dir.is_dir():
            continue
        for farm_dir in sorted(p for p in farms_dir.iterdir() if p.is_dir()):
            if farms and farm_dir.name not in farms:
                continue
            selected.append((grower_dir.name, farm_dir.name, farm_dir))
    return selected


def _desired_farm(grower_slug: str, farm_slug: str, before: dict, admin: dict) -> dict:
    after = dict(before)
    after["grower_slug"] = grower_slug
    after["farm_slug"] = farm_slug
    after.setdefault("display_name", farm_slug)
    after.setdefault("country", "US")
    after.setdefault("default_crs", "EPSG:4326")
    after.setdefault("notes", "Canonical farm metadata")

    boundary_state = _state(admin.get("state"))
    state = boundary_state or _state(before.get("state"))
    previous_state = _state(before.get("state"))
    if state:
        after["state"] = state
    if boundary_state and admin.get("state_fips") and state == boundary_state:
        after["state_fips"] = admin["state_fips"]
    elif state != previous_state:
        # FIPS described a different state; keeping it would be a lie.
        after.pop("state_fips", None)
    if admin.get("counties"):
        after["counties"] = admin["counties"]
    if admin.get("state_conflict"):
        after["state_conflict"] = admin["state_conflict"]
    else:
        after.pop("state_conflict", None)
    return after


def _desired_field(field_slug: str, before: dict) -> dict:
    after = dict(before)
    after["field_slug"] = field_slug
    after["field_id"] = field_slug
    if not isinstance(after.get("display_name"), str) or not after["display_name"].strip():
        after["display_name"] = field_slug
    return after


def _diff(before: dict, after: dict) -> tuple[dict, list[str]]:
    changed = {
        key: (before.get(key), value)
        for key, value in after.items()
        if before.get(key) != value
    }
    removed = [key for key in before if key not in after]
    return changed, removed


def _render_diff(label: str, before: dict, after: dict) -> list[str]:
    changed, removed = _diff(before, after)
    lines: list[str] = []
    for key, (old, new) in changed.items():
        lines.append(f"    {label}: {key} {old!r} -> {new!r}")
    for key in removed:
        lines.append(f"    {label}: -{key} (removed)")
    return lines


def _normalize_farm(
    grower_slug: str, farm_slug: str, farm_dir: Path, *, apply: bool
) -> tuple[list[str], list[str]]:
    """Return (change lines, error lines) for one farm."""
    from reporting_bootstrap import derive_boundary_admin

    changes: list[str] = []
    errors: list[str] = []
    prefix = f"growers/{grower_slug}/farms/{farm_slug}"

    boundary_path = farm_dir / "boundary" / "field_boundaries.geojson"
    admin = derive_boundary_admin(boundary_path)
    if not boundary_path.exists():
        errors.append(f"{prefix}: missing boundary file {boundary_path}")
    elif not admin:
        errors.append(
            f"{prefix}: boundary has no usable state/state_fips/county properties"
        )

    farm_json = farm_dir / "farm.json"
    before = _read_json(farm_json)
    after = _desired_farm(grower_slug, farm_slug, before, admin)
    if not _state(after.get("state")):
        errors.append(
            f"{prefix}: state unresolved (boundary and farm.json both lack a state)"
        )
    lines = _render_diff("farm.json", before, after)
    if lines:
        changes.extend(lines)
        if apply:
            _write_json(farm_json, after)

    fields_dir = farm_dir / "fields"
    field_dirs = (
        sorted(p for p in fields_dir.iterdir() if p.is_dir()) if fields_dir.is_dir() else []
    )
    for field_dir in field_dirs:
        field_json = field_dir / "field.json"
        f_before = _read_json(field_json)
        f_after = _desired_field(field_dir.name, f_before)
        f_lines = _render_diff(f"fields/{field_dir.name}/field.json", f_before, f_after)
        if f_lines:
            changes.extend(f_lines)
            if apply:
                _write_json(field_json, f_after)

    return changes, errors


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.root:
        root = Path(args.root).expanduser().resolve(strict=False)
        os.environ["DATA_PIPELINE_DATA_ROOT"] = str(root)

    scripts_dir = Path(__file__).resolve().parents[1]
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    try:
        from lib.paths import GROWERS_ROOT
    except Exception as exc:  # pragma: no cover - operator-facing guard
        print(f"error: cannot load pipeline libraries: {exc}", file=sys.stderr)
        return 2

    farms = _collect_farms(GROWERS_ROOT, args.grower, args.farm)
    if not farms:
        print(f"no farms found under {GROWERS_ROOT}", file=sys.stderr)
        return 2

    total_changes = 0
    all_errors: list[str] = []
    for grower_slug, farm_slug, farm_dir in farms:
        changes, errors = _normalize_farm(
            grower_slug, farm_slug, farm_dir, apply=args.apply
        )
        total_changes += len(changes)
        all_errors.extend(errors)
        header = f"{grower_slug}/{farm_slug}"
        if changes or errors:
            print(header)
            for line in changes:
                print(line)
            for line in errors:
                print(f"    ERROR: {line}")
        else:
            print(f"{header}: ok")

    # grower.json is farm-independent; normalize here so one pass covers all.
    growers_root = GROWERS_ROOT
    if growers_root.is_dir():
        for grower_dir in sorted(p for p in growers_root.iterdir() if p.is_dir()):
            if args.grower and grower_dir.name not in args.grower:
                continue
            grower_json = grower_dir / "grower.json"
            before = _read_json(grower_json)
            after = dict(before)
            after["grower_slug"] = grower_dir.name
            if not isinstance(after.get("display_name"), str) or not after["display_name"].strip():
                after["display_name"] = grower_dir.name
            after.setdefault("notes", "Canonical grower metadata")
            lines = _render_diff(f"growers/{grower_dir.name}/grower.json", before, after)
            if lines:
                print(f"growers/{grower_dir.name}")
                for line in lines:
                    print(line)
                total_changes += len(lines)
                if args.apply:
                    _write_json(grower_json, after)

    mode = "applied" if args.apply else "dry run"
    print(f"\n{mode}: {total_changes} change(s) across {len(farms)} farm(s)")
    if total_changes and not args.apply:
        print("re-run with --apply to write these changes")
    if all_errors:
        print(f"{len(all_errors)} farm(s) need manual attention", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
