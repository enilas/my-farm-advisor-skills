#!/usr/bin/env bash
# Regenerate the committed report fixture from a live runtime.
#
# Runs the one-command wrapper into a throwaway directory, then copies only the
# page and its figures here. analysis.json, manifest.json, and the field table
# stay out: they carry timestamps, mtimes, and per-field rows, while the page
# already carries each figure's sha256 for verification.
#
# Usage: ./build.sh <runtime-root>
# The root may be the data root (…/my-farm-advisor-runtime) or its
# data-pipeline directory; DATA_PIPELINE_DATA_ROOT is used when omitted.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
PIPELINE="$(cd "${HERE}/../../../../data-pipeline" && pwd -P)"
ROOT="${1:-${DATA_PIPELINE_DATA_ROOT:-}}"

if [[ -z "${ROOT}" ]]; then
  echo "usage: ./build.sh <runtime-root>" >&2
  echo "  or export DATA_PIPELINE_DATA_ROOT" >&2
  exit 2
fi

TMP="$(mktemp -d)"
trap 'rm -rf "${TMP}"' EXIT

"${PIPELINE}/scripts/run-eda-field-portfolio.sh" \
  --root "${ROOT}" \
  --out-dir "${TMP}" \
  --stage all

rm -rf "${HERE}/output"
mkdir -p "${HERE}/output/figures"
cp "${TMP}/report.html" "${HERE}/output/report.html"
cp "${TMP}"/figures/*.png "${HERE}/output/figures/"

count="$(find "${HERE}/output/figures" -name '*.png' | wc -l | tr -d ' ')"
echo
echo "wrote ${HERE}/output/report.html and ${count} figures"
