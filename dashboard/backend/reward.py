"""Reward explanation, derived from the actual implementation.

Nothing here reimplements or approximates the reward. The formula, weights and
normalisers are read from ``agent.reward`` (for defaults) and from each run's
``config.json`` (for what that run actually used), and the per-step component
values come from ``decisions.csv``, which the controller already writes.

**The reward function is not modified and the agent is unaffected.** This
module only reads.

Reconciliation
--------------
``decisions.csv`` records each reward *term* after normalisation but before
weighting (``r_throughput`` and friends), plus the final ``reward``. A term's
contribution is therefore ``term x weight x sign``, and the contributions must
sum to the total. We check that on every row and report the residual rather
than asserting it, because two things legitimately break exact equality:

* **Recorded precision.** Terms and the total are each written rounded to six
  decimals, so a weighted sum of rounded terms can differ from the rounded
  total by up to about 1.7e-6 (the weights sum to 2.4). Anything at that scale
  is rounding, not disagreement.
* **Clipping.** ``compute_reward`` clamps the total to [-1, 1]. When that
  bites, the contributions genuinely do not sum to the recorded value, and
  saying so is more useful than hiding it.
"""

from __future__ import annotations

import csv
import os
import sys
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from .config import PROJECT_ROOT, DataSource
from .runs import find_run
from .telemetry import _parse_q_values

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agent.reward import RewardNormalisers, RewardWeights  # noqa: E402

#: Residual above which we stop calling a difference "rounding". Derived from
#: the recorded precision: six decimals on each term, weights summing to 2.4,
#: plus the total's own rounding.
RECONCILE_TOLERANCE = 5e-6

#: The five terms, in the order the formula applies them. ``sign`` is +1 for
#: the term that rewards and -1 for the terms that penalise -- taken from the
#: implementation, not assumed.
TERMS: tuple = (
    {
        "key": "throughput",
        "csv_column": "r_throughput",
        "weight_key": "throughput",
        "sign": 1,
        "label": "Throughput",
        "measured": "Bits per second actually delivered to the destination hosts, "
                    "read from the edge switches' egress byte counters.",
        "normalisation": "Divided by a reference of both paths' combined capacity, "
                         "then clamped to 0-1.",
        "plain": "How much traffic the network actually delivered. This is the "
                 "only part of the score that can push it up.",
        "direction": "higher_is_better",
    },
    {
        "key": "utilisation",
        "csv_column": "r_utilisation",
        "weight_key": "utilisation",
        "sign": -1,
        "label": "Max link utilisation",
        "measured": "The busiest core link's utilisation during the interval, as a "
                    "fraction of that link's configured capacity.",
        "normalisation": "Already a fraction; clamped to 0-1.",
        "plain": "A stand-in for congestion. OpenFlow counters cannot measure "
                 "delay, so a link running near capacity is treated as a cost. "
                 "This is a proxy, not a latency measurement.",
        "direction": "lower_is_better",
    },
    {
        "key": "loss",
        "csv_column": "r_loss",
        "weight_key": "loss",
        "sign": -1,
        "label": "Packet loss (OVS counters)",
        "measured": "OpenFlow tx_dropped over offered packets on the core links.",
        "normalisation": "Already a fraction; clamped to 0-1.",
        "plain": "Intended to penalise discarded traffic. In practice this "
                 "term was inert: it was non-zero in 2 of 5,854 recorded "
                 "intervals across the whole matrix, with a maximum value of "
                 "0.000861.",
        "caveat": "Congestion loss in this emulation happens in the Linux "
                  "traffic-control queue that Mininet installs beneath Open "
                  "vSwitch, and OpenFlow's tx_dropped does not count it. "
                  "Measured directly: the bottleneck queue dropped 220 packets "
                  "while OpenFlow reported 0. End-to-end loss is measured by "
                  "the host ping stream instead. The term is left unchanged "
                  "because altering it would change the agent's objective and "
                  "invalidate the recorded experiments.",
        "direction": "lower_is_better",
    },
    {
        "key": "occupancy",
        "csv_column": "r_occupancy",
        "weight_key": "occupancy",
        "sign": -1,
        "label": "Flow-table occupancy",
        "measured": "Mean installed-rule count per switch against a soft cap.",
        "normalisation": "Divided by the soft cap per switch, then clamped to 0-1.",
        "plain": "How full the switches' flow tables are. Real switches have "
                 "limited rule space, so filling it has a cost. This is one of "
                 "the two terms that give variant C's timeout choices something "
                 "to gain.",
        "direction": "lower_is_better",
    },
    {
        "key": "controller_msgs",
        "csv_column": "r_controller_msgs",
        "weight_key": "controller_msgs",
        "sign": -1,
        "label": "Controller load",
        "measured": "Reactive controller messages per second (PacketIn + FlowMod). "
                    "Periodic statistics polling is deliberately excluded.",
        "normalisation": "Divided by a reference message rate, then clamped to 0-1.",
        "plain": "How hard the agent is making the controller work. Polling for "
                 "statistics is excluded because no policy choice can change it; "
                 "including it would drown out the part that can.",
        "direction": "lower_is_better",
    },
)


def _default_weights() -> Dict[str, float]:
    return asdict(RewardWeights())


def _default_normalisers() -> Dict[str, float]:
    n = RewardNormalisers()
    return {
        "throughput_ref_bps": n.throughput_ref_bps,
        "msg_rate_ref": n.msg_rate_ref,
        "occupancy_cap_rules": n.occupancy_cap_rules,
    }


def reward_model(run_id: Optional[str] = None) -> Dict[str, Any]:
    """The reward definition, with the weights a given run actually used."""
    weights = _default_weights()
    normalisers = _default_normalisers()
    source = DataSource.CONFIG.value
    run_note = "Default weights from agent/reward.py."

    if run_id:
        ref = find_run(run_id)
        if ref is not None:
            cfg_weights = ref.config.get("reward_weights")
            if isinstance(cfg_weights, dict) and cfg_weights:
                weights = {k: float(v) for k, v in cfg_weights.items()}
                source = DataSource.HISTORICAL.value
                run_note = f"Weights recorded in {run_id}/config.json."
            cfg_norm = ref.config.get("reward_normalisers")
            if isinstance(cfg_norm, dict) and cfg_norm:
                normalisers.update({k: float(v) for k, v in cfg_norm.items()})

    terms = []
    for term in TERMS:
        weight = float(weights.get(term["weight_key"], 0.0))
        terms.append({**term, "weight": weight,
                      "signed_weight": weight * term["sign"]})

    expression = " ".join(
        [f"{weights.get('throughput', 0):g} x throughput"]
        + [
            f"- {weights.get(t['weight_key'], 0):g} x {t['key']}"
            for t in TERMS if t["sign"] < 0
        ]
    )

    return {
        "source": source,
        "run_id": run_id,
        "note": run_note,
        "expression": expression,
        "clip_range": [-1.0, 1.0],
        "terms": terms,
        "weights": weights,
        "normalisers": normalisers,
        "plain_summary": (
            "Each interval, five quantities are measured from the switches and "
            "scaled to a 0-1 range. Delivered throughput adds to the score; "
            "congestion, packet loss, flow-table occupancy and controller load "
            "subtract from it. The weighted result is clamped to between -1 "
            "and +1."
        ),
        "scale_guide": [
            {"band": "positive", "meaning": "The interval delivered more value "
                                            "than it cost.", "range": "> +0.05"},
            {"band": "near_zero", "meaning": "Gains and costs roughly cancelled, "
                                             "or very little traffic moved.",
             "range": "-0.05 to +0.05"},
            {"band": "negative", "meaning": "Costs outweighed what was "
                                            "delivered.", "range": "< -0.05"},
        ],
        "caveats": [
            "The reward uses only OpenFlow counters. The agent never sees the "
            "ping or iperf3 measurements used to score the experiment, so a "
            "result in those metrics is an out-of-sample consequence of the "
            "policy rather than the thing it optimised.",
            "Max link utilisation is a proxy for congestion and queueing "
            "delay, because OpenFlow statistics cannot measure latency.",
            "These weights were chosen by hand and are not tuned. Different "
            "weights would produce a different policy.",
            "Variant A has no agent. The controller computes the same reward "
            "for it so it can be compared on equal terms, but nothing acts on "
            "it.",
        ],
    }


def _band(total: float) -> str:
    if total > 0.05:
        return "positive"
    if total < -0.05:
        return "negative"
    return "near_zero"


def _to_float(value: Optional[str]) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def explain_decisions(run_id: Optional[str], raw_dir: Optional[str],
                      limit: int = 120) -> Dict[str, Any]:
    """Per-step reward breakdown with a reconciliation check on every row."""
    model = reward_model(run_id)
    weights = model["weights"]

    if not raw_dir or not os.path.isdir(raw_dir):
        return {
            "available": False,
            "source": DataSource.UNAVAILABLE.value,
            "reason": "no run selected and no experiment running",
            "model": model,
            "steps": [],
        }

    path = os.path.join(raw_dir, "decisions.csv")
    if not os.path.exists(path):
        return {
            "available": False,
            "source": DataSource.UNAVAILABLE.value,
            "reason": "this run has not recorded any decisions yet",
            "model": model,
            "steps": [],
        }

    try:
        with open(path, "r", encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    except OSError as exc:
        return {"available": False, "source": DataSource.UNAVAILABLE.value,
                "reason": f"cannot read decisions: {exc}", "model": model,
                "steps": []}

    if limit and len(rows) > limit:
        rows = rows[-limit:]

    steps: List[Dict[str, Any]] = []
    worst_residual = 0.0
    reconciled = 0
    unreconciled = 0
    clipped_rows = 0

    for row in rows:
        total = _to_float(row.get("reward"))
        contributions = []
        raw_sum = 0.0
        complete = True

        for term in TERMS:
            value = _to_float(row.get(term["csv_column"]))
            weight = float(weights.get(term["weight_key"], 0.0))
            if value is None:
                complete = False
                contributions.append({
                    "key": term["key"], "label": term["label"],
                    "term_value": None, "weight": weight,
                    "sign": term["sign"], "contribution": None,
                })
                continue
            contribution = value * weight * term["sign"]
            raw_sum += contribution
            contributions.append({
                "key": term["key"], "label": term["label"],
                "term_value": value, "weight": weight,
                "sign": term["sign"], "contribution": contribution,
            })

        clipped = False
        residual = None
        if total is not None and complete:
            clipped_sum = max(-1.0, min(1.0, raw_sum))
            clipped = abs(clipped_sum - raw_sum) > 1e-12
            residual = abs(clipped_sum - total)
            worst_residual = max(worst_residual, residual)
            if residual <= RECONCILE_TOLERANCE:
                reconciled += 1
            else:
                unreconciled += 1
            if clipped:
                clipped_rows += 1

        steps.append({
            "t": _to_float(row.get("t")),
            "step": int(_to_float(row.get("step")) or 0),
            "variant": row.get("variant", ""),
            "state": row.get("state", ""),
            "action": row.get("action", ""),
            "path": row.get("path", ""),
            "idle_timeout": _to_float(row.get("idle_timeout")),
            "reward": total,
            "band": _band(total) if total is not None else None,
            "contributions": contributions,
            "sum_of_contributions": raw_sum if complete else None,
            "clipped": clipped,
            "residual": residual,
            "reconciles": (residual is not None
                           and residual <= RECONCILE_TOLERANCE),
            "epsilon": _to_float(row.get("epsilon")),
            "exploratory": (None if row.get("exploratory", "") == ""
                            else row.get("exploratory") == "1"),
            "greedy_action": row.get("greedy_action") or None,
            "q_before": _to_float(row.get("q_before")),
            "q_after": _to_float(row.get("q_after")),
            "td_error": _to_float(row.get("td_error")),
            "q_values": _parse_q_values(row.get("q_values_json")),
        })

    # Cross-check: the controller's own greedy_action must match the argmax
    # of the Q-values it logged for the *same* step, restricted to the
    # actions it marked as allowed. This is the same kind of evidence check
    # as the reward reconciliation above, applied to the "Choose Action"
    # stage of the journey -- if these ever disagreed, the journey would be
    # showing two different, contradictory accounts of why an action was
    # picked.
    q_checked = q_agree = q_disagree = 0
    for step in steps:
        q = step.get("q_values")
        if not q or not q.get("allowed") or not q.get("values"):
            continue
        values = q["values"]
        allowed = [a for a in q["allowed"] if a in values]
        if not allowed:
            continue
        argmax = max(allowed, key=lambda k: values[k])
        step["q_argmax"] = argmax
        greedy = step.get("greedy_action")
        if greedy:
            q_checked += 1
            if argmax == greedy:
                q_agree += 1
            else:
                q_disagree += 1
            step["q_argmax_matches_greedy"] = (argmax == greedy)

    checked = reconciled + unreconciled
    return {
        "available": True,
        "source": (DataSource.HISTORICAL.value if run_id
                   else DataSource.LIVE.value),
        "run_id": run_id,
        "model": model,
        "steps": steps,
        "reconciliation": {
            "checked": checked,
            "reconciled": reconciled,
            "unreconciled": unreconciled,
            "clipped_rows": clipped_rows,
            "worst_residual": worst_residual,
            "tolerance": RECONCILE_TOLERANCE,
            "ok": unreconciled == 0,
            "note": (
                "Each row's component contributions are summed and compared "
                "with the recorded reward. Differences up to "
                f"{RECONCILE_TOLERANCE:g} are the six-decimal rounding used "
                "when the values were written; anything larger would mean the "
                "breakdown does not describe the recorded score."
                + (f" {clipped_rows} row(s) hit the [-1, 1] clip, where the "
                   "components legitimately do not sum to the final value."
                   if clipped_rows else "")
            ),
        },
        "q_value_reconciliation": {
            "checked": q_checked,
            "agree": q_agree,
            "disagree": q_disagree,
            "ok": q_disagree == 0,
            "note": (
                "For every step with a logged Q-value row, the action with "
                "the highest Q-value among those the controller marked "
                "allowed is compared with the greedy_action the controller "
                "recorded independently. They should always agree."
                if q_checked else
                "No step in this run has a logged Q-value row -- either it "
                "predates the instrumentation, or this is variant A, which "
                "has no agent."
            ),
        },
    }


# --------------------------------------------------------------------------
# Metric catalogue
# --------------------------------------------------------------------------

#: Plain-English definitions for every metric the dashboard shows. ``kind``
#: distinguishes a reading taken at an instant from one aggregated over an
#: interval, because they are not interchangeable.
METRIC_CATALOG: tuple = (
    {
        "key": "throughput", "label": "Throughput", "unit": "Mbit/s",
        "direction": "higher_is_better", "kind": "interval_rate",
        "plain": "How much data the network actually delivered to the hosts.",
        "measured": "Edge-switch egress byte counters, cross-checked against "
                    "iperf3's own report after a run.",
    },
    {
        "key": "latency_median", "label": "Latency (median)", "unit": "ms",
        "direction": "lower_is_better", "kind": "interval_rate",
        "plain": "The typical round trip for a probe packet.",
        "measured": "ping from h1 to h3, measured by the host, not the controller.",
    },
    {
        "key": "latency_p95", "label": "Latency (p95)", "unit": "ms",
        "direction": "lower_is_better", "kind": "interval_rate",
        "plain": "The slow end of the range: 95% of probes were faster than this.",
        "measured": "Same probe stream as the median.",
    },
    {
        "key": "packet_loss", "label": "Packet loss (end-to-end)", "unit": "%",
        "direction": "lower_is_better", "kind": "interval_rate",
        "plain": "Share of probes that never received a reply at all.",
        "measured": "ping h1 -> h3, counting only probes with no reply at any "
                    "point. Probes answered late are counted as delayed, not "
                    "lost.",
        "caveat": "Read latency together with loss. A variant that drops more "
                  "traffic can show a lower median simply because the slow "
                  "packets were never delivered. Note also that 'high' depends "
                  "on the workload: the heavy workload deliberately offers more "
                  "traffic than the bottleneck path can carry, so some loss "
                  "there is the experiment working as designed.",
    },
    {
        "key": "probe_delayed", "label": "Probes delayed", "unit": "%",
        "direction": "lower_is_better", "kind": "interval_rate",
        "plain": "Share of probes answered only after the next probe was due "
                 "(later than 200 ms). These were delivered, just slowly.",
        "measured": "ping -O reports a probe as outstanding when its reply has "
                    "not arrived by the next send. Counting those as losses was "
                    "a measurement bug: it reported 96.3% loss on the heavy "
                    "workload where 3.3% of probes were actually lost.",
        "caveat": "This is a symptom of queueing, not of loss. It is reported "
                  "separately so the two are never conflated again.",
    },
    {
        "key": "ovs_drops", "label": "OVS-level drops", "unit": "%",
        "direction": "lower_is_better", "kind": "interval_rate",
        "plain": "Packets discarded by Open vSwitch itself.",
        "measured": "OpenFlow tx_dropped on core ports.",
        "caveat": "Near zero throughout this project, and NOT a measure of "
                  "congestion loss: Mininet enforces link bandwidth with a "
                  "traffic-control queue beneath Open vSwitch, and drops there "
                  "are invisible to this counter. Verified directly -- the "
                  "bottleneck queue dropped 220 packets while this counter "
                  "read 0. Use end-to-end packet loss instead.",
    },
    {
        "key": "max_link_util", "label": "Max link utilisation", "unit": "%",
        "direction": "context", "kind": "interval_rate",
        "plain": "How busy the busiest link is. High is not automatically bad "
                 "-- it can mean the network is being used well -- but near "
                 "100% usually means queueing and loss.",
        "measured": "Port byte counters divided by the link's configured capacity.",
    },
    {
        "key": "flow_occupancy", "label": "Flow-table occupancy", "unit": "rules",
        "direction": "lower_is_better", "kind": "instantaneous",
        "plain": "How many forwarding rules the switches are holding. Real "
                 "switches have limited space for these.",
        "measured": "Count of installed rules (priority 10+) from ovs-ofctl dumps. "
                    "Table-miss and IPv6 rules are controller plumbing and are "
                    "excluded.",
    },
    {
        "key": "rule_hit_ratio", "label": "Rule-hit ratio", "unit": "%",
        "direction": "higher_is_better", "kind": "interval_rate",
        "plain": "Share of packets handled by an installed rule instead of "
                 "being sent to the controller.",
        "measured": "Table lookups minus table-miss packets, over lookups.",
    },
    {
        "key": "table_misses", "label": "Table misses", "unit": "packets",
        "direction": "lower_is_better", "kind": "interval_count",
        "plain": "Packets that found no matching rule and had to be sent to "
                 "the controller.",
        "measured": "The priority-0 entry's own packet counter. In OpenFlow 1.3 "
                    "the table-miss entry is itself a rule, so lookups minus "
                    "matches is not usable for this.",
    },
    {
        "key": "controller_load", "label": "Controller load", "unit": "msg/s",
        "direction": "lower_is_better", "kind": "interval_rate",
        "plain": "How many messages the switches and controller exchange "
                 "because of forwarding decisions.",
        "measured": "PacketIn + FlowMod counted at the controller. Statistics "
                    "polling is excluded.",
    },
    {
        "key": "recovery_time", "label": "Link-failure recovery", "unit": "s",
        "direction": "lower_is_better", "kind": "event",
        "plain": "How long traffic stayed broken after a link went down.",
        "measured": "Time from the link-down event to the next successful ping "
                    "reply. Only exists for runs where a link actually failed.",
    },
    {
        "key": "reward", "label": "RL reward", "unit": "score",
        "direction": "higher_is_better", "kind": "interval_rate",
        "plain": "The agent's own score for the last interval, between -1 and +1.",
        "measured": "Computed by the controller from OpenFlow counters only.",
    },
)


def metric_catalog() -> Dict[str, Any]:
    return {
        "source": DataSource.CONFIG.value,
        "metrics": list(METRIC_CATALOG),
        "kinds": {
            "instantaneous": "A single reading at one moment.",
            "interval_rate": "Averaged over one polling interval.",
            "interval_count": "Counted up over one polling interval.",
            "event": "Measured when a specific event happens.",
        },
        "note": ("Values marked N/A are not measurable in the current context "
                 "rather than zero."),
    }
