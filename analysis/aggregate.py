"""Turn raw run directories into per-run metrics and comparison tables.

Reads only ``results/runs/*`` and writes only into ``analysis/out/``.  No run
directory is ever modified, so every published number can be traced back to an
untouched measurement file, and a change here never costs an experiment.

Missing values stay missing.  If a metric could not be measured for a run, it
is ``None``, excluded from the statistics, and counted in the reported ``n``.
Nothing is interpolated and nothing is defaulted to zero.

Two measurement windows are reported for every run:

``full``        the whole run.
``second_half`` the second half only. The RL variants spend the first half
                exploring (epsilon decays over it), so the second half is the
                closer look at the policy they actually converged on. Both are
                reported; neither is hidden.

Run::

    python3 -m analysis.aggregate [--runs results/runs] [--out analysis/out]
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from analysis import stats  # noqa: E402
from analysis.parsers import (  # noqa: E402
    Run, discover_runs, parse_all_iperf3, parse_ping, read_csv,
    recovery_window, to_float,
)

VARIANT_ORDER = ("A", "B", "C")
WORKLOAD_ORDER = ("light", "heavy", "churn", "failure")

VARIANT_LABEL = {
    "A": "A: conventional + fixed timeouts",
    "B": "B: RL routing + fixed timeouts",
    "C": "C: joint RL routing + RL timeouts",
}

#: Metrics reported in the comparison tables.
#: (key, human label, unit scale, digits, "higher is better"?)
METRICS: Tuple[Tuple[str, str, float, int, Optional[bool]], ...] = (
    ("throughput_of_bps",    "Throughput (OF counters)",   1e6, 2, True),
    ("throughput_iperf_bps", "Throughput (iperf3)",        1e6, 2, True),
    ("rtt_median_ms",        "Latency, median RTT",        1.0, 2, False),
    ("rtt_p95_ms",           "Latency, p95 RTT",           1.0, 2, False),
    ("ping_loss_fraction",   "Packet loss (ping)",         0.01, 2, False),
    ("ping_delayed_fraction", "Probes delayed past 200 ms", 0.01, 1, False),
    ("of_loss_fraction",     "Packet loss (OF counters)",  0.01, 3, False),
    ("max_link_util",        "Max link utilisation",       0.01, 1, None),
    ("mean_rules",           "Flow-table occupancy (rules)", 1.0, 1, False),
    ("rule_hit_ratio",       "Rule-hit ratio",             0.01, 2, True),
    ("table_misses_total",   "Table misses (total)",       1.0, 0, False),
    ("reactive_msgs_total",  "Controller messages (reactive)", 1.0, 0, False),
    ("recovery_s",           "Recovery time",              1.0, 2, False),
)


# --------------------------------------------------------------------------
# Per-run metric extraction
# --------------------------------------------------------------------------

def _window_rows(rows: List[Dict[str, str]], t0: float, t1: Optional[float]
                 ) -> List[Dict[str, str]]:
    out = []
    for row in rows:
        t = to_float(row.get("t", ""))
        if t is None or t < t0:
            continue
        if t1 is not None and t > t1:
            continue
        out.append(row)
    return out


def _mean(values: Sequence[Optional[float]]) -> Optional[float]:
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def snapshot_metrics(run: Run, window: str) -> Dict[str, Optional[float]]:
    """Metrics derived from the controller's own OpenFlow telemetry."""
    rows = read_csv(os.path.join(run.raw_dir, "snapshots.csv"))
    # Invalid intervals (counter resets) are excluded rather than clamped.
    rows = [r for r in rows if r.get("valid") == "1"]
    if not rows:
        return {}

    times = [to_float(r["t"]) for r in rows]
    times = [t for t in times if t is not None]
    if not times:
        return {}
    t_start, t_end = min(times), max(times)
    if window == "second_half":
        rows = _window_rows(rows, t_start + (t_end - t_start) / 2, None)
    if not rows:
        return {}

    delivered = [to_float(r["delivered_bps"]) for r in rows]
    util = [to_float(r["max_link_util"]) for r in rows]
    loss = [to_float(r["loss_fraction"]) for r in rows]
    rules = [to_float(r["total_rules"]) for r in rows]
    hit = [to_float(r.get("rule_hit_ratio", "")) for r in rows]
    misses = [to_float(r["table_misses"]) for r in rows]
    pin = [to_float(r["packet_in_rate"]) for r in rows]
    fmod = [to_float(r["flow_mod_rate"]) for r in rows]
    dt = [to_float(r["dt"]) for r in rows]

    reactive_total = None
    pairs = [(p, f, d) for p, f, d in zip(pin, fmod, dt)
             if p is not None and f is not None and d is not None]
    if pairs:
        reactive_total = sum((p + f) * d for p, f, d in pairs)

    return {
        "throughput_of_bps": _mean(delivered),
        "max_link_util": _mean(util),
        "of_loss_fraction": _mean(loss),
        "mean_rules": _mean(rules),
        "rule_hit_ratio": _mean(hit),
        "table_misses_total": sum(m for m in misses if m is not None) or 0.0,
        "reactive_msgs_total": reactive_total,
        "snapshot_intervals": float(len(rows)),
    }


def host_metrics(run: Run, window: str) -> Dict[str, Optional[float]]:
    """Metrics measured by the hosts -- iperf3 and ping.

    These are the independent check on the controller's own numbers. The agent
    never sees them, so an improvement here is an out-of-sample result.
    """
    out: Dict[str, Optional[float]] = {}

    iperf = parse_all_iperf3(run.raw_dir)
    ok = [r for r in iperf if r.ok and r.bits_per_second is not None]
    out["iperf_flows_total"] = float(len(iperf))
    out["iperf_flows_ok"] = float(len(ok))
    out["iperf_flows_failed"] = float(len(iperf) - len(ok))

    if ok:
        duration = to_float(str(run.config.get("duration_s", ""))) or 1.0
        total_bits = sum((r.bytes_received or 0) * 8 for r in ok)
        # Aggregate goodput over the run: total delivered bits / run duration.
        # Summing per-flow rates would overstate it for workloads whose flows
        # do not all overlap (churn and the failure segments).
        out["throughput_iperf_bps"] = total_bits / duration
    else:
        out["throughput_iperf_bps"] = None

    ping = parse_ping(os.path.join(run.raw_dir, "ping_h1.txt"))
    if ping.ok and ping.replies:
        replies = ping.replies
        if window == "second_half":
            times = [t for t, _, _ in replies]
            mid = min(times) + (max(times) - min(times)) / 2
            replies = [r for r in replies if r[0] >= mid]
        rtts = [r[2] for r in replies]
        out["rtt_median_ms"] = stats.percentile(rtts, 0.5)
        out["rtt_p95_ms"] = stats.percentile(rtts, 0.95)
        out["ping_replies"] = float(len(rtts))
        out["ping_loss_fraction"] = ping.loss_fraction
        # Recorded alongside loss, never folded into it: a probe answered
        # after the next one was due was delivered, not lost. It is a real
        # symptom of queueing and is reported as its own quantity.
        out["ping_delayed_fraction"] = ping.delayed_fraction
        out["ping_sent"] = float(ping.sent)
        out["ping_lost"] = float(ping.lost)
        out["ping_delayed"] = float(ping.delayed)
    else:
        out["rtt_median_ms"] = None
        out["rtt_p95_ms"] = None
        out["ping_replies"] = 0.0
        out["ping_loss_fraction"] = None
        out["ping_delayed_fraction"] = None
        out["ping_sent"] = None
        out["ping_lost"] = None
        out["ping_delayed"] = None

    return out


def recovery_metrics(run: Run) -> Dict[str, Optional[float]]:
    """Recovery time for the failure workload; empty for every other."""
    if run.workload != "failure":
        return {}
    workload_path = os.path.join(run.path, "workload.json")
    if not os.path.exists(workload_path):
        return {}
    try:
        with open(workload_path, encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError:
        return {}

    failure_t = None
    for event in data.get("events", []):
        if event.get("kind") == "link_down":
            failure_t = event.get("t")
            break
    if failure_t is None:
        return {"recovery_s": None, "recovery_note": None}

    ping = parse_ping(os.path.join(run.raw_dir, "ping_h1.txt"))
    window = recovery_window(ping, float(failure_t))
    return {
        "recovery_s": window["first_reply_after_failure_s"],
        "recovery_lost_probes": window["lost_probes_in_outage"],
        "recovery_max_gap_s": window["max_gap_s"],
    }


def learning_metrics(run: Run) -> Dict[str, Optional[float]]:
    """Agent-side diagnostics: reward trend, exploration, state coverage."""
    rows = read_csv(os.path.join(run.raw_dir, "decisions.csv"))
    out: Dict[str, Optional[float]] = {
        "policy_steps": float(len(rows)) if rows else 0.0,
        "mean_reward": None,
        "reward_first_half": None,
        "reward_second_half": None,
        "reward_delta": None,
        "exploration_fraction": None,
        "visited_states": None,
        "state_coverage": None,
    }
    rewards = [to_float(r.get("reward", "")) for r in rows]
    rewards = [r for r in rewards if r is not None]
    if rewards:
        out["mean_reward"] = sum(rewards) / len(rewards)
        mid = len(rewards) // 2
        if mid >= 1:
            first = rewards[:mid]
            second = rewards[mid:]
            out["reward_first_half"] = sum(first) / len(first)
            out["reward_second_half"] = sum(second) / len(second)
            out["reward_delta"] = out["reward_second_half"] - out["reward_first_half"]

    explor = [r.get("exploratory", "") for r in rows]
    flags = [v for v in explor if v in ("0", "1")]
    if flags:
        out["exploration_fraction"] = sum(1 for v in flags if v == "1") / len(flags)

    policy_path = os.path.join(run.path, "policy.json")
    if os.path.exists(policy_path):
        try:
            with open(policy_path, encoding="utf-8") as fh:
                policy = json.load(fh)
            out["visited_states"] = float(policy.get("visited_states", 0))
            out["state_coverage"] = float(policy.get("coverage", 0.0))
            out["distinct_greedy_actions"] = float(
                len(set(policy.get("greedy_policy", {}).values()))
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    return out


def run_metrics(run: Run, window: str) -> Dict[str, object]:
    row: Dict[str, object] = {
        "run": os.path.basename(run.path),
        "variant": run.variant,
        "workload": run.workload,
        "seed": run.seed,
        "window": window,
        "complete": int(run.complete),
        "duration_s": run.config.get("duration_s"),
    }
    row.update(snapshot_metrics(run, window))
    row.update(host_metrics(run, window))
    row.update(recovery_metrics(run))
    if window == "full":
        row.update(learning_metrics(run))
    return row


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

def modal_duration(rows: List[Dict[str, object]]) -> Optional[float]:
    """The duration most runs share, which defines the comparable population."""
    counts: Dict[float, int] = defaultdict(int)
    for row in rows:
        value = row.get("duration_s")
        if isinstance(value, (int, float)):
            counts[float(value)] += 1
    if not counts:
        return None
    return max(counts.items(), key=lambda kv: (kv[1], kv[0]))[0]


def split_by_duration(rows: List[Dict[str, object]], duration: Optional[float]
                      ) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    """Separate runs of the comparable duration from the rest.

    Every metric here depends on how long the run lasted -- total table
    misses and controller messages are cumulative, and the RL variants have
    had more or fewer learning steps. Averaging a 30-second ad-hoc run from
    the dashboard together with the 90-second matrix runs would quietly
    compare different experiments. The odd ones out are reported, not hidden.
    """
    if duration is None:
        return rows, []
    keep, drop = [], []
    for row in rows:
        value = row.get("duration_s")
        (keep if isinstance(value, (int, float)) and float(value) == duration
         else drop).append(row)
    return keep, drop


def aggregate(rows: List[Dict[str, object]], window: str
              ) -> Dict[Tuple[str, str], Dict[str, stats.Summary]]:
    """Group per-run metrics by (workload, variant) and summarise each cell."""
    grouped: Dict[Tuple[str, str], List[Dict[str, object]]] = defaultdict(list)
    for row in rows:
        if row.get("window") != window:
            continue
        grouped[(str(row["workload"]), str(row["variant"]))].append(row)

    out: Dict[Tuple[str, str], Dict[str, stats.Summary]] = {}
    for key, cell_rows in grouped.items():
        summaries: Dict[str, stats.Summary] = {}
        for metric, _, _, _, _ in METRICS:
            values = [r.get(metric) for r in cell_rows]
            values = [float(v) for v in values if isinstance(v, (int, float))]
            summaries[metric] = stats.summarise(values)
        summaries["_runs"] = stats.Summary(n=len(cell_rows))
        out[key] = summaries
    return out


def write_per_run_csv(rows: List[Dict[str, object]], path: str) -> None:
    if not rows:
        return
    fields: List[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def write_summary_csv(agg: Dict[Tuple[str, str], Dict[str, stats.Summary]],
                      window: str, path: str) -> None:
    fields = ["workload", "variant", "window", "metric",
              "n", "mean", "stdev", "median", "q1", "q3",
              "ci95_low", "ci95_high", "min", "max"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for (workload, variant) in sorted(agg):
            for metric, _, _, _, _ in METRICS:
                s = agg[(workload, variant)][metric]
                row = {"workload": workload, "variant": variant,
                       "window": window, "metric": metric}
                row.update({k.rstrip("_"): v for k, v in s.as_dict().items()})
                writer.writerow({k: row.get(k) for k in fields})


def markdown_tables(agg: Dict[Tuple[str, str], Dict[str, stats.Summary]],
                    window: str) -> str:
    lines: List[str] = []
    lines.append(f"## Results ({window.replace('_', ' ')} window)\n")
    lines.append(
        "Each cell is the median across seeds with the interquartile range in "
        "brackets. `n` is the number of runs that produced the metric; a blank "
        "cell means it was not measurable for any run in that group.\n"
    )
    workloads = [w for w in WORKLOAD_ORDER if any(k[0] == w for k in agg)]
    for workload in workloads:
        variants = [v for v in VARIANT_ORDER if (workload, v) in agg]
        if not variants:
            continue
        lines.append(f"\n### Workload: `{workload}`\n")
        header = "| Metric | " + " | ".join(f"{v} (n)" for v in variants) + " |"
        sep = "|---" * (len(variants) + 1) + "|"
        lines.append(header)
        lines.append(sep)
        for metric, label, scale, digits, _ in METRICS:
            cells = []
            any_value = False
            for variant in variants:
                s = agg[(workload, variant)][metric]
                if s.n == 0:
                    cells.append("-")
                else:
                    any_value = True
                    cells.append(f"{s.format_median_iqr(scale, digits)} ({s.n})")
            if any_value:
                unit = {1e6: " Mbit/s", 0.01: " %"}.get(scale, "")
                lines.append(f"| {label}{unit} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runs", default=os.path.join(PROJECT_ROOT, "results", "runs"))
    p.add_argument("--out", default=os.path.join(PROJECT_ROOT, "analysis", "out"))
    p.add_argument("--include-incomplete", action="store_true")
    args = p.parse_args(argv)

    all_runs = discover_runs(args.runs)
    runs = all_runs if args.include_incomplete else [r for r in all_runs if r.complete]
    excluded = len(all_runs) - len(runs)
    if not runs:
        print(f"no usable runs under {args.runs} ({len(all_runs)} found, {excluded} excluded)")
        return 1

    os.makedirs(args.out, exist_ok=True)

    rows: List[Dict[str, object]] = []
    for run in runs:
        for window in ("full", "second_half"):
            rows.append(run_metrics(run, window))

    # Only runs of the same duration are comparable; see split_by_duration.
    duration = modal_duration(rows)
    rows, off_duration = split_by_duration(rows, duration)
    off_duration_runs = sorted({str(r.get("run")) for r in off_duration})
    if off_duration_runs:
        print(f"excluding {len(off_duration_runs)} run(s) whose duration is not "
              f"{duration:g}s: {', '.join(off_duration_runs[:4])}"
              + (" ..." if len(off_duration_runs) > 4 else ""))

    write_per_run_csv(rows, os.path.join(args.out, "per_run_metrics.csv"))

    md_parts: List[str] = []
    counts: Dict[str, Dict[str, int]] = {}
    for window in ("full", "second_half"):
        agg = aggregate(rows, window)
        write_summary_csv(agg, window, os.path.join(args.out, f"summary_{window}.csv"))
        md_parts.append(markdown_tables(agg, window))
        if window == "full":
            for (workload, variant), cell in agg.items():
                counts.setdefault(workload, {})[variant] = cell["_runs"].n

    provenance = {
        "runs_found": len(all_runs),
        "runs_analysed": len(runs),
        "runs_excluded_incomplete": excluded,
        "comparable_duration_s": duration,
        "runs_excluded_off_duration": off_duration_runs,
        "runs_per_cell": counts,
    }
    with open(os.path.join(args.out, "aggregate_provenance.json"), "w",
              encoding="utf-8") as fh:
        json.dump(provenance, fh, indent=2, sort_keys=True)

    header = [
        "# Aggregated results\n",
        f"- Runs found: **{len(all_runs)}**",
        f"- Runs analysed: **{len(runs)}**",
        f"- Runs excluded as incomplete: **{excluded}**\n",
        "Generated by `analysis/aggregate.py` from `results/runs/`. "
        "Every value is computed from raw measurement files; none is entered by hand.\n",
    ]
    with open(os.path.join(args.out, "results.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(header) + "\n" + "\n\n".join(md_parts) + "\n")

    print(f"runs found={len(all_runs)} analysed={len(runs)} excluded={excluded}")
    for workload in WORKLOAD_ORDER:
        if workload in counts:
            print(f"  {workload:8s}: " +
                  ", ".join(f"{v}={counts[workload].get(v, 0)}" for v in VARIANT_ORDER))
    print(f"wrote per_run_metrics.csv, summary_*.csv, results.md -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
