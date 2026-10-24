"""Reading a run's telemetry: metrics, decisions, events, flow tables.

Works identically for a running experiment and a finished one -- the files have
the same shape either way, so "live" and "historical" differ only in the label
attached to the response and whether new rows keep arriving.

Flow-table parsing is delegated to ``analysis.parsers``, the same code the
research analysis uses. That matters for a specific reason: if the dashboard
had its own parser, the dashboard and the report could disagree about what a
switch was holding, and there would be no way to tell which was right.
"""

from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from typing import Dict, List, Optional, Tuple

from .config import (
    FLOW_DUMP_INTERVAL_S, PROJECT_ROOT, STALE_AFTER_S, DataSource,
)

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from analysis.parsers import FlowDump, parse_flow_dump  # noqa: E402
from topo import spec  # noqa: E402

#: Priority floor for rules the controller installed as routing decisions.
#: Below this are the table-miss (0) and IPv6 discard (1) entries, which are
#: controller plumbing and must never be counted as flows.
FLOW_PRIORITY = 10
TABLE_MISS_PRIORITY = 0

#: Egress port at each edge switch -> the path that port leads down. Derived
#: from topo.spec so it cannot drift from the real topology.
EDGE_PORT_TO_PATH: Dict[Tuple[str, int], str] = {
    ("s1", spec.NEIGHBOUR_PORT[("s1", "s2")]): "A",
    ("s1", spec.NEIGHBOUR_PORT[("s1", "s3")]): "B",
    ("s4", spec.NEIGHBOUR_PORT[("s4", "s2")]): "A",
    ("s4", spec.NEIGHBOUR_PORT[("s4", "s5")]): "B",
}


def _to_float(value: Optional[str]) -> Optional[float]:
    """Empty means 'not measured' and must never become 0.0."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _read_csv_tail(path: str, limit: Optional[int]) -> List[Dict[str, str]]:
    """Read a CSV, optionally keeping only the last ``limit`` rows.

    Tolerates a row being written as we read: the controller flushes per row,
    but a partially-written final line is still possible, and dropping it is
    correct rather than reporting a malformed measurement.
    """
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8", newline="", errors="replace") as fh:
            rows = list(csv.DictReader(fh))
    except (OSError, csv.Error):
        return []
    if rows and any(v is None for v in rows[-1].values()):
        rows = rows[:-1]
    if limit is not None and len(rows) > limit:
        rows = rows[-limit:]
    return rows


def file_age_s(path: str) -> Optional[float]:
    try:
        return max(0.0, time.time() - os.path.getmtime(path))
    except OSError:
        return None


# --------------------------------------------------------------------------
# Metrics (snapshots.csv)
# --------------------------------------------------------------------------

#: Metric key -> (label, unit, kind). ``kind`` tells the UI whether a number is
#: an instantaneous reading or an aggregate over the polling interval, which
#: are not interchangeable and are displayed differently.
METRIC_META: Dict[str, Tuple[str, str, str]] = {
    "delivered_bps": ("Throughput", "bit/s", "interval_rate"),
    "max_link_util": ("Max link utilisation", "fraction", "interval_rate"),
    "loss_fraction": ("Packet loss (OpenFlow counters)", "fraction", "interval_rate"),
    "total_rules": ("Flow-table occupancy", "rules", "instantaneous"),
    "occupancy_fraction": ("Occupancy vs soft cap", "fraction", "instantaneous"),
    "rule_hit_ratio": ("Rule-hit ratio", "fraction", "interval_rate"),
    "table_misses": ("Table misses", "packets", "interval_count"),
    "miss_rate_per_s": ("Table-miss rate", "packets/s", "interval_rate"),
    "packet_in_rate": ("Controller PacketIn rate", "msg/s", "interval_rate"),
    "flow_mod_rate": ("Controller FlowMod rate", "msg/s", "interval_rate"),
    "controller_msg_rate": ("Controller messages (all)", "msg/s", "interval_rate"),
}

SERIES_KEYS = tuple(METRIC_META.keys()) + ("util_A", "util_B")


def read_metric_series(raw_dir: str, limit: int = 180) -> Dict[str, object]:
    """Time series from snapshots.csv.

    Invalid intervals (counter resets) are passed through with ``valid: false``
    rather than dropped, so a gap in the data is visible in the UI instead of
    being quietly smoothed over.
    """
    path = os.path.join(raw_dir, "snapshots.csv")
    rows = _read_csv_tail(path, limit)
    points: List[Dict[str, object]] = []
    for row in rows:
        t = _to_float(row.get("t"))
        if t is None:
            continue
        point: Dict[str, object] = {
            "t": t,
            "dt": _to_float(row.get("dt")),
            "valid": row.get("valid") == "1",
            "note": row.get("note", ""),
        }
        for key in SERIES_KEYS:
            point[key] = _to_float(row.get(key))
        points.append(point)

    return {
        "points": points,
        "count": len(points),
        "metric_meta": {
            k: {"label": v[0], "unit": v[1], "kind": v[2]}
            for k, v in METRIC_META.items()
        },
        "age_s": file_age_s(path),
        "interval_s": points[-1]["dt"] if points else None,
    }


def latest_metrics(raw_dir: str) -> Dict[str, object]:
    """Most recent valid interval, for the metric cards."""
    series = read_metric_series(raw_dir, limit=30)
    points = [p for p in series["points"] if p.get("valid")]
    if not points:
        return {"available": False, "reason": "no valid telemetry intervals yet",
                "age_s": series["age_s"]}
    latest = points[-1]
    return {
        "available": True,
        "t": latest["t"],
        "interval_s": latest["dt"],
        "age_s": series["age_s"],
        "values": {k: latest.get(k) for k in SERIES_KEYS},
        "metric_meta": series["metric_meta"],
    }


# --------------------------------------------------------------------------
# Latency (ping_h1.txt, written line-buffered so it is readable live)
# --------------------------------------------------------------------------

_PING_REPLY = re.compile(r"^\[(\d+\.\d+)\].*?icmp_seq=(\d+).*?time=([\d.]+)\s*ms")
_PING_LOSS = re.compile(r"^\[(\d+\.\d+)\]\s*no answer yet for icmp_seq=(\d+)")


def read_latency(raw_dir: str, window_s: float = 30.0,
                 limit_lines: int = 4000) -> Dict[str, object]:
    """Recent RTTs and probe losses from the live ping stream.

    The probe runs with ``stdbuf -oL`` and ``-O``, so replies *and* losses are
    both on disk as they happen. Without ``-O`` a lost probe would leave no
    trace and an outage would be indistinguishable from the probe stopping.
    """
    path = os.path.join(raw_dir, "ping_h1.txt")
    if not os.path.exists(path):
        return {"available": False, "reason": "no latency probe in this run"}

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()[-limit_lines:]
    except OSError as exc:
        return {"available": False, "reason": f"cannot read probe output: {exc}"}

    # Track per-sequence so a probe reported outstanding and answered later is
    # recognised as delivered. ``ping -O`` prints "no answer yet" once a reply
    # has not arrived by the time the next probe is sent -- after the 0.2s
    # probe interval, not after the 1s timeout. Counting those lines as losses
    # conflated slow delivery with loss and reported 96.3% loss on a workload
    # that actually lost 3.3% of probes.
    replies: Dict[int, Tuple[float, float]] = {}
    outstanding: Dict[int, float] = {}
    for line in lines:
        m = _PING_REPLY.match(line)
        if m:
            replies[int(m.group(2))] = (float(m.group(1)), float(m.group(3)))
            continue
        m = _PING_LOSS.match(line)
        if m:
            outstanding[int(m.group(2))] = float(m.group(1))

    if not replies and not outstanding:
        return {"available": False, "reason": "probe has produced no results yet"}

    stamps = [t for t, _ in replies.values()] + list(outstanding.values())
    newest = max(stamps)
    cutoff = newest - window_s

    recent_replies = {s: v for s, v in replies.items() if v[0] >= cutoff}
    recent_outstanding = {s: t for s, t in outstanding.items() if t >= cutoff}

    lost_seqs = set(recent_outstanding) - set(recent_replies)
    delayed_seqs = set(recent_outstanding) & set(recent_replies)
    accounted = set(recent_replies) | set(recent_outstanding)

    rtts = sorted(rtt for _, rtt in recent_replies.values())
    total = len(accounted)

    def pct(q: float) -> Optional[float]:
        if not rtts:
            return None
        idx = min(len(rtts) - 1, max(0, int(round(q * (len(rtts) - 1)))))
        return rtts[idx]

    series = sorted(
        ((t, rtt) for t, rtt in recent_replies.values()), key=lambda x: x[0]
    )

    return {
        "available": True,
        "window_s": window_s,
        "probes_sent": total,
        "replies_received": len(recent_replies),
        "samples": len(recent_replies),
        "lost": len(lost_seqs),
        "losses": len(lost_seqs),
        "delayed": len(delayed_seqs),
        "loss_fraction": (len(lost_seqs) / total) if total else None,
        "delayed_fraction": (len(delayed_seqs) / total) if total else None,
        "median_ms": pct(0.5),
        "p95_ms": pct(0.95),
        "min_ms": rtts[0] if rtts else None,
        "max_ms": rtts[-1] if rtts else None,
        "series": [{"t": t, "rtt_ms": rtt} for t, rtt in series[-120:]],
        "age_s": file_age_s(path),
        "source_note": (
            "End-to-end ICMP h1 -> h3, measured by the host. 'Lost' counts "
            "probes with no reply at any point; probes answered after the next "
            "was due are counted as delayed, not lost."
        ),
    }


# --------------------------------------------------------------------------
# RL decisions (decisions.csv)
# --------------------------------------------------------------------------

def read_decisions(raw_dir: str, limit: int = 60) -> List[Dict[str, object]]:
    """Agent decision events, newest last.

    Only fields the agent actually records are returned. Variant A has no
    agent, so its rows carry no Q-values or epsilon; those stay null rather
    than being filled with plausible numbers.
    """
    rows = _read_csv_tail(os.path.join(raw_dir, "decisions.csv"), limit)
    out: List[Dict[str, object]] = []
    for row in rows:
        exploratory_raw = row.get("exploratory", "")
        out.append({
            "t": _to_float(row.get("t")),
            "step": int(_to_float(row.get("step")) or 0),
            "variant": row.get("variant", ""),
            "state": row.get("state", ""),
            "action": row.get("action", ""),
            "path": row.get("path", ""),
            "idle_timeout": _to_float(row.get("idle_timeout")),
            "reward": _to_float(row.get("reward")),
            "epsilon": _to_float(row.get("epsilon")),
            "q_before": _to_float(row.get("q_before")),
            "q_after": _to_float(row.get("q_after")),
            "td_error": _to_float(row.get("td_error")),
            # Empty means the variant does not track exploration (variant A).
            "exploratory": (None if exploratory_raw == ""
                            else exploratory_raw == "1"),
            "greedy_action": row.get("greedy_action") or None,
            "usable_paths": [p for p in (row.get("usable_paths") or "").split("|") if p],
            "reward_terms": {
                "throughput": _to_float(row.get("r_throughput")),
                "utilisation": _to_float(row.get("r_utilisation")),
                "loss": _to_float(row.get("r_loss")),
                "occupancy": _to_float(row.get("r_occupancy")),
                "controller_msgs": _to_float(row.get("r_controller_msgs")),
            },
            "q_values": _parse_q_values(row.get("q_values_json")),
        })
    return out


def _parse_q_values(raw: Optional[str]) -> Optional[Dict[str, object]]:
    """Decode the logged Q-value row, or None if it was never recorded.

    Missing is reported as missing. The whole 60-run primary matrix and the
    nine control runs were recorded before this instrumentation existed, and
    this function must not paper over that with an empty dict that looks
    measured -- a caller checking ``is None`` sees the real distinction
    between "not recorded" and "recorded as empty".
    """
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    values = payload.get("values")
    allowed = payload.get("allowed")
    if not isinstance(values, dict) or not isinstance(allowed, list):
        return None
    return {
        "values": {str(k): float(v) for k, v in values.items()},
        "allowed": [str(a) for a in allowed],
    }


def decode_state(state: str) -> Dict[str, object]:
    """Expand the encoded state into the bins it represents.

    The controller stores state as "a-b-c-d" bin indices. The UI needs the
    meaning, and this is the one place that mapping is written down for it.
    """
    parts = state.split("-")
    if len(parts) != 4:
        return {"raw": state, "decoded": False}
    try:
        util_a, util_b, occupancy, miss = (int(p) for p in parts)
    except ValueError:
        return {"raw": state, "decoded": False}

    util_labels = ["idle (<25%)", "light (25-50%)", "busy (50-75%)", "saturated (>75%)"]
    occ_labels = ["low (<33%)", "medium (33-66%)", "high (>66%)"]
    miss_labels = ["low (<5/s)", "medium (5-20/s)", "high (>20/s)"]

    def label(values: List[str], index: int) -> str:
        return values[index] if 0 <= index < len(values) else f"bin {index}"

    return {
        "raw": state,
        "decoded": True,
        "util_a": {"bin": util_a, "label": label(util_labels, util_a)},
        "util_b": {"bin": util_b, "label": label(util_labels, util_b)},
        "occupancy": {"bin": occupancy, "label": label(occ_labels, occupancy)},
        "miss_rate": {"bin": miss, "label": label(miss_labels, miss)},
    }


# --------------------------------------------------------------------------
# Events (events.csv)
# --------------------------------------------------------------------------

def read_events(raw_dir: str, limit: int = 80) -> List[Dict[str, object]]:
    rows = _read_csv_tail(os.path.join(raw_dir, "events.csv"), limit)
    return [
        {"t": _to_float(r.get("t")), "kind": r.get("kind", ""),
         "detail": r.get("detail", "")}
        for r in rows
    ]


def link_states_from_events(raw_dir: str) -> Dict[str, Dict[str, object]]:
    """Current up/down state of each core link, from observed events.

    Built by replaying ``link_state_change`` events in order. Links with no
    event are up: the controller only reports changes, and a link that never
    changed has never been down.
    """
    states: Dict[str, Dict[str, object]] = {}
    for event in read_events(raw_dir, limit=1000):
        if event["kind"] != "link_state_change":
            continue
        detail = event["detail"] or ""
        link_match = re.search(r"link=(\S+)", detail)
        state_match = re.search(r"state=(\w+)", detail)
        if not link_match or not state_match:
            continue
        states[link_match.group(1)] = {
            "state": state_match.group(1),
            "t": event["t"],
            "detail": detail,
        }
    return states


# --------------------------------------------------------------------------
# Flow tables (flow_dumps/, real ovs-ofctl output)
# --------------------------------------------------------------------------

def list_flow_dumps(raw_dir: str) -> List[str]:
    folder = os.path.join(raw_dir, "flow_dumps")
    if not os.path.isdir(folder):
        return []
    try:
        names = [n for n in os.listdir(folder) if n.endswith(".txt")]
    except OSError:
        return []
    return [os.path.join(folder, n) for n in sorted(names)]


def latest_flow_dump(raw_dir: str) -> Optional[Tuple[FlowDump, str]]:
    """Newest complete flow dump.

    The newest file can be mid-write while the dumper is running, so if it
    yields no rules the previous one is used instead. Showing an empty flow
    table would read as "the switches have no rules", which is a very different
    claim from "the snapshot is still being written".
    """
    paths = list_flow_dumps(raw_dir)
    for path in reversed(paths[-3:]):
        try:
            dump = parse_flow_dump(path)
        except OSError:
            continue
        if dump.rules:
            return dump, path
    return None


def classify_rule(rule) -> str:
    if rule.priority == TABLE_MISS_PRIORITY:
        return "table_miss"
    if rule.priority < FLOW_PRIORITY:
        return "infrastructure"
    return "routed"


def flow_table(raw_dir: str, switch: Optional[str] = None) -> Dict[str, object]:
    """Flow-table snapshot for one switch or all of them."""
    found = latest_flow_dump(raw_dir)
    if found is None:
        return {
            "available": False,
            "reason": "no flow-table dump has been written for this run yet",
            "source": DataSource.UNAVAILABLE.value,
        }
    dump, path = found
    age = file_age_s(path)

    rules = []
    for rule in dump.rules:
        if switch and rule.switch != switch:
            continue
        rules.append({
            "switch": rule.switch,
            "kind": classify_rule(rule),
            "priority": rule.priority,
            "cookie": rule.cookie,
            "table_id": 0,
            "idle_timeout": rule.idle_timeout,
            "hard_timeout": rule.hard_timeout,
            "duration_s": rule.duration_s,
            "n_packets": rule.n_packets,
            "n_bytes": rule.n_bytes,
            "output_port": rule.output,
            "nw_src": rule.nw_src,
            "nw_dst": rule.nw_dst,
            "proto": rule.proto,
            "installed_at": rule.installed_at(dump.t),
            "raw": rule.raw,
        })

    by_switch: Dict[str, Dict[str, int]] = {}
    for rule in dump.rules:
        entry = by_switch.setdefault(
            rule.switch, {"total": 0, "routed": 0, "table_miss": 0, "infrastructure": 0}
        )
        entry["total"] += 1
        entry[classify_rule(rule)] += 1

    timeouts: Dict[str, int] = {}
    for rule in dump.rules:
        if classify_rule(rule) == "routed" and rule.idle_timeout is not None:
            key = str(rule.idle_timeout)
            timeouts[key] = timeouts.get(key, 0) + 1

    return {
        "available": True,
        "source": DataSource.LIVE.value,
        "dump_t": dump.t,
        "age_s": age,
        "stale": (age is not None and age > STALE_AFTER_S),
        "dump_interval_s": FLOW_DUMP_INTERVAL_S,
        "dump_file": os.path.basename(path),
        "switch": switch,
        "rules": rules,
        "counts_by_switch": by_switch,
        "routed_timeout_distribution": timeouts,
        "note": (
            "Captured by the experiment runner with 'ovs-ofctl dump-flows'. "
            f"Refreshed every {FLOW_DUMP_INTERVAL_S:.0f}s; the dashboard adds "
            "no OpenFlow traffic of its own."
        ),
    }


def occupancy_history(raw_dir: str, limit: int = 60) -> List[Dict[str, object]]:
    """Per-switch routed-rule counts across successive dumps."""
    out: List[Dict[str, object]] = []
    for path in list_flow_dumps(raw_dir)[-limit:]:
        try:
            dump = parse_flow_dump(path)
        except OSError:
            continue
        if not dump.rules:
            continue
        counts: Dict[str, int] = {}
        for rule in dump.rules:
            if classify_rule(rule) == "routed":
                counts[rule.switch] = counts.get(rule.switch, 0) + 1
        out.append({"t": dump.t, "per_switch": counts,
                    "total": sum(counts.values())})
    return out


# --------------------------------------------------------------------------
# Active path: determined from installed rules, never from agent intent
# --------------------------------------------------------------------------

def active_paths(raw_dir: str) -> Dict[str, object]:
    """Which path the switches are actually forwarding on.

    This reads egress ports of installed rules at the edge switches. It is
    deliberately *not* derived from the agent's latest decision: the agent
    selecting path B does not make path B active, and showing a route as
    changed before the rules exist would misrepresent the network. The
    decision and the installed reality are reported separately and compared.
    """
    found = latest_flow_dump(raw_dir)
    if found is None:
        return {"available": False,
                "reason": "no flow dump yet; cannot confirm installed routes",
                "source": DataSource.UNAVAILABLE.value}
    dump, path = found

    counts: Dict[str, int] = {"A": 0, "B": 0}
    packets: Dict[str, int] = {"A": 0, "B": 0}
    for rule in dump.rules:
        if classify_rule(rule) != "routed" or rule.output is None:
            continue
        try:
            port = int(rule.output)
        except (TypeError, ValueError):
            continue
        name = EDGE_PORT_TO_PATH.get((rule.switch, port))
        if name:
            counts[name] += 1
            packets[name] += rule.n_packets

    total = counts["A"] + counts["B"]
    dominant = None
    if total:
        dominant = "A" if counts["A"] >= counts["B"] else "B"

    return {
        "available": True,
        "source": DataSource.LIVE.value,
        "dump_t": dump.t,
        "age_s": file_age_s(path),
        "rule_counts": counts,
        "packet_counts": packets,
        "share": {k: (v / total if total else 0.0) for k, v in counts.items()},
        "dominant_path": dominant,
        "total_edge_rules": total,
        "note": (
            "Determined from egress ports of installed rules at the edge "
            "switches s1/s4 -- i.e. what the switches are doing, not what the "
            "agent asked for."
        ),
    }


def decision_vs_installed(raw_dir: str) -> Dict[str, object]:
    """Compare the agent's latest decision with what the switches hold.

    This is the honesty check the UI shows beside every decision: a decision is
    reported as *confirmed* only when rules on the chosen path actually exist.
    """
    decisions = read_decisions(raw_dir, limit=1)
    installed = active_paths(raw_dir)
    if not decisions:
        return {"available": False, "reason": "no decisions recorded yet"}
    latest = decisions[-1]

    if not installed.get("available"):
        return {
            "available": True,
            "decision": latest,
            "installed": None,
            "status": "unverified",
            "detail": "No flow dump available yet to confirm installation.",
        }

    chosen = latest.get("path")
    counts = installed.get("rule_counts", {})
    present = counts.get(chosen, 0) if chosen else 0
    dump_t = installed.get("dump_t") or 0
    decision_t = latest.get("t") or 0

    # Order matters. The staleness check has to come first: rules for the
    # chosen path may already exist from an *earlier* decision, and treating
    # those as confirmation would report a brand-new decision as installed
    # before any switch had acted on it. Only a dump taken after the decision
    # can confirm that decision.
    if dump_t < decision_t:
        status = "awaiting_confirmation"
        detail = ("The newest flow dump predates this decision; the next dump "
                  f"(up to {FLOW_DUMP_INTERVAL_S:.0f}s) will confirm it."
                  + (f" ({present} pre-existing rule(s) on path {chosen}.)"
                     if present else ""))
    elif present > 0:
        status = "confirmed"
        detail = (f"{present} rule(s) on path {chosen} are installed at the "
                  "edge switches, observed after the decision was made.")
    else:
        status = "not_observed"
        detail = (f"No rules on path {chosen} were found in a flow dump taken "
                  "after this decision.")

    return {
        "available": True,
        "decision": latest,
        "installed": installed,
        "status": status,
        "detail": detail,
        "lag_s": (dump_t - decision_t) if (dump_t and decision_t) else None,
    }


def _path_rule_counts(dump: "object") -> Dict[str, int]:
    """Routed rules per path in one flow dump, by egress port at the edges."""
    counts: Dict[str, int] = {"A": 0, "B": 0}
    for rule in dump.rules:  # type: ignore[attr-defined]
        if classify_rule(rule) != "routed" or rule.output is None:
            continue
        try:
            port = int(rule.output)
        except (TypeError, ValueError):
            continue
        name = EDGE_PORT_TO_PATH.get((rule.switch, port))
        if name:
            counts[name] += 1
    return counts


def verify_decision_at(raw_dir: str, step: int) -> Dict[str, object]:
    """Switch confirmation for one specific, arbitrary decision step.

    ``decision_vs_installed`` above answers this only for the *latest*
    decision in a live run. The Decision Journey lets a viewer step through
    *any* recorded decision -- including ones from a finished, historical run
    -- and each one needs the same honest treatment: confirmed only by a flow
    dump taken strictly after it, never by a dump that merely happens to show
    rules on the right path because an earlier decision put them there.
    """
    decisions = read_decisions(raw_dir, limit=100000)
    match = next((d for d in decisions if d.get("step") == step), None)
    if match is None:
        return {"available": False, "reason": f"no decision recorded at step {step}"}

    decision_t = match.get("t")
    chosen = match.get("path")
    if decision_t is None or not chosen:
        return {"available": False, "reason": "decision has no timestamp or path"}

    dumps: List[Tuple[float, str]] = []
    for dump_path in list_flow_dumps(raw_dir):
        try:
            dump = parse_flow_dump(dump_path)
        except OSError:
            continue
        if not dump.rules:
            continue
        dumps.append((dump.t, dump_path))

    # The first dump at or after the decision's timestamp is the one that can
    # confirm it. Dumps are usually already time-ordered by filename, but sort
    # explicitly rather than assume it.
    dumps.sort(key=lambda pair: pair[0])
    confirming = next((p for t, p in dumps if t >= decision_t), None)

    if confirming is None:
        return {
            "available": True,
            "decision": match,
            "status": "no_later_dump",
            "detail": (
                "No flow dump was taken after this decision before the run "
                "ended, so it cannot be confirmed from switch evidence."
            ),
        }

    dump = parse_flow_dump(confirming)
    counts = _path_rule_counts(dump)
    present = counts.get(chosen, 0)
    status = "confirmed" if present > 0 else "not_observed"
    detail = (
        f"{present} rule(s) on path {chosen} found in the flow dump taken at "
        f"{dump.t:.1f}s (this decision was made at {decision_t:.1f}s)."
        if present else
        f"No rules on path {chosen} were found in the flow dump taken at "
        f"{dump.t:.1f}s, the first one after this decision."
    )
    return {
        "available": True,
        "decision": match,
        "status": status,
        "detail": detail,
        "dump_t": dump.t,
        "lag_s": dump.t - decision_t,
        "rule_counts": counts,
    }
