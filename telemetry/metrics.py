"""Telemetry: turn raw OpenFlow counters into per-interval metrics, and write
both the raw counters and the derived metrics to disk.

Design rules this module exists to enforce:

1. **Raw first.** Every counter the controller reads is written out verbatim
   (``raw_port_stats.csv``, ``raw_flow_stats.csv``, ``raw_table_stats.csv``)
   before anything is derived from it.  If a derived metric is ever doubted it
   can be recomputed from the raw file, and the analysis scripts can be fixed
   without re-running experiments.
2. **Deltas, never absolutes.** OpenFlow counters are monotonic since switch
   start.  Rates come from differencing consecutive polls; the first poll of a
   run therefore produces no rate row, which is correct rather than zero.
3. **Counter resets are dropped, not clamped.** If a counter goes backwards
   (switch or port reset) the interval is marked invalid and excluded, instead
   of silently producing a huge negative or clamped-to-zero rate.
"""

from __future__ import annotations

import csv
import os
import time
from dataclasses import dataclass, field, asdict
from typing import Dict, Iterable, List, Optional, Tuple

from topo import spec


# --------------------------------------------------------------------------
# Raw counter containers
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class PortCounters:
    """One switch port's counters at one instant."""

    tx_packets: int
    rx_packets: int
    tx_bytes: int
    rx_bytes: int
    tx_dropped: int
    rx_dropped: int
    tx_errors: int
    rx_errors: int


@dataclass(frozen=True)
class TableCounters:
    """One flow table's counters at one instant."""

    active_count: int
    lookup_count: int
    matched_count: int


@dataclass
class ControllerCounters:
    """Messages the controller itself has sent or received.

    These are counted in the controller, not read from switches, because
    OpenFlow gives no way to ask "how much work did you cause me".  They are
    the honest measurement of controller load.
    """

    packet_in: int = 0
    flow_mod: int = 0
    packet_out: int = 0
    stats_request: int = 0
    stats_reply: int = 0
    flow_removed: int = 0
    port_status: int = 0

    def total(self) -> int:
        return (
            self.packet_in
            + self.flow_mod
            + self.packet_out
            + self.stats_request
            + self.stats_reply
            + self.flow_removed
            + self.port_status
        )

    def copy(self) -> "ControllerCounters":
        return ControllerCounters(**asdict(self))

    def as_dict(self) -> Dict[str, int]:
        d = asdict(self)
        d["total"] = self.total()
        return d


@dataclass
class RawSample:
    """Everything read from the switches in one polling cycle.

    Note on counting table misses: in OpenFlow 1.3 the table-miss entry is an
    ordinary priority-0 flow entry, so packets that "miss" still increment the
    table's ``matched_count``.  ``lookup_count - matched_count`` is therefore
    near zero and is *not* a usable miss count.  We instead read the priority-0
    entry's own packet counter, which is exactly the number of packets that
    found no installed rule.  ``tables`` is still recorded raw for completeness.
    """

    t: float
    ports: Dict[Tuple[str, int], PortCounters] = field(default_factory=dict)
    tables: Dict[str, TableCounters] = field(default_factory=dict)
    #: per switch: number of *installed* rules (priority > 0), excluding the
    #: table-miss entry, which is controller overhead rather than a flow.
    rules_installed: Dict[str, int] = field(default_factory=dict)
    #: per switch: packets matched by installed (priority > 0) rules.
    pkts_installed: Dict[str, int] = field(default_factory=dict)
    #: per switch: packets matched by the priority-0 table-miss entry.
    pkts_miss: Dict[str, int] = field(default_factory=dict)
    controller: ControllerCounters = field(default_factory=ControllerCounters)


# --------------------------------------------------------------------------
# Derived per-interval metrics
# --------------------------------------------------------------------------

@dataclass
class Snapshot:
    """Derived metrics for the interval ending at ``t``."""

    t: float
    dt: float
    valid: bool

    path_util: Dict[str, float] = field(default_factory=dict)
    max_link_util: float = 0.0
    delivered_bps: float = 0.0
    offered_bps: float = 0.0
    loss_fraction: float = 0.0

    total_rules: int = 0
    occupancy_fraction: float = 0.0
    rule_hit_packets: int = 0
    #: Cross-check from the installed rules' own counters; see derive().
    installed_rule_packets: int = 0
    table_lookups: int = 0
    table_matches: int = 0
    table_misses: int = 0
    miss_rate_per_s: float = 0.0
    rule_hit_ratio: Optional[float] = None

    controller_msg_rate: float = 0.0
    packet_in_rate: float = 0.0
    flow_mod_rate: float = 0.0

    note: str = ""

    def as_row(self) -> Dict[str, object]:
        row: Dict[str, object] = {
            "t": round(self.t, 4),
            "dt": round(self.dt, 4),
            "valid": int(self.valid),
            "max_link_util": round(self.max_link_util, 6),
            "delivered_bps": round(self.delivered_bps, 2),
            "offered_bps": round(self.offered_bps, 2),
            "loss_fraction": round(self.loss_fraction, 6),
            "total_rules": self.total_rules,
            "occupancy_fraction": round(self.occupancy_fraction, 6),
            "rule_hit_packets": self.rule_hit_packets,
            "installed_rule_packets": self.installed_rule_packets,
            "table_lookups": self.table_lookups,
            "table_matches": self.table_matches,
            "table_misses": self.table_misses,
            "miss_rate_per_s": round(self.miss_rate_per_s, 4),
            "rule_hit_ratio": (
                "" if self.rule_hit_ratio is None else round(self.rule_hit_ratio, 6)
            ),
            "controller_msg_rate": round(self.controller_msg_rate, 4),
            "packet_in_rate": round(self.packet_in_rate, 4),
            "flow_mod_rate": round(self.flow_mod_rate, 4),
            "note": self.note,
        }
        for name in spec.PATH_NAMES:
            row[f"util_{name}"] = round(self.path_util.get(name, 0.0), 6)
        return row


SNAPSHOT_FIELDS: Tuple[str, ...] = (
    "t", "dt", "valid",
    *(f"util_{n}" for n in spec.PATH_NAMES),
    "max_link_util", "delivered_bps", "offered_bps", "loss_fraction",
    "total_rules", "occupancy_fraction", "rule_hit_packets",
    "installed_rule_packets",
    "table_lookups", "table_matches", "table_misses", "miss_rate_per_s",
    "rule_hit_ratio", "controller_msg_rate", "packet_in_rate", "flow_mod_rate",
    "note",
)


#: Edge ports facing hosts. Bytes leaving these ports have actually been
#: delivered to a host, which is the only throughput figure the controller can
#: claim from switch counters alone.
HOST_FACING_EGRESS: Tuple[Tuple[str, int], ...] = tuple(
    (h.switch, h.port) for h in spec.HOSTS
)

#: Core (switch-to-switch) egress keys, used for utilisation and loss.
CORE_EGRESS: Tuple[Tuple[str, int], ...] = tuple(
    key for key in spec.EGRESS_CAPACITY_BPS if key not in HOST_FACING_EGRESS
)


def _delta(new: int, old: int) -> Optional[int]:
    """Counter difference, or None if the counter went backwards (reset)."""
    d = new - old
    return None if d < 0 else d


def derive(
    previous: RawSample,
    current: RawSample,
    occupancy_cap_rules: float,
) -> Snapshot:
    """Compute interval metrics between two raw samples."""
    dt = current.t - previous.t
    if dt <= 0:
        return Snapshot(t=current.t, dt=dt, valid=False, note="non-positive dt")

    snap = Snapshot(t=current.t, dt=dt, valid=True)
    # ``notes`` is informational and does not invalidate an interval;
    # ``invalidating`` does. Rule churn (entries expiring and being replaced)
    # is normal behaviour and must not throw away an otherwise good interval,
    # otherwise short-timeout variants would lose most of their data and look
    # artificially sparse.
    notes: List[str] = []
    invalidating: List[str] = []

    # ---- port-derived quantities ----------------------------------------
    egress_bps: Dict[Tuple[str, int], float] = {}
    delivered_bytes = 0
    offered_pkts = 0
    dropped_pkts = 0

    for key, cur in current.ports.items():
        prev = previous.ports.get(key)
        if prev is None:
            continue
        d_bytes = _delta(cur.tx_bytes, prev.tx_bytes)
        d_pkts = _delta(cur.tx_packets, prev.tx_packets)
        d_drop = _delta(cur.tx_dropped, prev.tx_dropped)
        if d_bytes is None or d_pkts is None or d_drop is None:
            invalidating.append(f"port counter reset on {key[0]}:{key[1]}")
            continue

        egress_bps[key] = (d_bytes * 8.0) / dt

        if key in HOST_FACING_EGRESS:
            delivered_bytes += d_bytes
        else:
            offered_pkts += d_pkts + d_drop
            dropped_pkts += d_drop

    snap.delivered_bps = (delivered_bytes * 8.0) / dt
    snap.offered_bps = sum(
        bps for key, bps in egress_bps.items() if key in CORE_EGRESS
    )

    # KNOWN BLIND SPOT -- read before using this figure.
    #
    # ``tx_dropped`` counts packets Open vSwitch itself discarded. It does NOT
    # count packets dropped by the Linux traffic-control queue underneath it,
    # and in this emulation that is where congestion loss happens: Mininet's
    # TCLink enforces every link's bandwidth with an HTB qdisc below OVS.
    #
    # Measured directly (experiments/diagnose_loss_run.py, heavy workload,
    # variant A): the HTB queue on the 5 Mbit bottleneck s1-eth3 dropped 220
    # packets and recorded 13,253 overlimits, while OpenFlow reported
    # tx_dropped = 0 on every port. The two counters disagree because they
    # are watching different places.
    #
    # Consequence: this value is near zero for the whole 60-run matrix and is
    # NOT a usable measure of packet loss here. End-to-end loss is measured by
    # the host's ping stream instead (see analysis/parsers.py). The field is
    # kept, unchanged, because the reward function consumes it and altering it
    # would silently change the agent's objective and invalidate the recorded
    # experiments -- but it should be read as "OVS-level drops", not as loss.
    snap.loss_fraction = (dropped_pkts / offered_pkts) if offered_pkts else 0.0

    # Per-path utilisation = worst link on that path (the bottleneck is what
    # limits the path, so a mean would understate congestion).
    for path_name, keys in spec.PATH_EGRESS_KEYS.items():
        utils = []
        for key in keys:
            cap = spec.EGRESS_CAPACITY_BPS.get(key)
            if cap and key in egress_bps:
                utils.append(egress_bps[key] / cap)
        snap.path_util[path_name] = max(utils) if utils else 0.0

    core_utils = [
        egress_bps[key] / spec.EGRESS_CAPACITY_BPS[key]
        for key in CORE_EGRESS
        if key in egress_bps and spec.EGRESS_CAPACITY_BPS.get(key)
    ]
    snap.max_link_util = max(core_utils) if core_utils else 0.0

    # ---- flow table occupancy -------------------------------------------
    if current.rules_installed:
        snap.total_rules = sum(current.rules_installed.values())
        per_switch = [
            min(1.0, n / occupancy_cap_rules)
            for n in current.rules_installed.values()
        ]
        snap.occupancy_fraction = sum(per_switch) / len(per_switch)

    # ---- table-level counters (monotonic since switch start) -------------
    lookups = matches = 0
    for sw, cur_tbl in current.tables.items():
        prev_tbl = previous.tables.get(sw)
        if prev_tbl is None:
            continue
        d_look = _delta(cur_tbl.lookup_count, prev_tbl.lookup_count)
        d_match = _delta(cur_tbl.matched_count, prev_tbl.matched_count)
        if d_look is None or d_match is None:
            invalidating.append(f"table counter reset on {sw}")
            continue
        lookups += d_look
        matches += d_match
    snap.table_lookups = lookups
    snap.table_matches = matches

    # ---- misses: the priority-0 entry's own packet counter ----------------
    # That entry is installed once with no timeout and is never replaced, so
    # its counter is monotonic for the whole run and safe to difference.
    miss_packets = 0
    for sw, cur_pkts in current.pkts_miss.items():
        prev_pkts = previous.pkts_miss.get(sw)
        if prev_pkts is None:
            continue
        d = _delta(cur_pkts, prev_pkts)
        if d is not None:
            miss_packets += d
    snap.table_misses = miss_packets
    snap.miss_rate_per_s = miss_packets / dt

    # ---- hits: derived from lookups, not from the installed-rule counters.
    # Installed rules expire and are replaced constantly under a churn
    # workload, so summing their packet counters is not monotonic and loses
    # whole intervals. ``lookup_count`` counts every packet entering the
    # table and survives rule churn, so hits = lookups - misses is both
    # robust and exact. (A negligible number of lookups are IPv6 frames
    # caught by the priority-1 discard rule; see controller/base_app.py.)
    snap.rule_hit_packets = max(0, lookups - miss_packets)
    snap.rule_hit_ratio = (snap.rule_hit_packets / lookups) if lookups else None

    # Best-effort cross-check from the installed rules themselves. Recorded,
    # not relied upon, precisely because of the churn problem above.
    installed_delta = 0
    for sw, cur_pkts in current.pkts_installed.items():
        prev_pkts = previous.pkts_installed.get(sw)
        if prev_pkts is None:
            continue
        d = _delta(cur_pkts, prev_pkts)
        if d is None:
            notes.append(f"installed-rule counter churned on {sw}")
            continue
        installed_delta += d
    snap.installed_rule_packets = installed_delta

    # ---- controller load -------------------------------------------------
    d_total = _delta(current.controller.total(), previous.controller.total())
    snap.controller_msg_rate = (d_total / dt) if d_total is not None else 0.0
    d_pin = _delta(current.controller.packet_in, previous.controller.packet_in)
    snap.packet_in_rate = (d_pin / dt) if d_pin is not None else 0.0
    d_fm = _delta(current.controller.flow_mod, previous.controller.flow_mod)
    snap.flow_mod_rate = (d_fm / dt) if d_fm is not None else 0.0

    all_notes = sorted(set(notes)) + sorted(set(invalidating))
    if all_notes:
        snap.note = "; ".join(all_notes)
    if invalidating:
        snap.valid = False
    return snap


# --------------------------------------------------------------------------
# CSV writing
# --------------------------------------------------------------------------

class CsvWriter:
    """Append-only CSV with a fixed header, flushed after every row.

    Flushing every row costs a little throughput and buys a lot: if a run is
    killed mid-experiment, everything up to the kill is already on disk and
    usable, instead of sitting in a buffer that dies with the process.
    """

    def __init__(self, path: str, fields: Iterable[str]):
        self.path = path
        self.fields = list(fields)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        new = not os.path.exists(path) or os.path.getsize(path) == 0
        self._fh = open(path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(
            self._fh, fieldnames=self.fields, extrasaction="ignore"
        )
        if new:
            self._writer.writeheader()
            self._fh.flush()

    def write(self, row: Dict[str, object]) -> None:
        self._writer.writerow(row)
        self._fh.flush()

    def close(self) -> None:
        try:
            self._fh.flush()
            self._fh.close()
        except Exception:
            pass


RAW_PORT_FIELDS = (
    "t", "switch", "port", "tx_packets", "rx_packets", "tx_bytes", "rx_bytes",
    "tx_dropped", "rx_dropped", "tx_errors", "rx_errors",
)
RAW_TABLE_FIELDS = ("t", "switch", "active_count", "lookup_count", "matched_count")
RAW_FLOW_FIELDS = ("t", "switch", "rules_installed", "pkts_installed", "pkts_miss")
CONTROLLER_FIELDS = (
    "t", "packet_in", "flow_mod", "packet_out", "stats_request", "stats_reply",
    "flow_removed", "port_status", "total",
)
DECISION_FIELDS = (
    "t", "step", "variant", "state", "action", "path", "idle_timeout",
    "exploratory", "greedy_action", "epsilon", "reward", "q_before", "q_after",
    "td_error", "usable_paths",
    "r_throughput", "r_utilisation", "r_loss", "r_occupancy", "r_controller_msgs",
)
EVENT_FIELDS = ("t", "kind", "detail")


class RunWriters:
    """All the CSV writers for one run directory."""

    def __init__(self, raw_dir: str):
        os.makedirs(raw_dir, exist_ok=True)
        self.raw_dir = raw_dir
        self.ports = CsvWriter(os.path.join(raw_dir, "raw_port_stats.csv"), RAW_PORT_FIELDS)
        self.tables = CsvWriter(os.path.join(raw_dir, "raw_table_stats.csv"), RAW_TABLE_FIELDS)
        self.flows = CsvWriter(os.path.join(raw_dir, "raw_flow_stats.csv"), RAW_FLOW_FIELDS)
        self.controller = CsvWriter(os.path.join(raw_dir, "controller_counters.csv"), CONTROLLER_FIELDS)
        self.snapshots = CsvWriter(os.path.join(raw_dir, "snapshots.csv"), SNAPSHOT_FIELDS)
        self.decisions = CsvWriter(os.path.join(raw_dir, "decisions.csv"), DECISION_FIELDS)
        self.events = CsvWriter(os.path.join(raw_dir, "events.csv"), EVENT_FIELDS)

    def write_raw(self, sample: RawSample) -> None:
        t = round(sample.t, 4)
        for (sw, port), c in sorted(sample.ports.items()):
            self.ports.write({
                "t": t, "switch": sw, "port": port,
                "tx_packets": c.tx_packets, "rx_packets": c.rx_packets,
                "tx_bytes": c.tx_bytes, "rx_bytes": c.rx_bytes,
                "tx_dropped": c.tx_dropped, "rx_dropped": c.rx_dropped,
                "tx_errors": c.tx_errors, "rx_errors": c.rx_errors,
            })
        for sw, tc in sorted(sample.tables.items()):
            self.tables.write({
                "t": t, "switch": sw, "active_count": tc.active_count,
                "lookup_count": tc.lookup_count, "matched_count": tc.matched_count,
            })
        for sw in sorted(sample.rules_installed):
            self.flows.write({
                "t": t, "switch": sw,
                "rules_installed": sample.rules_installed.get(sw, 0),
                "pkts_installed": sample.pkts_installed.get(sw, 0),
                "pkts_miss": sample.pkts_miss.get(sw, 0),
            })
        row = {"t": t}
        row.update(sample.controller.as_dict())
        self.controller.write(row)

    def event(self, kind: str, detail: str = "") -> None:
        self.events.write({"t": round(time.time(), 4), "kind": kind, "detail": detail})

    def close(self) -> None:
        for w in (self.ports, self.tables, self.flows, self.controller,
                  self.snapshots, self.decisions, self.events):
            w.close()
