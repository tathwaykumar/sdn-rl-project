"""Small statistics helpers with explicit handling of small samples.

With five seeds per cell, the right summary is a median with an interquartile
range, plus a mean with a confidence interval wide enough to be honest about
how little data that is.  These helpers never silently drop missing values:
callers pass in the values they actually measured, and ``n`` is reported
alongside every statistic so a cell computed from two runs is never mistaken
for one computed from five.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

#: Two-sided 95% critical values of Student's t by degrees of freedom.
#: Hard-coded to avoid a scipy dependency; df >= 30 uses the normal value.
_T95: Dict[int, float] = {
    1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365,
    8: 2.306, 9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145,
    15: 2.131, 16: 2.120, 17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086,
    21: 2.080, 22: 2.074, 23: 2.069, 24: 2.064, 25: 2.060, 26: 2.056,
    27: 2.052, 28: 2.048, 29: 2.045,
}


def t95(df: int) -> float:
    if df <= 0:
        return float("nan")
    return _T95.get(df, 1.960)


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    """Linear-interpolation percentile; ``q`` in [0, 1]."""
    clean = sorted(v for v in values if v is not None)
    if not clean:
        return None
    if len(clean) == 1:
        return clean[0]
    pos = q * (len(clean) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return clean[int(pos)]
    return clean[lo] + (clean[hi] - clean[lo]) * (pos - lo)


@dataclass
class Summary:
    """Summary of one cell of the results table."""

    n: int
    mean: Optional[float] = None
    stdev: Optional[float] = None
    median: Optional[float] = None
    q1: Optional[float] = None
    q3: Optional[float] = None
    ci95_low: Optional[float] = None
    ci95_high: Optional[float] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None

    @property
    def iqr(self) -> Optional[float]:
        if self.q1 is None or self.q3 is None:
            return None
        return self.q3 - self.q1

    def as_dict(self, prefix: str = "") -> Dict[str, Optional[float]]:
        return {
            f"{prefix}n": self.n,
            f"{prefix}mean": self.mean,
            f"{prefix}stdev": self.stdev,
            f"{prefix}median": self.median,
            f"{prefix}q1": self.q1,
            f"{prefix}q3": self.q3,
            f"{prefix}ci95_low": self.ci95_low,
            f"{prefix}ci95_high": self.ci95_high,
            f"{prefix}min": self.minimum,
            f"{prefix}max": self.maximum,
        }

    def format_median_iqr(self, scale: float = 1.0, digits: int = 2) -> str:
        if self.median is None:
            return "n/a"
        iqr = self.iqr
        if iqr is None:
            return f"{self.median / scale:.{digits}f}"
        return f"{self.median / scale:.{digits}f} [IQR {iqr / scale:.{digits}f}]"

    def format_mean_ci(self, scale: float = 1.0, digits: int = 2) -> str:
        if self.mean is None:
            return "n/a"
        if self.ci95_low is None or self.n < 2:
            return f"{self.mean / scale:.{digits}f} (n={self.n})"
        half = (self.ci95_high - self.ci95_low) / 2 / scale
        return f"{self.mean / scale:.{digits}f} +/- {half:.{digits}f}"


def summarise(values: Sequence[Optional[float]]) -> Summary:
    """Summarise measured values, ignoring ``None`` but reporting the count.

    ``None`` means "not measured" and is excluded from the statistics; the
    resulting ``n`` tells the reader how many observations the cell really
    rests on.
    """
    clean = [float(v) for v in values if v is not None]
    n = len(clean)
    if n == 0:
        return Summary(n=0)
    mean = sum(clean) / n
    if n >= 2:
        var = sum((x - mean) ** 2 for x in clean) / (n - 1)
        sd = math.sqrt(var)
        half = t95(n - 1) * sd / math.sqrt(n)
        lo, hi = mean - half, mean + half
    else:
        sd = None
        lo = hi = None
    return Summary(
        n=n, mean=mean, stdev=sd,
        median=percentile(clean, 0.5),
        q1=percentile(clean, 0.25),
        q3=percentile(clean, 0.75),
        ci95_low=lo, ci95_high=hi,
        minimum=min(clean), maximum=max(clean),
    )


def ci_overlap(a: Summary, b: Summary) -> Optional[bool]:
    """Do two 95% CIs overlap?

    Non-overlap is informal evidence of a difference; overlap is not proof of
    no difference. With n=5 this is a weak instrument and the report says so
    rather than dressing it up as a significance test.
    """
    if a.ci95_low is None or b.ci95_low is None:
        return None
    return not (a.ci95_high < b.ci95_low or b.ci95_high < a.ci95_low)


def relative_change(baseline: Optional[float], other: Optional[float]) -> Optional[float]:
    """Fractional change from ``baseline`` to ``other``; None if undefined."""
    if baseline in (None, 0) or other is None:
        return None
    return (other - baseline) / abs(baseline)
