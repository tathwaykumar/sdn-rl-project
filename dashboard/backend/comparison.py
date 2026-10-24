"""A/B/C comparison, computed with the project's own analysis code.

``analysis.aggregate`` and ``analysis.stats`` are imported rather than
reimplemented. If the dashboard computed its own throughput or its own
confidence intervals, it could disagree with the generated report and nobody
could say which was correct. One definition, two presentations.

Two things this module is careful about:

* **Direction of improvement.** Higher throughput is better; lower latency,
  loss, occupancy, controller load and recovery time are better. The sign is
  taken from an explicit table, not guessed from the metric name.
* **What counts as a difference.** With five seeds a difference is only
  reported as real when the 95% confidence intervals do not overlap.
  Otherwise it is "not distinguishable at this sample size" -- which is
  usually the honest answer, and is not the same as "no difference".
"""

from __future__ import annotations

import os
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

from .config import ANALYSIS_OUT, PROJECT_ROOT, RUNS_DIR, DataSource

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from analysis import stats  # noqa: E402
from analysis.aggregate import (  # noqa: E402
    METRICS, VARIANT_ORDER, WORKLOAD_ORDER, modal_duration, run_metrics,
    split_by_duration,
)
from analysis.parsers import (  # noqa: E402
    is_diagnostic_run, is_primary_run, load_run,
)
from analysis.report import LOWER_IS_BETTER  # noqa: E402

#: Metric key -> presentation metadata for the UI.
METRIC_INFO: Dict[str, Dict[str, object]] = {
    key: {
        "key": key,
        "label": label,
        "scale": scale,
        "digits": digits,
        "unit": {1e6: "Mbit/s", 0.01: "%"}.get(scale, ""),
        "lower_is_better": key in LOWER_IS_BETTER,
    }
    for key, label, scale, digits, _ in METRICS
}

_CACHE_LOCK = threading.Lock()
_CACHE: Dict[str, Tuple[float, List[Dict[str, object]]]] = {}
_CACHE_TTL_S = 20.0


def _cache_key(runs_dir: str, window: str) -> str:
    return f"{runs_dir}|{window}"


def _runs_fingerprint(runs_dir: str) -> float:
    """Cheap change detector: newest manifest wins.

    A run only becomes analysable when its manifest appears, so the newest
    manifest mtime is enough to know whether the result set changed.
    """
    newest = 0.0
    try:
        for entry in os.listdir(runs_dir):
            manifest = os.path.join(runs_dir, entry, "manifest.json")
            try:
                newest = max(newest, os.path.getmtime(manifest))
            except OSError:
                continue
    except OSError:
        return 0.0
    return newest


def collect_run_metrics(runs_dir: str = RUNS_DIR, window: str = "full",
                        include_incomplete: bool = False) -> List[Dict[str, object]]:
    """Per-run metrics for every analysable run, cached briefly."""
    key = _cache_key(runs_dir, window)
    fingerprint = _runs_fingerprint(runs_dir)
    now = time.time()

    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and (now - cached[0]) < _CACHE_TTL_S and cached[0] >= fingerprint:
            return cached[1]

    rows: List[Dict[str, object]] = []
    try:
        entries = sorted(os.listdir(runs_dir))
    except OSError:
        entries = []

    for entry in entries:
        path = os.path.join(runs_dir, entry)
        if not os.path.isdir(path):
            continue
        # Control-experiment runs are valid runs but deliberately crippled or
        # scrambled, so they are not samples of a variant's normal behaviour
        # and must not enter the A/B/C comparison.
        if is_diagnostic_run(entry):
            continue
        # Ad-hoc runs launched from the dashboard are not extra samples of the
        # controlled matrix. analysis.parsers.is_primary_run documents why:
        # one was started with a seed the matrix already used, so pooling them
        # double-counts that configuration, inflates n and understates the
        # spread. The offline analysis has always filtered them; this page did
        # not, which is why it could report n=8 for a cell the matrix defines
        # as n=5 and disagree with the generated report it claims to match.
        # They stay fully visible in History and are listed as excluded below.
        if not is_primary_run(entry):
            continue
        run = load_run(path)
        if run is None:
            continue
        if not run.complete and not include_incomplete:
            continue
        try:
            row = run_metrics(run, window)
        except Exception as exc:  # a malformed run must not break the page
            row = {
                "run": entry, "variant": run.variant, "workload": run.workload,
                "seed": run.seed, "window": window, "complete": int(run.complete),
                "error": f"{type(exc).__name__}: {exc}",
            }
        row["run_id"] = entry
        rows.append(row)

    with _CACHE_LOCK:
        _CACHE[key] = (now, rows)
    return rows


def _filter(rows: List[Dict[str, object]], workloads: Optional[List[str]],
            variants: Optional[List[str]], seeds: Optional[List[int]]
            ) -> List[Dict[str, object]]:
    out = rows
    if workloads:
        out = [r for r in out if r.get("workload") in workloads]
    if variants:
        out = [r for r in out if r.get("variant") in variants]
    if seeds:
        out = [r for r in out if r.get("seed") in seeds]
    return out


def _summary_payload(summary: stats.Summary) -> Dict[str, object]:
    return {
        "n": summary.n,
        "mean": summary.mean,
        "median": summary.median,
        "q1": summary.q1,
        "q3": summary.q3,
        "iqr": summary.iqr,
        "stdev": summary.stdev,
        "ci95_low": summary.ci95_low,
        "ci95_high": summary.ci95_high,
        "min": summary.minimum,
        "max": summary.maximum,
    }


def compare(runs_dir: str = RUNS_DIR, window: str = "full",
            workloads: Optional[List[str]] = None,
            variants: Optional[List[str]] = None,
            seeds: Optional[List[int]] = None,
            baseline: str = "A") -> Dict[str, object]:
    """Full comparison payload: per-cell statistics plus baseline deltas."""
    all_rows = collect_run_metrics(runs_dir, window, include_incomplete=True)
    analysable = [r for r in all_rows if r.get("complete")]
    excluded = [r for r in all_rows if not r.get("complete")]

    # Runs of different lengths are not comparable: cumulative metrics scale
    # with duration and the RL variants get more or fewer learning steps. Keep
    # the duration most runs share and report the rest rather than averaging
    # a 30-second dashboard run in with the 90-second matrix.
    duration = modal_duration(analysable)
    analysable, off_duration = split_by_duration(analysable, duration)

    rows = _filter(analysable, workloads, variants, seeds)

    present_workloads = [w for w in WORKLOAD_ORDER
                         if any(r.get("workload") == w for r in rows)]
    present_variants = [v for v in VARIANT_ORDER
                        if any(r.get("variant") == v for r in rows)]

    cells: Dict[str, Dict[str, Dict[str, object]]] = {}
    for workload in present_workloads:
        cells[workload] = {}
        for variant in present_variants:
            subset = [r for r in rows
                      if r.get("workload") == workload and r.get("variant") == variant]
            metric_summaries: Dict[str, object] = {}
            for key in METRIC_INFO:
                values = [r.get(key) for r in subset]
                values = [float(v) for v in values if isinstance(v, (int, float))]
                metric_summaries[key] = _summary_payload(stats.summarise(values))
            cells[workload][variant] = {
                "runs": len(subset),
                "seeds": sorted({int(r.get("seed", -1)) for r in subset}),
                "metrics": metric_summaries,
            }

    # Deltas against the baseline variant, with explicit direction handling.
    deltas: Dict[str, Dict[str, Dict[str, object]]] = {}
    for workload in present_workloads:
        base_cell = cells[workload].get(baseline)
        if not base_cell:
            continue
        deltas[workload] = {}
        for variant in present_variants:
            if variant == baseline:
                continue
            cell = cells[workload][variant]
            metric_deltas: Dict[str, object] = {}
            for key, info in METRIC_INFO.items():
                base = base_cell["metrics"][key]
                other = cell["metrics"][key]
                metric_deltas[key] = _delta(base, other, bool(info["lower_is_better"]))
            deltas[workload][variant] = metric_deltas

    return {
        "source": DataSource.HISTORICAL.value,
        "window": window,
        "baseline": baseline,
        "workloads": present_workloads,
        "variants": present_variants,
        "metric_info": METRIC_INFO,
        "cells": cells,
        "deltas": deltas,
        "coverage": _coverage(rows),
        "runs_analysed": len(rows),
        "runs_excluded_incomplete": len(excluded),
        "comparable_duration_s": duration,
        "runs_excluded_off_duration": [
            {"run_id": r.get("run_id"), "variant": r.get("variant"),
             "workload": r.get("workload"), "seed": r.get("seed"),
             "duration_s": r.get("duration_s")}
            for r in off_duration
        ],
        "excluded_runs": [
            {"run_id": r.get("run_id"), "variant": r.get("variant"),
             "workload": r.get("workload"), "seed": r.get("seed")}
            for r in excluded
        ],
        "note": (
            "Computed with the project's own analysis code "
            "(analysis.aggregate / analysis.stats), so these figures match the "
            "generated report by construction."
        ),
    }


def _delta(base: Dict[str, object], other: Dict[str, object],
           lower_is_better: bool) -> Dict[str, object]:
    """Relative change from baseline, with an honest verdict."""
    base_median = base.get("median")
    other_median = other.get("median")
    if base.get("n", 0) == 0 or other.get("n", 0) == 0:
        return {"available": False,
                "reason": "metric missing for one of the variants"}
    if not isinstance(base_median, (int, float)) or not isinstance(other_median, (int, float)):
        return {"available": False, "reason": "no median available"}
    if base_median == 0:
        return {"available": False,
                "reason": "baseline is zero; relative change undefined"}

    change = (other_median - base_median) / abs(base_median)
    improved = (change < 0) if lower_is_better else (change > 0)

    base_summary = stats.Summary(
        n=int(base["n"]), mean=base.get("mean"), median=base_median,
        ci95_low=base.get("ci95_low"), ci95_high=base.get("ci95_high"),
    )
    other_summary = stats.Summary(
        n=int(other["n"]), mean=other.get("mean"), median=other_median,
        ci95_low=other.get("ci95_low"), ci95_high=other.get("ci95_high"),
    )
    overlap = stats.ci_overlap(base_summary, other_summary)

    if overlap is True:
        verdict = "not_distinguishable"
        detail = (f"{abs(change):.1%} "
                  f"{'better' if improved else 'worse'} direction, but the 95% "
                  f"confidence intervals overlap at n={min(base_summary.n, other_summary.n)}")
    elif overlap is False:
        verdict = "improved" if improved else "regressed"
        detail = (f"{abs(change):.1%} "
                  f"{'better' if improved else 'worse'}; 95% intervals do not overlap")
    else:
        verdict = "indeterminate"
        detail = "too few observations to form a confidence interval"

    return {
        "available": True,
        "relative_change": change,
        "improved": improved,
        "verdict": verdict,
        "detail": detail,
        "ci_overlap": overlap,
        "baseline_median": base_median,
        "variant_median": other_median,
    }


def _coverage(rows: List[Dict[str, object]]) -> Dict[str, object]:
    matrix: Dict[str, Dict[str, List[int]]] = {}
    for row in rows:
        workload = str(row.get("workload"))
        variant = str(row.get("variant"))
        seed = row.get("seed")
        bucket = matrix.setdefault(workload, {}).setdefault(variant, [])
        if isinstance(seed, int):
            bucket.append(seed)
    return {
        workload: {variant: sorted(set(seeds)) for variant, seeds in variants.items()}
        for workload, variants in matrix.items()
    }


def analysis_artifacts() -> Dict[str, object]:
    """Figures and tables already produced by the analysis scripts."""
    items: List[Dict[str, object]] = []
    if os.path.isdir(ANALYSIS_OUT):
        for name in sorted(os.listdir(ANALYSIS_OUT)):
            path = os.path.join(ANALYSIS_OUT, name)
            if not os.path.isfile(path):
                continue
            items.append({
                "name": name,
                "kind": ("figure" if name.endswith(".png")
                         else "table" if name.endswith(".csv")
                         else "report" if name.endswith(".md")
                         else "data"),
                "bytes": os.path.getsize(path),
                "modified": os.path.getmtime(path),
            })
    return {
        "available": bool(items),
        "directory": ANALYSIS_OUT,
        "items": items,
        "note": ("Generated by analysis/aggregate.py, verify_behaviour.py, "
                 "plots.py and report.py. Regenerate them to refresh."),
    }
