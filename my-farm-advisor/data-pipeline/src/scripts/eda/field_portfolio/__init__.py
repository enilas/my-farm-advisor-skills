"""Field-portfolio EDA: cross-grower comparison of boundaries, crop history, climate.

Stage 1 covers the boundary layer. The package is deliberately split so each
module is reviewable on its own:

    dataset    runtime discovery -> tidy frames, with the layer guards
    metrics    agronomic derivations (GDD, frost dates) that Stage 3 consumes
    stats      effect sizes, bootstrap intervals, rank tests
    figures_*  one module per data category
    report     analysis.json -> a static, printable HTML page
    run        CLI entrypoint, writes figures/tables/analysis.json/manifest.json

Runnable code lives next to the other EDA scripts; the workflow prose lives in
``my-farm-advisor/eda/eda-field-portfolio/GUIDE.md``.
"""

from __future__ import annotations

import sys
from pathlib import Path

# scripts/ and scripts/eda/ on the path so ``lib.*`` and ``field_portfolio.*``
# import identically whether this runs as a script or as a package.
_SCRIPTS = Path(__file__).resolve().parents[2]
for _candidate in (_SCRIPTS, _SCRIPTS / "eda"):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))

__all__ = ["dataset", "metrics", "report", "stats"]
