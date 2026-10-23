"""Figures for the report, generated from the aggregated metrics.

Reads ``analysis/out/per_run_metrics.csv`` (written by ``analysis/aggregate.py``)
and the raw decision logs; writes PNGs into ``analysis/out/``.  Nothing is drawn
from numbers typed by hand.

Charting conventions followed here:

* Three series (variants A, B, C) get three fixed categorical hues, assigned in
  slot order and never cycled or re-assigned when a chart drops a series.
  The palette is the validated default three-slot set.
* Every bar carries a visible value label. That is required, not decorative:
  one of the three hues sits below 3:1 contrast on a white surface, and the
  relief rule for that is visible labels or a table view. We ship both.
* Error bars are 95% confidence intervals and individual seed values are drawn
  as dots on top. With five seeds per cell, showing the actual observations
  matters more than the summary -- a bar alone would imply more precision than
  five runs can support.
* One y-axis per panel, never two. Grid and axes are recessive.

Run::

    python3 -m analysis.plots [--out analysis/out]
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Sequence, Tuple

import matplotlib
matplotlib.use("Agg")  # no display in WSL; render straight to file
import matplotlib.pyplot as plt  # noqa: E402

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from analysis import stats  # noqa: E402
from analysis.parsers import discover_runs, read_csv, to_float  # noqa: E402

# ---- design tokens -------------------------------------------------------
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
TEXT_MUTED = "#8a8982"
GRID = "#e3e2dd"

#: Fixed categorical slots 1-3. Variant -> hue, assigned by identity so a chart
#: that omits a variant never repaints the others.
VARIANT_COLOR: Dict[str, str] = {
    "A": "#2a78d6",  # blue
    "B": "#eb6834",  # orange
    "C": "#1baf7a",  # aqua
}
VARIANT_ORDER = ("A", "B", "C")
VARIANT_LEGEND = {
    "A": "A - conventional + fixed timeouts",
    "B": "B - RL routing + fixed timeouts",
    "C": "C - joint RL routing + timeouts",
}
WORKLOAD_ORDER = ("light", "heavy", "churn", "failure")


def _style_axes(ax) -> None:
    ax.set_facecolor(SURFACE)
    ax.grid(True, axis="y", color=GRID, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(GRID)
        ax.spines[side].set_linewidth(0.8)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=8, length=0)
    ax.yaxis.label.set_color(TEXT_SECONDARY)
    ax.xaxis.label.set_color(TEXT_SECONDARY)


def load_metrics(out_dir: str, window: str = "full") -> List[Dict[str, str]]:
    path = os.path.join(out_dir, "per_run_metrics.csv")
    if not os.path.exists(path):
        raise SystemExit(f"missing {path}; run analysis/aggregate.py first")
    return [r for r in read_csv(path) if r.get("window") == window]


def cell_values(rows: Sequence[Dict[str, str]], workload: str, variant: str,
                metric: str) -> List[float]:
    out = []
    for r in rows:
        if r.get("workload") == workload and r.get("variant") == variant:
            v = to_float(r.get(metric, ""))
            if v is not None:
                out.append(v)
    return out


# --------------------------------------------------------------------------
# Figure 1: grouped comparison across workloads
# --------------------------------------------------------------------------

PANELS: Tuple[Tuple[str, str, float, str], ...] = (
    ("throughput_of_bps", "Throughput", 1e6, "Mbit/s"),
    ("rtt_median_ms", "Latency (median RTT)", 1.0, "ms"),
    ("mean_rules", "Flow-table occupancy", 1.0, "rules"),
    ("reactive_msgs_total", "Controller load (reactive msgs)", 1.0, "messages"),
)


def figure_comparison(rows: List[Dict[str, str]], out_path: str,
                      window_label: str) -> Optional[str]:
    workloads = [w for w in WORKLOAD_ORDER
                 if any(r.get("workload") == w for r in rows)]
    if not workloads:
        return None

    fig, axes = plt.subplots(2, 2, figsize=(11.5, 7.5), facecolor=SURFACE)
    fig.suptitle(
        f"Variant comparison by workload  ({window_label})",
        color=TEXT_PRIMARY, fontsize=13, fontweight="bold", x=0.02, ha="left", y=0.98,
    )
    fig.text(0.02, 0.935,
             "Bars are means with 95% confidence intervals; dots are individual seeds. "
             "n is shown under each bar.",
             color=TEXT_SECONDARY, fontsize=8.5, ha="left")

    width = 0.26
    for ax, (metric, title, scale, unit) in zip(axes.flat, PANELS):
        _style_axes(ax)
        ax.set_title(title, color=TEXT_PRIMARY, fontsize=10.5,
                     fontweight="semibold", loc="left", pad=8)
        ax.set_ylabel(unit, fontsize=8.5)

        xs = range(len(workloads))
        any_data = False
        for i, variant in enumerate(VARIANT_ORDER):
            offset = (i - 1) * width
            centres, means, errs, ns = [], [], [], []
            for xi, workload in zip(xs, workloads):
                values = [v / scale for v in cell_values(rows, workload, variant, metric)]
                summary = stats.summarise(values)
                centres.append(xi + offset)
                means.append(summary.mean if summary.mean is not None else 0.0)
                if summary.ci95_low is not None:
                    errs.append((summary.ci95_high - summary.ci95_low) / 2)
                else:
                    errs.append(0.0)
                ns.append(summary.n)
                if summary.n:
                    any_data = True
                    ax.scatter([xi + offset] * len(values), values, s=9,
                               color=TEXT_PRIMARY, alpha=0.45, zorder=4,
                               linewidths=0)
            ax.bar(centres, means, width=width * 0.88,
                   color=VARIANT_COLOR[variant], zorder=3,
                   label=VARIANT_LEGEND[variant], linewidth=0)
            ax.errorbar(centres, means, yerr=errs, fmt="none",
                        ecolor=TEXT_SECONDARY, elinewidth=1.0, capsize=2.5, zorder=5)
            # Visible value labels: required relief for the low-contrast hue,
            # and they let the figure be read without the table.
            for cx, mean, n in zip(centres, means, ns):
                if n:
                    ax.text(cx, mean, f"{mean:,.1f}", ha="center", va="bottom",
                            fontsize=7, color=TEXT_PRIMARY, zorder=6)
                    # Sample size goes *below* the axis, not inside the bar:
                    # on the bar it was muted ink on a saturated fill and
                    # effectively unreadable.
                    ax.annotate(f"n={n}", xy=(cx, 0), xycoords=("data", "axes fraction"),
                                xytext=(0, -11), textcoords="offset points",
                                ha="center", va="top", fontsize=6,
                                color=TEXT_MUTED, annotation_clip=False)
        ax.set_xticks(list(xs))
        ax.set_xticklabels(workloads, fontsize=9, color=TEXT_SECONDARY)
        ax.tick_params(axis="x", pad=14)  # room for the n= annotations
        if not any_data:
            ax.text(0.5, 0.5, "not measured", transform=ax.transAxes,
                    ha="center", color=TEXT_MUTED, fontsize=10)
        ax.margins(y=0.18)

    handles, labels = axes.flat[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=3, frameon=False,
               fontsize=9, labelcolor=TEXT_SECONDARY, bbox_to_anchor=(0.5, 0.0))
    fig.tight_layout(rect=(0, 0.06, 1, 0.92))
    fig.savefig(out_path, dpi=160, facecolor=SURFACE)
    plt.close(fig)
    return out_path


# --------------------------------------------------------------------------
# Figure 2: learning curves
# --------------------------------------------------------------------------

def figure_learning(runs_root: str, out_path: str) -> Optional[str]:
    """Mean reward per policy step, averaged across seeds.

    Variant A has no agent, but the controller computes the same reward for it,
    so its line is the non-learning reference the RL variants are trying to
    beat -- not an agent that is secretly learning.
    """
    series: Dict[Tuple[str, str], List[List[float]]] = defaultdict(list)
    for run in discover_runs(runs_root):
        if not run.complete:
            continue
        rows = read_csv(os.path.join(run.raw_dir, "decisions.csv"))
        rewards = [to_float(r.get("reward", "")) for r in rows]
        rewards = [r for r in rewards if r is not None]
        if rewards:
            series[(run.workload, run.variant)].append(rewards)
    if not series:
        return None

    workloads = [w for w in WORKLOAD_ORDER if any(k[0] == w for k in series)]
    fig, axes = plt.subplots(1, len(workloads),
                             figsize=(3.6 * len(workloads), 3.6),
                             facecolor=SURFACE, squeeze=False)
    fig.suptitle("Learning curves: mean reward per policy step",
                 color=TEXT_PRIMARY, fontsize=12, fontweight="bold",
                 x=0.02, ha="left")
    fig.text(0.02, 0.88,
             "Mean across seeds, smoothed over a 5-step window. "
             "Variant A does not learn; its line is the non-learning reference.",
             color=TEXT_SECONDARY, fontsize=8, ha="left")

    for ax, workload in zip(axes[0], workloads):
        _style_axes(ax)
        ax.set_title(workload, color=TEXT_PRIMARY, fontsize=10,
                     fontweight="semibold", loc="left")
        ax.set_xlabel("policy step", fontsize=8.5)
        ax.set_ylabel("reward", fontsize=8.5)
        for variant in VARIANT_ORDER:
            runs_list = series.get((workload, variant))
            if not runs_list:
                continue
            length = min(len(r) for r in runs_list)
            if length < 2:
                continue
            mean_curve = [
                sum(r[i] for r in runs_list) / len(runs_list) for i in range(length)
            ]
            window = 5
            smoothed = [
                sum(mean_curve[max(0, i - window + 1): i + 1])
                / len(mean_curve[max(0, i - window + 1): i + 1])
                for i in range(length)
            ]
            ax.plot(range(length), smoothed, color=VARIANT_COLOR[variant],
                    linewidth=2.0, label=VARIANT_LEGEND[variant], zorder=3)

    handles, labels = axes[0][0].get_legend_handles_labels()
    if handles:
        fig.legend(handles, labels, loc="lower center", ncol=3, frameon=False,
                   fontsize=8.5, labelcolor=TEXT_SECONDARY, bbox_to_anchor=(0.5, 0.0))
    fig.tight_layout(rect=(0, 0.1, 1, 0.85))
    fig.savefig(out_path, dpi=160, facecolor=SURFACE)
    plt.close(fig)
    return out_path


# --------------------------------------------------------------------------
# Figure 3: dataplane behaviour (paths and timeouts actually installed)
# --------------------------------------------------------------------------

def figure_behaviour(verification_path: str, out_path: str) -> Optional[str]:
    """What the switches actually held -- the behavioural evidence, charted."""
    if not os.path.exists(verification_path):
        return None
    with open(verification_path, encoding="utf-8") as fh:
        report = json.load(fh)
    by_variant = report.get("by_variant", {})
    if not by_variant:
        return None

    variants = [v for v in VARIANT_ORDER if v in by_variant]
    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.0), facecolor=SURFACE)
    fig.suptitle("What the switches actually held",
                 color=TEXT_PRIMARY, fontsize=12, fontweight="bold",
                 x=0.02, ha="left")
    fig.text(0.02, 0.88,
             "Counted from ovs-ofctl flow-table dumps, not from controller logs.",
             color=TEXT_SECONDARY, fontsize=8, ha="left")

    # Panel 1: share of installed rules on each path.
    ax = axes[0]
    _style_axes(ax)
    ax.set_title("Routing path used by installed rules", color=TEXT_PRIMARY,
                 fontsize=10, fontweight="semibold", loc="left")
    ax.set_ylabel("share of rules", fontsize=8.5)
    for i, variant in enumerate(variants):
        share = by_variant[variant].get("path_share", {})
        a_share = float(share.get("A", 0.0))
        b_share = float(share.get("B", 0.0))
        ax.bar(i, a_share, width=0.5, color=VARIANT_COLOR[variant], zorder=3,
               linewidth=0)
        ax.bar(i, b_share, width=0.5, bottom=a_share,
               color=VARIANT_COLOR[variant], alpha=0.42, zorder=3,
               linewidth=1.5, edgecolor=SURFACE)
        ax.text(i, a_share / 2, f"path A\n{a_share:.0%}", ha="center", va="center",
                fontsize=8, color="#ffffff", fontweight="semibold", zorder=5)
        if b_share > 0.04:
            ax.text(i, a_share + b_share / 2, f"path B\n{b_share:.0%}",
                    ha="center", va="center", fontsize=8,
                    color=TEXT_PRIMARY, fontweight="semibold", zorder=5)
    ax.set_xticks(range(len(variants)))
    ax.set_xticklabels([f"Variant {v}" for v in variants], fontsize=9,
                       color=TEXT_SECONDARY)
    ax.set_ylim(0, 1.12)

    # Panel 2: idle timeouts actually installed.
    ax = axes[1]
    _style_axes(ax)
    ax.set_title("Idle timeouts present on installed rules", color=TEXT_PRIMARY,
                 fontsize=10, fontweight="semibold", loc="left")
    ax.set_ylabel("share of rules", fontsize=8.5)
    all_timeouts: List[int] = sorted({
        int(k) for v in variants
        for k in by_variant[v].get("timeout_counts", {})
    })
    width = 0.8 / max(1, len(variants))
    for i, variant in enumerate(variants):
        counts = {int(k): v for k, v in by_variant[variant].get("timeout_counts", {}).items()}
        total = sum(counts.values()) or 1
        xs = [j + (i - (len(variants) - 1) / 2) * width for j in range(len(all_timeouts))]
        ys = [counts.get(t, 0) / total for t in all_timeouts]
        ax.bar(xs, ys, width=width * 0.9, color=VARIANT_COLOR[variant],
               zorder=3, label=VARIANT_LEGEND[variant], linewidth=0)
        for x, y in zip(xs, ys):
            if y > 0:
                ax.text(x, y, f"{y:.0%}", ha="center", va="bottom", fontsize=7,
                        color=TEXT_PRIMARY, zorder=5)
    ax.set_xticks(range(len(all_timeouts)))
    ax.set_xticklabels([f"{t}s" for t in all_timeouts], fontsize=9,
                       color=TEXT_SECONDARY)
    ax.set_ylim(0, 1.15)
    handles, labels = ax.get_legend_handles_labels()
    fig.legend(handles, labels, loc="lower center", ncol=3, frameon=False,
               fontsize=8.5, labelcolor=TEXT_SECONDARY, bbox_to_anchor=(0.5, 0.0))
    fig.tight_layout(rect=(0, 0.1, 1, 0.85))
    fig.savefig(out_path, dpi=160, facecolor=SURFACE)
    plt.close(fig)
    return out_path


# --------------------------------------------------------------------------
# Figure 4: recovery
# --------------------------------------------------------------------------

def figure_recovery(rows: List[Dict[str, str]], out_path: str) -> Optional[str]:
    variants = [v for v in VARIANT_ORDER
                if cell_values(rows, "failure", v, "recovery_s")]
    if not variants:
        return None

    fig, ax = plt.subplots(figsize=(6.4, 4.0), facecolor=SURFACE)
    _style_axes(ax)
    ax.set_title("Recovery time after link failure (path A cut)",
                 color=TEXT_PRIMARY, fontsize=11.5, fontweight="bold", loc="left")
    ax.set_ylabel("seconds to first successful ping reply", fontsize=8.5)
    fig.text(0.02, 0.90,
             "Measured from the host's own ping stream; dots are individual seeds.",
             color=TEXT_SECONDARY, fontsize=8, ha="left")

    for i, variant in enumerate(variants):
        values = cell_values(rows, "failure", variant, "recovery_s")
        summary = stats.summarise(values)
        err = ((summary.ci95_high - summary.ci95_low) / 2
               if summary.ci95_low is not None else 0.0)
        ax.bar(i, summary.mean or 0.0, width=0.5, color=VARIANT_COLOR[variant],
               zorder=3, linewidth=0, label=VARIANT_LEGEND[variant])
        ax.errorbar([i], [summary.mean or 0.0], yerr=[err], fmt="none",
                    ecolor=TEXT_SECONDARY, elinewidth=1.0, capsize=3, zorder=5)
        ax.scatter([i] * len(values), values, s=14, color=TEXT_PRIMARY,
                   alpha=0.5, zorder=6, linewidths=0)
        ax.text(i, summary.mean or 0.0, f"{summary.mean:.2f}s",
                ha="center", va="bottom", fontsize=8.5, color=TEXT_PRIMARY, zorder=7)
        ax.annotate(f"n={summary.n}", xy=(i, 0), xycoords=("data", "axes fraction"),
                    xytext=(0, -11), textcoords="offset points",
                    ha="center", va="top", fontsize=7,
                    color=TEXT_MUTED, annotation_clip=False)

    ax.set_xticks(range(len(variants)))
    ax.set_xticklabels([f"Variant {v}" for v in variants], fontsize=9,
                       color=TEXT_SECONDARY)
    ax.margins(y=0.2)
    fig.tight_layout(rect=(0, 0, 1, 0.88))
    fig.savefig(out_path, dpi=160, facecolor=SURFACE)
    plt.close(fig)
    return out_path


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default=os.path.join(PROJECT_ROOT, "analysis", "out"))
    p.add_argument("--runs", default=os.path.join(PROJECT_ROOT, "results", "runs"))
    args = p.parse_args(argv)

    os.makedirs(args.out, exist_ok=True)
    written: List[str] = []

    for window, label in (("full", "full run"), ("second_half", "second half")):
        rows = load_metrics(args.out, window)
        if not rows:
            continue
        path = figure_comparison(
            rows, os.path.join(args.out, f"fig_comparison_{window}.png"), label
        )
        if path:
            written.append(path)
        if window == "full":
            r = figure_recovery(rows, os.path.join(args.out, "fig_recovery.png"))
            if r:
                written.append(r)

    learning = figure_learning(args.runs, os.path.join(args.out, "fig_learning.png"))
    if learning:
        written.append(learning)

    behaviour = figure_behaviour(
        os.path.join(args.out, "behaviour_verification.json"),
        os.path.join(args.out, "fig_behaviour.png"),
    )
    if behaviour:
        written.append(behaviour)

    for path in written:
        print(f"  wrote {path}")
    if not written:
        print("no figures written (no data yet)")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
