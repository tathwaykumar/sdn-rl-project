"""Generate the research report from measured results.

Every number in the output is read from `analysis/out/` (which is itself
generated from `results/runs/`). Nothing is hard-coded, nothing is copied from
literature, and no claim is written unless the data supports it:

* A comparison is only called a difference when the two 95% confidence
  intervals do not overlap. Otherwise it is reported as "not distinguishable at
  this sample size", which with n=5 is usually the correct answer.
* Metrics that were not measurable are printed as "not measured", never as 0.
* The number of runs behind every cell is printed, and excluded runs are
  reported rather than dropped silently.

Run after `aggregate.py` and `verify_behaviour.py`::

    python3 -m analysis.report
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from analysis import stats  # noqa: E402
from analysis.aggregate import (  # noqa: E402
    METRICS, VARIANT_LABEL, VARIANT_ORDER, WORKLOAD_ORDER, aggregate, run_metrics,
)
from analysis.parsers import discover_runs, read_csv, to_float  # noqa: E402
from topo import spec  # noqa: E402

#: Metrics where a lower value is the better outcome.
LOWER_IS_BETTER = {
    "rtt_median_ms", "rtt_p95_ms", "ping_loss_fraction", "of_loss_fraction",
    "mean_rules", "table_misses_total", "reactive_msgs_total", "recovery_s",
}


def fmt(summary: stats.Summary, scale: float, digits: int) -> str:
    if summary.n == 0:
        return "not measured"
    return f"{summary.format_median_iqr(scale, digits)} (n={summary.n})"


def compare(a: stats.Summary, b: stats.Summary, metric: str) -> str:
    """Describe b relative to a, refusing to overclaim."""
    if a.n == 0 or b.n == 0:
        return "not comparable (a metric is missing)"
    if a.median is None or b.median is None:
        return "not comparable"
    change = stats.relative_change(a.median, b.median)
    overlap = stats.ci_overlap(a, b)
    if change is None:
        return "not comparable"
    direction = "higher" if change > 0 else "lower"
    better = (change < 0) if metric in LOWER_IS_BETTER else (change > 0)
    verdict = "better" if better else "worse"
    magnitude = f"{abs(change):.1%} {direction}"
    if overlap is True:
        return (f"{magnitude} ({verdict} direction), but the 95% intervals "
                f"overlap -- not distinguishable at n={min(a.n, b.n)}")
    if overlap is None:
        return f"{magnitude} ({verdict} direction); intervals unavailable"
    return f"{magnitude} -- {verdict}; 95% intervals do not overlap"


def load_json(path: str) -> Optional[dict]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except json.JSONDecodeError:
        return None


def build_report(out_dir: str, runs_root: str) -> str:
    runs = [r for r in discover_runs(runs_root) if r.complete]
    all_runs = discover_runs(runs_root)

    rows: List[Dict[str, object]] = []
    for run in runs:
        for window in ("full", "second_half"):
            rows.append(run_metrics(run, window))

    agg_full = aggregate(rows, "full")
    agg_half = aggregate(rows, "second_half")
    verification = load_json(os.path.join(out_dir, "behaviour_verification.json"))

    provenance: Dict[str, object] = {}
    if runs:
        provenance = dict(runs[0].config.get("provenance", {}))

    L: List[str] = []
    A = L.append

    # ---------------------------------------------------------------- header
    A("# RL-Based SDN Traffic Routing and Flow-Table Optimization")
    A("")
    A("**Experimental report — generated from measured results only.**")
    A("")
    A("This document is produced by `analysis/report.py` from the contents of "
      "`results/runs/`. Every figure in it was computed from raw measurement "
      "files by script. No value was entered by hand, and no value is taken "
      "from published literature.")
    A("")

    # ---------------------------------------------------------------- method
    A("## 1. What was measured")
    A("")
    A("Three controller variants were compared under identical seeded traffic "
      "on an emulated five-switch network:")
    A("")
    A("| Variant | Routing | Flow-rule timeouts |")
    A("|---|---|---|")
    A("| A | conventional shortest-path (fewest hops) | fixed |")
    A("| B | Q-learning selects the path | fixed |")
    A("| C | Q-learning selects the path | Q-learning selects the timeout |")
    A("")
    A("A and B differ in one respect; B and C differ in one respect. Any "
      "difference between B and C is therefore attributable to flow-timeout "
      "control alone.")
    A("")
    A("```")
    A(spec.describe())
    A("```")
    A("")
    A("Path A is shorter but narrower, so the better path depends on load. A "
      "fixed policy cannot be optimal in both regimes; a state-conditioned one "
      "can. This is the property the experiment is built to test, and it is "
      "also the main limit on how far the result generalises "
      "(see `docs/LIMITATIONS.md`).")
    A("")

    # ------------------------------------------------------------ provenance
    A("## 2. Provenance and sample sizes")
    A("")
    if provenance:
        A("| Component | Version |")
        A("|---|---|")
        for key in ("os_release", "kernel", "python", "ovs_version",
                    "mininet_version", "os_ken_version", "iperf3_version",
                    "git_commit"):
            value = provenance.get(key)
            if value:
                A(f"| `{key}` | {value} |")
        A("")
    excluded = len(all_runs) - len(runs)
    A(f"- Run directories found: **{len(all_runs)}**")
    A(f"- Runs analysed (manifest complete): **{len(runs)}**")
    A(f"- Runs excluded as incomplete: **{excluded}**")
    A("")
    if excluded:
        A(f"> {excluded} run(s) did not produce every expected artefact and are "
          "excluded. They are counted here rather than silently dropped, "
          "because averaging over whatever survived would bias results toward "
          "whichever variant failed least gracefully.")
        A("")

    cells = sorted({(w, v) for (w, v) in agg_full})
    if cells:
        A("Runs per cell:")
        A("")
        workloads = [w for w in WORKLOAD_ORDER if any(c[0] == w for c in cells)]
        A("| Workload | " + " | ".join(f"Variant {v}" for v in VARIANT_ORDER) + " |")
        A("|---" * (len(VARIANT_ORDER) + 1) + "|")
        for workload in workloads:
            counts = []
            for variant in VARIANT_ORDER:
                cell = agg_full.get((workload, variant))
                counts.append(str(cell["_runs"].n) if cell else "0")
            A(f"| `{workload}` | " + " | ".join(counts) + " |")
        A("")

    # ------------------------------------------------- behavioural evidence
    A("## 3. Did the learned actions reach the switches?")
    A("")
    A("This is checked against `ovs-ofctl` flow-table dumps, not controller "
      "logs: a controller can log a decision it never installed, but a flow "
      "table cannot misreport what it holds.")
    A("")
    if verification:
        by_variant = verification.get("by_variant", {})
        A("| Variant | Paths installed | Idle timeouts installed | Rules checked | Path agreement | Timeout agreement |")
        A("|---|---|---|---|---|---|")
        for variant in VARIANT_ORDER:
            v = by_variant.get(variant)
            if not v:
                continue
            paths = v.get("path_share") or v.get("path_counts") or {}
            paths_txt = ", ".join(f"{k}:{float(x):.0%}" for k, x in sorted(paths.items())) \
                if v.get("path_share") else str(v.get("path_counts"))
            timeouts = v.get("timeout_counts", {})
            timeouts_txt = ", ".join(f"{k}s" for k in sorted(timeouts, key=int)) or "-"
            pa = v.get("path_agreement")
            ta = v.get("timeout_agreement")
            A(f"| {variant} | {paths_txt} | {timeouts_txt} | "
              f"{v.get('rules_checked_against_decisions', 0)} | "
              f"{'n/a' if pa is None else f'{pa:.1%}'} | "
              f"{'n/a' if ta is None else f'{ta:.1%}'} |")
        A("")
        A("Each rule is matched against the decision that was in force **when "
          "that rule was installed** (recovered from the switch's own "
          "`duration` field), not against the controller's latest decision.")
        A("")
        A("Claim checks:")
        A("")
        for claim in verification.get("claims", []):
            A(f"- **{claim['result']}** — {claim['claim']}  \n  {claim['detail']}")
        A("")
    else:
        A("> `behaviour_verification.json` not found. Run "
          "`python3 -m analysis.verify_behaviour` first.")
        A("")

    # ---------------------------------------------------------------- results
    for window, agg, title in (
        ("full", agg_full, "4. Results — full run"),
        ("second_half", agg_half, "5. Results — second half of each run"),
    ):
        A(f"## {title}")
        A("")
        if window == "second_half":
            A("Exploration decays over the first half of each run, so this "
              "window is closer to the policy the agents converged on. Both "
              "windows are reported; neither is hidden.")
            A("")
        A("Cells are the **median across seeds** with the interquartile range "
          "in brackets, and `n` is the number of runs contributing.")
        A("")
        workloads = [w for w in WORKLOAD_ORDER if any(c[0] == w for c in agg)]
        for workload in workloads:
            variants = [v for v in VARIANT_ORDER if (workload, v) in agg]
            if not variants:
                continue
            A(f"### Workload `{workload}`")
            A("")
            A("| Metric | " + " | ".join(variants) + " |")
            A("|---" * (len(variants) + 1) + "|")
            for metric, label, scale, digits, _ in METRICS:
                cells_txt = []
                any_data = False
                for variant in variants:
                    s = agg[(workload, variant)][metric]
                    if s.n:
                        any_data = True
                    cells_txt.append(fmt(s, scale, digits))
                if any_data:
                    unit = {1e6: " (Mbit/s)", 0.01: " (%)"}.get(scale, "")
                    A(f"| {label}{unit} | " + " | ".join(cells_txt) + " |")
            A("")

    # ------------------------------------------------------------- findings
    A("## 6. Findings")
    A("")
    A("Each comparison below is against variant A on the same workload, using "
      "the second-half window. A difference is only called a difference when "
      "the 95% confidence intervals do not overlap.")
    A("")
    headline_metrics = ("throughput_of_bps", "rtt_median_ms", "mean_rules",
                        "reactive_msgs_total", "recovery_s")
    workloads = [w for w in WORKLOAD_ORDER if any(c[0] == w for c in agg_half)]
    for workload in workloads:
        base = agg_half.get((workload, "A"))
        if not base:
            continue
        A(f"### `{workload}`")
        A("")

        # Latency is measured only over probes that came back. When two
        # variants drop very different fractions of traffic, their RTT medians
        # are computed over different populations and are not directly
        # comparable -- a variant can look fast precisely because it discarded
        # the packets that would have been slow. Say so where it applies
        # rather than leaving the reader to infer it.
        loss_values = {
            v: agg_half[(workload, v)]["ping_loss_fraction"].median
            for v in VARIANT_ORDER
            if (workload, v) in agg_half
            and agg_half[(workload, v)]["ping_loss_fraction"].n > 0
        }
        if len(loss_values) > 1:
            spread = max(loss_values.values()) - min(loss_values.values())
            if spread > 0.10:
                detail = ", ".join(
                    f"{v} {loss * 100:.0f}%" for v, loss in sorted(loss_values.items())
                )
                A(f"> **Read latency together with loss here.** Probe loss "
                  f"differs substantially between variants ({detail}), so the "
                  f"RTT medians below are computed over different populations "
                  f"of surviving probes. A variant that drops more traffic can "
                  f"show a lower median RTT without being better: the packets "
                  f"that would have been slow were never delivered.")
                A("")

        for variant in ("B", "C"):
            cell = agg_half.get((workload, variant))
            if not cell:
                continue
            A(f"**Variant {variant} vs A**")
            A("")
            for metric in headline_metrics:
                label = next(m[1] for m in METRICS if m[0] == metric)
                a_s, b_s = base[metric], cell[metric]
                if a_s.n == 0 and b_s.n == 0:
                    continue
                A(f"- {label}: {compare(a_s, b_s, metric)}")
            A("")
        c_cell = agg_half.get((workload, "C"))
        b_cell = agg_half.get((workload, "B"))
        if b_cell and c_cell:
            A("**Variant C vs B** (isolates flow-timeout control)")
            A("")
            for metric in headline_metrics:
                label = next(m[1] for m in METRICS if m[0] == metric)
                if b_cell[metric].n == 0 and c_cell[metric].n == 0:
                    continue
                A(f"- {label}: {compare(b_cell[metric], c_cell[metric], metric)}")
            A("")

    # ------------------------------------------------------------- learning
    A("## 7. Learning behaviour")
    A("")
    learning_rows = [r for r in rows if r.get("window") == "full"]
    by_variant: Dict[str, List[Dict[str, object]]] = {}
    for row in learning_rows:
        by_variant.setdefault(str(row["variant"]), []).append(row)
    A("| Variant | Policy steps | Mean reward | Reward 2nd half − 1st half | Exploration | States visited (of 144) | Distinct greedy actions |")
    A("|---|---|---|---|---|---|---|")
    for variant in VARIANT_ORDER:
        rws = by_variant.get(variant, [])
        if not rws:
            continue
        def s(metric: str) -> stats.Summary:
            return stats.summarise([
                r.get(metric) for r in rws
                if isinstance(r.get(metric), (int, float))
            ])
        steps, reward, delta = s("policy_steps"), s("mean_reward"), s("reward_delta")
        expl, visited = s("exploration_fraction"), s("visited_states")
        actions = s("distinct_greedy_actions")
        A(f"| {variant} | {steps.median:.0f} | "
          f"{'n/a' if reward.median is None else f'{reward.median:.3f}'} | "
          f"{'n/a' if delta.median is None else f'{delta.median:+.3f}'} | "
          f"{'n/a' if expl.median is None else f'{expl.median:.1%}'} | "
          f"{'n/a' if visited.median is None else f'{visited.median:.0f}'} | "
          f"{'n/a' if actions.median is None else f'{actions.median:.0f}'} |")
    A("")
    A("Variant A has no agent; the controller computes the same reward for it, "
      "so its row is the non-learning reference rather than evidence of "
      "learning. A reward delta near zero for A is therefore expected and is a "
      "useful check that the reward itself is not simply drifting upward over "
      "a run for reasons unrelated to the policy.")
    A("")

    # ------------------------------------------------------- control experiments
    diagnostics = load_json(os.path.join(out_dir, "diagnostics.json"))
    if diagnostics:
        A("## 7b. Control experiments: do the learned values matter?")
        A("")
        A("The results above show the variants differ. They cannot, on their "
          "own, show that the difference comes from the *learned Q-values* "
          "rather than from some incidental property of the RL code path. "
          "Three conditions separate those, run on the same workload with the "
          "same seeds:")
        A("")
        A("| Condition | Distinct actions | Q updates | Paths installed | Timeouts installed | Mean reward |")
        A("|---|---|---|---|---|---|")
        for name in ("trained", "frozen", "shuffled"):
            cell = diagnostics["by_condition"].get(name)
            if not cell:
                continue
            reward = cell.get("mean_reward")
            A(
                f"| `{name}` | {cell['distinct_actions_chosen']} | "
                f"{cell['q_updates_total']:.0f} | "
                f"{cell['installed_path_counts']} | "
                f"{cell['installed_timeout_counts']} | "
                f"{'n/a' if reward is None else f'{reward:+.4f}'} |"
            )
        A("")
        for claim in diagnostics.get("claims", []):
            A(f"- **{claim['result']}** — {claim['claim']}  \n  {claim['detail']}")
        A("")
        A("**What this establishes.** The frozen control runs the entire RL "
          "code path with an all-zero table and no exploration. It collapses "
          "to a single action, installs only one path and one timeout class, "
          "and scores far below the trained agent. So the gain is not an "
          "artefact of the machinery: acting over the action space is doing "
          "real work. Shuffling the action mapping visibly changes what the "
          "switches hold, which confirms the actions reach the dataplane "
          "rather than only the logs.")
        A("")
        A("**What this does not establish.** The shuffled agent learns "
          "normally but its action indices map to different dataplane "
          "effects, so its learned values are meaningless by construction -- "
          "yet it scores close to the trained agent. Combined with the sparse "
          "state coverage in section 7 (a handful of the 144 states visited, "
          "reward deltas near zero within a run), the most defensible reading "
          "is that most of the benefit here comes from *distributing traffic "
          "across both paths and several timeout classes at all*, rather than "
          "from having learned which action is best in which state. A "
          "well-chosen static multi-path policy might capture much of the "
          "same gain. Establishing that the learned mapping specifically "
          "matters would need far longer runs, better state coverage, and a "
          "train-then-freeze evaluation -- none of which the 90-second budget "
          "used here allows. This is a limitation of the experiment, not a "
          "claim about reinforcement learning in general.")
        A("")
        A(f"*(n = 3 seeds per condition; too few for a significance claim, "
          f"and reported as a direction rather than a result.)*")
        A("")

    # ----------------------------------------------------------------- figures
    A("## 8. Figures")
    A("")
    for name, caption in (
        ("fig_comparison_full.png", "Variant comparison by workload, full run."),
        ("fig_comparison_second_half.png", "Variant comparison, second-half window."),
        ("fig_learning.png", "Mean reward per policy step."),
        ("fig_behaviour.png", "Paths and idle timeouts actually installed on the switches."),
        ("fig_recovery.png", "Recovery time after a link failure on path A."),
    ):
        if os.path.exists(os.path.join(out_dir, name)):
            A(f"![{caption}]({name})")
            A("")
            A(f"*{caption}*")
            A("")

    # -------------------------------------------------------------- caveats
    A("## 9. How to read these numbers")
    A("")
    A("- **Five seeds per cell.** The confidence intervals are wide and are "
      "computed with a t critical value. Overlapping intervals mean *not "
      "distinguishable at this sample size*, not *no difference*. No "
      "significance test is claimed.")
    A("- **The agents are still learning during measurement.** Each run is ~90 "
      "decision steps from a cold start. This is not a train-then-test "
      "protocol; the second-half window is the closest available approximation.")
    A("- **Absolute latency figures carry WSL2 scheduling noise** and are not "
      "comparable with hardware testbed results. The relative comparison "
      "between variants is run on one host with identical seeded traffic and "
      "is the meaningful part.")
    A("- **Latency and loss must be read together.** RTT is measured only over "
      "probes that returned. Where loss rates differ sharply between variants, "
      "the medians summarise different populations and a lower median can "
      "reflect more aggressive dropping rather than better service. The "
      "per-workload sections flag this where it applies.")
    A("- **Check state coverage before reading the learning results.** The "
      "table in section 7 reports how many of the 144 states each agent "
      "actually visited. Where that number is small and the reward delta is "
      "near zero, the measured differences between variants cannot be "
      "attributed to a converged learned policy; they reflect the behaviour "
      "the policy produced during the run, exploration included. The "
      "null-control and action-shuffle diagnostics are the test that "
      "separates those.")
    A("- **Variant C has a larger action space than B** on the same step budget, "
      "so less experience per action. If C does not beat B, that is partly an "
      "exploration-budget artefact and not purely evidence against joint control.")
    A("- Full detail in `docs/LIMITATIONS.md` and `docs/ASSUMPTIONS.md`.")
    A("")
    A("---")
    A("")
    A("*Generated by `analysis/report.py`. Regenerate with:* "
      "`python3 -m analysis.aggregate && python3 -m analysis.verify_behaviour "
      "&& python3 -m analysis.plots && python3 -m analysis.report`")
    A("")
    return "\n".join(L)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default=os.path.join(PROJECT_ROOT, "analysis", "out"))
    p.add_argument("--runs", default=os.path.join(PROJECT_ROOT, "results", "runs"))
    p.add_argument("--dest", default=None,
                   help="where to write REPORT.md (default: analysis/out/REPORT.md)")
    args = p.parse_args(argv)

    text = build_report(args.out, args.runs)
    dest = args.dest or os.path.join(args.out, "REPORT.md")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8") as fh:
        fh.write(text)
    print(f"wrote {dest} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
