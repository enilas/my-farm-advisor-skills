#!/usr/bin/env python3
"""Run the field-portfolio EDA stage and write reviewable static outputs.

Examples
--------
    export DATA_PIPELINE_DATA_ROOT=$HOME/my-farm-advisor-runtime
    cd "$DATA_PIPELINE_DATA_ROOT/data-pipeline/src"
    "$DATA_PIPELINE_DATA_ROOT/data-pipeline/.venv/bin/python" \
        scripts/eda/field_portfolio/run.py --stage boundaries

Outputs land in ``<runtime>/derived/eda/field-portfolio`` unless ``--out-dir``
says otherwise. Stages are additive: ``boundaries`` writes the size/shape and
map set, ``cdl`` the crop-history set, ``weather`` the cell-year climate set,
and ``all`` writes every stage.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[2]
for _candidate in (_SCRIPTS, _SCRIPTS / "eda"):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

from field_portfolio import dataset  # noqa: E402
from field_portfolio.dataset import PortfolioError  # noqa: E402

STAGES = ("boundaries", "cdl", "weather", "all")
BUILT_STAGES = ("boundaries", "cdl", "weather", "all")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", default="boundaries", choices=STAGES)
    parser.add_argument("--root", default=None, help="runtime data root (default: $DATA_PIPELINE_DATA_ROOT)")
    parser.add_argument("--out-dir", default=None, help="default: <runtime>/derived/eda/field-portfolio")
    parser.add_argument(
        "--require-layers",
        default="weather,cdl",
        help="comma-separated layers that must exist, or 'none' (default: weather,cdl)",
    )
    parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="skip the dataset gate (recorded in the manifest; not recommended)",
    )
    return parser.parse_args(argv)


def _required_layers(raw: str) -> tuple[str, ...]:
    if raw.strip().lower() == "none":
        return ()
    layers = tuple(item.strip().lower() for item in raw.split(",") if item.strip())
    unknown = set(layers) - {"weather", "cdl"}
    if unknown:
        raise SystemExit(f"error: unknown layers {sorted(unknown)}; use weather,cdl,none")
    return layers


def _candidate_git_dirs(base: Path) -> list[Path]:
    """Directories that might sit inside a checkout, best first.

    The script normally runs from the runtime copy, which is not a repo. The
    installer records the checkout that produced the copy, so consult that
    locator before giving up.
    """
    dirs: list[Path] = [*Path(__file__).resolve().parents, base]
    locator = base / ".my-farm-advisor-source.json"
    if locator.exists():
        try:
            payload = json.loads(locator.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        root = payload.get("my_farm_advisor_skill_root")
        if root:
            resolved = Path(root).expanduser().resolve()
            dirs.extend([resolved, *resolved.parents])
    return dirs


def _git_state(base: Path) -> tuple[str | None, bool | None]:
    """Commit and dirty flag of the checkout that produced this code."""
    for cwd in _candidate_git_dirs(base):
        try:
            head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        commit = head.stdout.strip()
        if head.returncode != 0 or not commit:
            continue
        try:
            status = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            return commit, None
        dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
        return commit, dirty
    return None, None


def _run_validator(base: Path) -> dict:
    script = base / "src" / "scripts" / "validate_dataset.py"
    if not script.exists():
        return {"status": "unavailable", "detail": f"missing {script}"}
    env = dict(os.environ)
    env["DATA_PIPELINE_DATA_ROOT"] = str(base.parent if base.name == "data-pipeline" else base)
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=base / "src",
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        tail = "\n".join(result.stdout.strip().splitlines()[-25:])
        raise PortfolioError(
            "dataset validation failed; fix the data before analysing it:\n" + tail
        )
    tail = [line for line in result.stdout.splitlines() if line.strip()][-1:]
    return {"status": "passed", "detail": tail[0] if tail else ""}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    if args.stage not in BUILT_STAGES:
        print(
            f"stage {args.stage!r} is not built yet.",
            file=sys.stderr,
        )
        return 2

    try:
        base = dataset.runtime_base(args.root)
        out_dir = (
            Path(args.out_dir).expanduser().resolve()
            if args.out_dir
            else dataset.default_output_dir(base)
        )
        out_dir.mkdir(parents=True, exist_ok=True)

        validation = (
            {"status": "skipped", "detail": "--skip-validate"}
            if args.skip_validate
            else _run_validator(base)
        )
        if validation["status"] == "skipped":
            print("warning: dataset validation skipped", file=sys.stderr)

        portfolio = dataset.build_portfolio(
            root=base, require_layers=_required_layers(args.require_layers)
        )
    except PortfolioError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    records: list[dict] = []
    if args.stage in ("boundaries", "all"):
        from field_portfolio import figures_boundary

        records.extend(figures_boundary.build_all(portfolio, out_dir))
    if args.stage in ("cdl", "all"):
        from field_portfolio import figures_cdl

        records.extend(figures_cdl.build_all(portfolio, out_dir))
    if args.stage in ("weather", "all"):
        from field_portfolio import figures_weather

        records.extend(figures_weather.build_all(portfolio, out_dir))
    if not records:  # pragma: no cover - guarded above
        raise AssertionError(args.stage)

    table_path = out_dir / "tables" / "table_fields.csv"
    dataset.write_table(portfolio.fields_table, table_path)

    analysis = {
        "workflow": "eda-field-portfolio",
        "stage": args.stage,
        "generated_at": dataset.iso_now(),
        "root_rel": ".",
        "dataset": {
            "farms": [farm.label for farm in portfolio.farms],
            "growers": portfolio.growers,
            "fields": int(len(portfolio.fields_table)),
            "weather_cells": portfolio.cell_count,
            "weather_days": int(portfolio.weather["date"].nunique()) if not portfolio.weather.empty else 0,
            "cdl_years": portfolio.cdl_years,
        },
        "unit_of_analysis": {
            "boundaries": "field (n per grower)",
            "cdl": "field-year (dominant CDL class; 5 years per field)",
            "weather": "NASA POWER cell / site-year; fields are not independent samples",
        },
        "tables": {"fields": "tables/table_fields.csv"},
        "figures": records,
    }
    analysis_path = out_dir / "analysis.json"
    dataset.write_json(analysis_path, analysis)

    inputs: list[Path] = []
    for farm in portfolio.farms:
        inputs.append(farm.boundary_path)
        inputs.append(farm.directory / "farm.json")
        inputs.extend(
            path for path in farm.tables_dir.iterdir() if path.is_file()
        )
    states_path = dataset.geoadmin_states_path(portfolio.base)
    if states_path is not None:
        inputs.append(states_path)
    outputs = [table_path, analysis_path]
    outputs.extend(out_dir / record["file"] for record in records)

    git_head, git_dirty = _git_state(base)
    manifest = {
        "workflow": "eda-field-portfolio",
        "stage": args.stage,
        "generated_at": dataset.iso_now(),
        "runtime_base_rel": ".",
        "git_head": git_head,
        "git_dirty": git_dirty,
        "python": platform.python_version(),
        "validation": validation,
        "require_layers": list(_required_layers(args.require_layers)),
        "inputs": dataset.input_manifest(inputs, base),
        "outputs": dataset.input_manifest(outputs, base),
        "figures": [
            {
                "id": record["id"],
                "file": record["file"],
                "n": record["n"],
                "unit": record["unit"],
            }
            for record in records
        ],
    }
    dataset.write_json(out_dir / "manifest.json", manifest)

    print(f"\nfield-portfolio :: stage {args.stage}")
    print(f"  runtime   {base}")
    print(f"  output    {out_dir}")
    print(
        f"  dataset   {len(portfolio.fields_table)} fields, "
        f"{len(portfolio.growers)} growers, {portfolio.cell_count} weather cells"
    )
    print(f"  validate  {validation['status']}")
    print("\n  figure                              n   unit    finding")
    for record in records:
        print(
            f"  {record['id']:<34} {record['n']:>3}  {record['unit']:<7} "
            f"{record['finding'][:72]}"
        )
    print(f"\n  wrote {len(outputs)} files + manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
