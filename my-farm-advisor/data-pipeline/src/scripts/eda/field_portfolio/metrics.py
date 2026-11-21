"""Agronomic derivations shared by the portfolio stages.

Conventions are fixed here and stated in the guide, because a GDD number
without its base temperature and cap is not a number:

* ``growing_degree_days`` — corn convention: base 10 °C, upper cap 30 °C on
  the daily maximum, ``max(0, (min(Tmax, 30) + Tmin) / 2 - 10)``.
  The cap matters: a 36 °C day contributes the same as a 30 °C day, which is
  the point of a cap (heat above the optimum is not extra growth).
* Frost — ``T2M_MIN < 0 °C``. The spring frost is the last one before the
  growing season (searched from March 1); the fall frost is the first one
  after midsummer (searched from August 1). Both are calendar-year scoped.
* ``season_precip`` — inclusive month window, default April through September.

All functions take plain sequences so they are testable without pandas.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np

GDD_BASE_C = 10.0
GDD_CAP_C = 30.0
FROST_THRESHOLD_C = 0.0
# Frost dates are only meaningful inside these windows: a January thaw is not
# the start of the season, and a December freeze is after harvest.
SPRING_WINDOW = ((3, 1), (6, 30))
FALL_WINDOW = ((8, 1), (11, 30))


def growing_degree_days(
    tmin,
    tmax,
    base_c: float = GDD_BASE_C,
    cap_c: float = GDD_CAP_C,
) -> np.ndarray:
    """Daily GDD with the upper cap applied to Tmax, floored at zero."""
    tmin_arr = np.asarray(tmin, dtype=float)
    tmax_arr = np.asarray(tmax, dtype=float)
    if tmin_arr.shape != tmax_arr.shape:
        raise ValueError(
            f"tmin/tmax shape mismatch: {tmin_arr.shape} vs {tmax_arr.shape}"
        )
    capped = np.minimum(tmax_arr, cap_c)
    return np.maximum((capped + tmin_arr) / 2.0 - base_c, 0.0)


@dataclass(frozen=True)
class FrostWindow:
    """Frost boundaries for one calendar year of one site."""

    last_spring_frost: date | None
    first_fall_frost: date | None

    @property
    def season_length_days(self) -> int | None:
        if self.last_spring_frost is None or self.first_fall_frost is None:
            return None
        return (self.first_fall_frost - self.last_spring_frost).days


def frost_window(dates, tmin, threshold_c: float = FROST_THRESHOLD_C) -> FrostWindow:
    """Last spring frost and first fall frost for a single calendar year.

    ``dates`` must be day-resolution and sorted ascending. Only the spring
    window (Mar 1 - Jun 30) and the fall window (Aug 1 - Nov 30) are scanned:
    a January thaw is not the start of the growing season, and a December
    freeze is after harvest.
    """
    import pandas as pd

    series = pd.Series(np.asarray(tmin, dtype=float), index=pd.DatetimeIndex(dates))
    if series.empty:
        return FrostWindow(None, None)
    year = series.index[0].year
    frozen = series[series < threshold_c]

    def window(bounds):
        (start_month, start_day), (end_month, end_day) = bounds
        lower = pd.Timestamp(year, start_month, start_day)
        upper = pd.Timestamp(year, end_month, end_day)
        return frozen[(frozen.index >= lower) & (frozen.index <= upper)]

    spring = window(SPRING_WINDOW)
    autumn = window(FALL_WINDOW)
    return FrostWindow(
        last_spring_frost=spring.index.max().date() if not spring.empty else None,
        first_fall_frost=autumn.index.min().date() if not autumn.empty else None,
    )


def season_precip(dates, precip_mm, start_month: int = 4, end_month: int = 9) -> float:
    """Total precipitation inside an inclusive month window."""
    import pandas as pd

    series = pd.Series(np.asarray(precip_mm, dtype=float), index=pd.DatetimeIndex(dates))
    if series.empty:
        return 0.0
    keep = series.index.month
    mask = (keep >= start_month) & (keep <= end_month)
    return float(series[mask].sum())
