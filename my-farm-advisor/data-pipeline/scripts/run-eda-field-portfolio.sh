#!/usr/bin/env bash
# One-command run for the field-portfolio EDA: refresh runtime source, run the
# stage, then summarize what landed on disk. Intended for demos and repeat
# checks; the underlying command is still run.py.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PIPELINE_DIR="$(cd "${SCRIPT_DIR}/.." && pwd -P)"

STAGE="all"
OUT_DIR=""
ROOT="${DATA_PIPELINE_DATA_ROOT:-}"
REFRESH=1
SKIP_VALIDATE=0
NO_REPORT=0

usage() {
  cat <<'EOF'
Usage: run-eda-field-portfolio.sh [options]

Refresh the runtime source, run the field-portfolio EDA, and print where the
outputs landed.

Options:
  --stage <stage>     boundaries | cdl | weather | all. Defaults to all.
  --out-dir <path>    Output directory. Relative paths resolve against the
                      caller's working directory. Defaults to
                      <runtime>/derived/eda/field-portfolio.
  --root <abs-path>   Data root. Overrides DATA_PIPELINE_DATA_ROOT. Accepts the
                      root (…/my-farm-advisor-runtime) or its data-pipeline
                      directory.
  --no-refresh        Skip the runtime source refresh (faster repeat runs).
                      Only safe when the runtime already matches the checkout.
  --skip-validate     Pass through to run.py; recorded in the manifest. Use
                      only for deliberate forensics.
  --no-report         Skip report.html (figures and analysis.json still land).
  -h, --help          Show this help.

Environment:
  DATA_PIPELINE_DATA_ROOT   Used when --root is omitted; must be absolute.

Examples:
  ./scripts/run-eda-field-portfolio.sh
  ./scripts/run-eda-field-portfolio.sh --stage weather --out-dir /tmp/w-review
EOF
}

die() {
  printf '[eda] %s\n' "$*" >&2
  exit 2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage)
      [[ $# -ge 2 ]] || die "--stage needs a value"
      STAGE="$2"
      shift 2
      ;;
    --out-dir)
      [[ $# -ge 2 ]] || die "--out-dir needs a value"
      OUT_DIR="$2"
      shift 2
      ;;
    --root)
      [[ $# -ge 2 ]] || die "--root needs a value"
      ROOT="$2"
      shift 2
      ;;
    --no-refresh)
      REFRESH=0
      shift
      ;;
    --skip-validate)
      SKIP_VALIDATE=1
      shift
      ;;
    --no-report)
      NO_REPORT=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf '[eda] unknown argument: %s\n\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

case "${STAGE}" in
  boundaries|cdl|weather|all) ;;
  *) die "unknown stage: ${STAGE} (expected boundaries, cdl, weather, or all)" ;;
esac

[[ -n "${ROOT}" ]] || die "DATA_PIPELINE_DATA_ROOT is unset; export it or pass --root"
[[ "${ROOT}" = /* ]] || die "data root must be absolute: ${ROOT}"

# Mirror dataset.runtime_base: accept either the root or its data-pipeline dir.
if [[ "$(basename "${ROOT}")" == "data-pipeline" ]]; then
  RUNTIME_BASE="${ROOT%/}"
  DATA_ROOT="$(dirname "${RUNTIME_BASE}")"
else
  DATA_ROOT="${ROOT%/}"
  RUNTIME_BASE="${DATA_ROOT}/data-pipeline"
fi
[[ -d "${RUNTIME_BASE}" ]] || die "runtime base not found: ${RUNTIME_BASE}"

PY="${RUNTIME_BASE}/.venv/bin/python"
[[ -x "${PY}" ]] || die "runtime virtualenv missing: ${PY}
Run ./scripts/install.sh --non-interactive --force-refresh (without
--no-install-deps) once to create it."

if [[ -n "${OUT_DIR}" ]]; then
  OUT_DIR="$(realpath -m "${OUT_DIR}")"
else
  OUT_DIR="${RUNTIME_BASE}/derived/eda/field-portfolio"
fi

printf '[eda] runtime  %s\n' "${RUNTIME_BASE}"
printf '[eda] output   %s\n' "${OUT_DIR}"
printf '[eda] stage    %s\n\n' "${STAGE}"

if (( REFRESH )); then
  "${SCRIPT_DIR}/install.sh" \
    --data-root "${DATA_ROOT}" \
    --non-interactive \
    --force-refresh \
    --no-install-deps
  printf '\n'
else
  printf '[eda] source refresh skipped (--no-refresh)\n\n'
fi

ARGS=(--stage "${STAGE}" --out-dir "${OUT_DIR}")
(( SKIP_VALIDATE )) && ARGS+=(--skip-validate)
(( NO_REPORT )) && ARGS+=(--no-report)

(
  cd "${RUNTIME_BASE}/src"
  DATA_PIPELINE_DATA_ROOT="${DATA_ROOT}" "${PY}" \
    scripts/eda/field_portfolio/run.py "${ARGS[@]}"
)

printf '\n[eda] outputs\n'
"${PY}" - "${OUT_DIR}" <<'PY'
import json
import pathlib
import sys

out = pathlib.Path(sys.argv[1])
analysis = json.loads((out / "analysis.json").read_text(encoding="utf-8"))
manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))

figures = analysis["figures"]
categories: dict[str, int] = {}
for figure in figures:
    categories[figure["category"]] = categories.get(figure["category"], 0) + 1

pngs = sorted(item.name for item in (out / "figures").glob("*.png"))
missing = [figure["file"] for figure in figures if not (out / figure["file"]).is_file()]

def size(name: str) -> str:
    path = out / name
    return f"{path.stat().st_size / 1024:.1f} KB" if path.is_file() else "missing"

print(f"[eda] figures   {len(figures)} records, {len(pngs)} png on disk")
print("               " + ", ".join(f"{name} {count}" for name, count in categories.items()))
print(f"[eda] files     tables/table_fields.csv {size('tables/table_fields.csv')} | "
      f"analysis.json {size('analysis.json')} | manifest.json {size('manifest.json')}")
if (out / "report.html").is_file():
    print(f"[eda] report    report.html {size('report.html')} — open it in a browser; print to PDF")
print(f"[eda] manifest  head {manifest['git_head'][:7]} "
      f"dirty={str(manifest['git_dirty']).lower()} "
      f"validator={manifest['validation']['status']}")
if missing:
    print(f"[eda] WARNING   {len(missing)} record(s) without a file: {', '.join(missing)}")
    sys.exit(1)
PY
