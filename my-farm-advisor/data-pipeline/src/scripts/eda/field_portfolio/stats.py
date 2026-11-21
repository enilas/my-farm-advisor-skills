"""Small, explicit statistics layer.

Three rules this module enforces so the figures cannot overclaim:

1. **n is an argument, not a decoration.** Every result carries the number of
   independent units it was computed on, so a caller cannot quietly pass 30
   field-days where 10 cells is the honest count.
2. **Effect size travels with every p-value.** With 10 vs 10 vs 10 fields a
   p-value alone is close to meaningless; epsilon-squared, Cliff's delta and
   the median difference are what the reader can act on.
3. **Seeds are fixed.** Bootstrap and permutation results are reproducible
   across runs, which is what makes a figure re-reviewable.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

DEFAULT_SEED = 20260101
DEFAULT_BOOTSTRAP = 4000
DEFAULT_PERMUTATIONS = 10000


@dataclass(frozen=True)
class GroupTest:
    """Kruskal-Wallis result with the effect size the guide reports."""

    h: float
    p: float
    epsilon_squared: float
    n: int
    groups: int
    medians: dict[str, float]

    def as_dict(self) -> dict:
        return {
            "test": "kruskal-wallis",
            "h": round(self.h, 4),
            "p": round(self.p, 6),
            "effect": "epsilon_squared",
            "effect_value": round(self.epsilon_squared, 4),
            "n": self.n,
            "groups": self.groups,
            "medians": {k: round(v, 4) for k, v in self.medians.items()},
        }


@dataclass(frozen=True)
class RankCorrelation:
    rho: float
    p: float
    n: int
    ci_low: float | None = None
    ci_high: float | None = None

    def as_dict(self) -> dict:
        payload = {
            "test": "spearman",
            "rho": round(self.rho, 4),
            "p": round(self.p, 6),
            "n": self.n,
        }
        if self.ci_low is not None and self.ci_high is not None:
            payload["ci95"] = [round(self.ci_low, 4), round(self.ci_high, 4)]
        return payload


@dataclass(frozen=True)
class MeanDifference:
    observed: float
    p: float
    a_mean: float
    b_mean: float
    n_a: int
    n_b: int
    cliffs_delta: float

    def as_dict(self) -> dict:
        return {
            "test": "permutation_mean_difference",
            "observed": round(self.observed, 4),
            "p": round(self.p, 6),
            "effect": "cliffs_delta",
            "effect_value": round(self.cliffs_delta, 4),
            "n_a": self.n_a,
            "n_b": self.n_b,
        }


@dataclass(frozen=True)
class Association:
    """Chi-square test of independence with Cramér's V as the effect size."""

    chi2: float
    p: float
    dof: int
    cramers_v: float
    n: int
    min_expected: float

    def as_dict(self) -> dict:
        return {
            "test": "chi_square_independence",
            "chi2": round(self.chi2, 4),
            "p": round(self.p, 6),
            "dof": self.dof,
            "effect": "cramers_v",
            "effect_value": round(self.cramers_v, 4),
            "n": self.n,
            "min_expected": round(self.min_expected, 3),
        }


def chi_square(table) -> Association:
    """Test association in a contingency table, without Yates correction.

    ``table`` is any 2-D count array. Cramér's V is computed from the same
    statistic; the minimum expected count is returned so a caller can state
    whether the asymptotic p is trustworthy. No continuity correction: the
    transition table has a structural zero, and Yates would move the p-value
    without changing the conclusion.
    """
    from scipy import stats

    counts = np.asarray(table, dtype=float)
    if counts.ndim != 2 or min(counts.shape) < 2:
        raise ValueError(f"chi_square needs a 2-D table with >=2 rows and columns, got {counts.shape}")
    if (counts < 0).any():
        raise ValueError("chi_square does not accept negative counts")
    n = int(counts.sum())
    if n == 0:
        raise ValueError("chi_square needs at least one observation")
    result = stats.chi2_contingency(counts, correction=False)
    chi2, p, dof, expected = (
        float(result.statistic),
        float(result.pvalue),
        int(result.dof),
        np.asarray(result.expected_freq, dtype=float),
    )
    cramers_v = math.sqrt(chi2 / (n * (min(counts.shape) - 1)))
    return Association(
        chi2=chi2,
        p=p,
        dof=dof,
        cramers_v=cramers_v,
        n=n,
        min_expected=float(expected.min()),
    )


def _clean_pair(x, y) -> tuple[np.ndarray, np.ndarray]:
    xa = np.asarray(x, dtype=float)
    ya = np.asarray(y, dtype=float)
    if xa.shape != ya.shape:
        raise ValueError(f"paired arrays differ in shape: {xa.shape} vs {ya.shape}")
    keep = np.isfinite(xa) & np.isfinite(ya)
    return xa[keep], ya[keep]


def kruskal_groups(groups: dict[str, np.ndarray]) -> GroupTest:
    """Kruskal-Wallis across named groups, with epsilon-squared."""
    from scipy import stats

    cleaned = {
        name: np.asarray(values, dtype=float)
        for name, values in groups.items()
        if np.asarray(values, dtype=float).size
    }
    if len(cleaned) < 2:
        raise ValueError("kruskal_groups needs at least two non-empty groups")
    samples = list(cleaned.values())
    result = stats.kruskal(*samples)
    n_total = int(sum(sample.size for sample in samples))
    k = len(samples)
    # epsilon^2 = (H - k + 1) / (n - k); the standard KW effect size.
    epsilon_squared = max(0.0, (float(result.statistic) - k + 1) / max(n_total - k, 1))
    return GroupTest(
        h=float(result.statistic),
        p=float(result.pvalue),
        epsilon_squared=epsilon_squared,
        n=n_total,
        groups=k,
        medians={name: float(np.median(vals)) for name, vals in cleaned.items()},
    )


def spearman(x, y, bootstrap: int = DEFAULT_BOOTSTRAP, seed: int = DEFAULT_SEED) -> RankCorrelation:
    """Spearman rho with a percentile bootstrap interval on the pairs."""
    from scipy import stats

    xa, ya = _clean_pair(x, y)
    n = int(xa.size)
    if n < 3:
        raise ValueError(f"spearman needs >=3 finite pairs, got {n}")
    result = stats.spearmanr(xa, ya)
    rho, p = float(result.statistic), float(result.pvalue)
    if not np.isfinite(rho):
        return RankCorrelation(rho=0.0, p=1.0, n=n)
    # scipy can return 0.9999999999999999 for a perfect rank match; snap so a
    # downstream "rho == 1" check is not fighting float noise.
    rho = float(np.clip(rho, -1.0, 1.0))
    if abs(abs(rho) - 1.0) < 1e-12:
        rho = float(np.sign(rho))

    rng = np.random.default_rng(seed)
    draws = np.empty(bootstrap, dtype=float)
    for index in range(bootstrap):
        pick = rng.integers(0, n, size=n)
        if np.unique(xa[pick]).size < 2 or np.unique(ya[pick]).size < 2:
            draws[index] = np.nan
            continue
        draws[index] = stats.spearmanr(xa[pick], ya[pick]).statistic
    valid = draws[np.isfinite(draws)]
    low = float(np.percentile(valid, 2.5)) if valid.size else None
    high = float(np.percentile(valid, 97.5)) if valid.size else None
    return RankCorrelation(rho=rho, p=p, n=n, ci_low=low, ci_high=high)


def cliffs_delta(a, b) -> float:
    """Probability that a draw from A exceeds one from B, rescaled to [-1, 1]."""
    xa = np.asarray(a, dtype=float)
    xb = np.asarray(b, dtype=float)
    xa, xb = xa[np.isfinite(xa)], xb[np.isfinite(xb)]
    if xa.size == 0 or xb.size == 0:
        return 0.0
    greater = float((xa[:, None] > xb[None, :]).sum())
    less = float((xa[:, None] < xb[None, :]).sum())
    return (greater - less) / (xa.size * xb.size)


def permutation_mean_difference(
    a,
    b,
    permutations: int = DEFAULT_PERMUTATIONS,
    seed: int = DEFAULT_SEED,
) -> MeanDifference:
    """Two-sided permutation test on the difference of means.

    Used instead of a t-test for the cross-grower comparisons: field sizes are
    not normal, the groups are small, and a permutation test assumes only
    exchangeability under the null.
    """
    xa = np.asarray(a, dtype=float)
    xb = np.asarray(b, dtype=float)
    xa, xb = xa[np.isfinite(xa)], xb[np.isfinite(xb)]
    if xa.size < 2 or xb.size < 2:
        raise ValueError("permutation_mean_difference needs >=2 values per group")

    observed = float(xa.mean() - xb.mean())
    pooled = np.concatenate([xa, xb])
    rng = np.random.default_rng(seed)
    extreme = 0
    for _ in range(permutations):
        rng.shuffle(pooled)
        diff = float(pooled[: xa.size].mean() - pooled[xa.size :].mean())
        if abs(diff) >= abs(observed) - 1e-12:
            extreme += 1
    p = (extreme + 1) / (permutations + 1)
    return MeanDifference(
        observed=observed,
        p=p,
        a_mean=float(xa.mean()),
        b_mean=float(xb.mean()),
        n_a=int(xa.size),
        n_b=int(xb.size),
        cliffs_delta=cliffs_delta(xa, xb),
    )


def format_p(p: float) -> str:
    """p-values as a reader would write them; never 'p = 0.000'."""
    if p < 0.001:
        return "p < 0.001"
    return f"p = {p:.3f}"
