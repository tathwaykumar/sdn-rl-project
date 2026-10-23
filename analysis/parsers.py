"""Parsers for the raw measurement files.

Everything here reads files produced by a run and returns structured data.  No
parser ever writes into a run directory, so the raw data stays exactly as the
measurement tools left it and a parsing bug can be fixed without re-running an
experiment.

Every parser reports failure explicitly rather than returning a plausible
default.  A missing or truncated measurement must surface as missing, because a
silent zero would be averaged into the results as if it were observed.
"""

from __future__ import annotations

import csv
import glob
import json
import os
import re
from dataclasses import dataclass, field
from typing import Dict, Iterator, List, Optional, Tuple


# --------------------------------------------------------------------------
# iperf3
# --------------------------------------------------------------------------

@dataclass
class IperfResult:
    path: str
    ok: bool
    bits_per_second: Optional[float] = None
    seconds: Optional[float] = None
    retransmits: Optional[int] = None
    bytes_received: Optional[int] = None
    error: str = ""


def parse_iperf3(path: str) -> IperfResult:
    """Parse one iperf3 --json file.

    iperf3 writes its report only when a test completes.  A killed or failed
    client leaves either an empty file or a short JSON containing an ``error``
    key; both are reported as not-ok so the analysis can exclude them by count
    rather than silently treating them as zero throughput.
    """
    if not os.path.exists(path):
        return IperfResult(path, False, error="missing")
    if os.path.getsize(path) == 0:
        return IperfResult(path, False, error="empty (client did not finish)")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        return IperfResult(path, False, error=f"malformed json: {exc}")

    if "error" in data:
        return IperfResult(path, False, error=str(data["error"]))

    try:
        end = data["end"]
        summary = end.get("sum_received") or end.get("sum_sent")
        if summary is None:
            return IperfResult(path, False, error="no sum_received/sum_sent")
        bps = float(summary["bits_per_second"])
        sent = end.get("sum_sent", {})
        return IperfResult(
            path=path,
            ok=True,
            bits_per_second=bps,
            seconds=float(summary.get("seconds", 0.0)),
            retransmits=sent.get("retransmits"),
            bytes_received=int(summary.get("bytes", 0)),
        )
    except (KeyError, TypeError, ValueError) as exc:
        return IperfResult(path, False, error=f"unexpected structure: {exc}")


def parse_all_iperf3(raw_dir: str) -> List[IperfResult]:
    return [parse_iperf3(p)
            for p in sorted(glob.glob(os.path.join(raw_dir, "iperf3_*.json")))]


# --------------------------------------------------------------------------
# ping
# --------------------------------------------------------------------------

_PING_REPLY = re.compile(r"^\[(\d+\.\d+)\].*?icmp_seq=(\d+).*?time=([\d.]+)\s*ms")
_PING_NOANSWER = re.compile(r"^\[(\d+\.\d+)\]\s*no answer yet for icmp_seq=(\d+)")


@dataclass
class PingResult:
    path: str
    ok: bool
    replies: List[Tuple[float, int, float]] = field(default_factory=list)
    losses: List[Tuple[float, int]] = field(default_factory=list)
    error: str = ""

    @property
    def rtts_ms(self) -> List[float]:
        return [r[2] for r in self.replies]

    @property
    def sent(self) -> int:
        """Probes accounted for: replies plus explicitly-reported losses."""
        seqs = {s for _, s, _ in self.replies} | {s for _, s in self.losses}
        return len(seqs)

    @property
    def loss_fraction(self) -> Optional[float]:
        total = self.sent
        if total == 0:
            return None
        return len(self.losses) / total


def parse_ping(path: str) -> PingResult:
    """Parse ``ping -D -O`` output into timestamped replies and losses."""
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return PingResult(path, False, error="missing or empty")
    replies: List[Tuple[float, int, float]] = []
    losses: List[Tuple[float, int]] = []
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = _PING_REPLY.match(line)
            if m:
                replies.append((float(m.group(1)), int(m.group(2)), float(m.group(3))))
                continue
            m = _PING_NOANSWER.match(line)
            if m:
                losses.append((float(m.group(1)), int(m.group(2))))
    if not replies and not losses:
        return PingResult(path, False, error="no parseable probe lines")
    return PingResult(path, True, replies=replies, losses=losses)


def recovery_window(
    ping: PingResult, failure_t: float, repair_t: Optional[float] = None
) -> Dict[str, Optional[float]]:
    """Measure the outage around a known link-failure timestamp.

    Returns observations, not inferences:

    ``first_reply_after_failure_s``  time from the failure to the next
                                     successful reply -- the recovery time, as
                                     actually observed by the hosts.
    ``lost_probes_in_outage``        probes with no answer between failure and
                                     the first subsequent reply.
    ``max_gap_s``                    largest interval between consecutive
                                     replies anywhere after the failure.

    ``None`` means not observable from this file, never zero.
    """
    out: Dict[str, Optional[float]] = {
        "first_reply_after_failure_s": None,
        "lost_probes_in_outage": None,
        "max_gap_s": None,
    }
    if not ping.ok:
        return out

    after = [t for t, _, _ in ping.replies if t >= failure_t]
    if after:
        out["first_reply_after_failure_s"] = after[0] - failure_t
        out["lost_probes_in_outage"] = float(
            sum(1 for t, _ in ping.losses if failure_t <= t <= after[0])
        )
        gaps = [b - a for a, b in zip(after, after[1:])]
        out["max_gap_s"] = max(gaps) if gaps else 0.0
    else:
        # Never recovered within the measurement: record the losses seen, and
        # leave recovery time unknown rather than inventing a number.
        out["lost_probes_in_outage"] = float(
            sum(1 for t, _ in ping.losses if t >= failure_t)
        )
    return out


# --------------------------------------------------------------------------
# Flow-table dumps (the behavioural ground truth)
# --------------------------------------------------------------------------

@dataclass
class FlowRule:
    switch: str
    priority: int
    cookie: str
    idle_timeout: Optional[int]
    hard_timeout: Optional[int]
    n_packets: int
    n_bytes: int
    output: Optional[str]
    nw_src: Optional[str]
    nw_dst: Optional[str]
    proto: Optional[str]
    #: Seconds since the rule was installed, as reported by the switch.
    #: Subtracting it from the dump timestamp gives the install time, which is
    #: what lets us check a rule against the decision that was in force when it
    #: was created rather than against whatever the policy is doing now.
    duration_s: Optional[float]
    raw: str

    def installed_at(self, dump_t: float) -> Optional[float]:
        if self.duration_s is None:
            return None
        return dump_t - self.duration_s


@dataclass
class FlowDump:
    path: str
    t: float
    rules: List[FlowRule]

    def routed(self, min_priority: int = 10) -> List[FlowRule]:
        return [r for r in self.rules if r.priority >= min_priority]


_INT_FIELDS = ("priority", "idle_timeout", "hard_timeout", "n_packets", "n_bytes")


def _parse_flow_line(switch: str, line: str) -> Optional[FlowRule]:
    if "cookie=" not in line:
        return None
    values: Dict[str, Optional[int]] = {}
    for key in _INT_FIELDS:
        m = re.search(rf"\b{key}=(\d+)", line)
        values[key] = int(m.group(1)) if m else None

    cookie_m = re.search(r"cookie=(0x[0-9a-fA-F]+)", line)
    out_m = re.search(r"actions=output:(\d+)", line)
    if out_m:
        output: Optional[str] = out_m.group(1)
    elif "actions=CONTROLLER" in line:
        output = "CONTROLLER"
    elif "actions=drop" in line:
        output = "drop"
    else:
        output = None

    src_m = re.search(r"nw_src=([\d.]+)", line)
    dst_m = re.search(r"nw_dst=([\d.]+)", line)
    proto = None
    for candidate in ("tcp", "udp", "icmp", "ipv6", "arp"):
        if re.search(rf"priority=\d+,{candidate}\b", line):
            proto = candidate
            break

    dur_m = re.search(r"\bduration=([\d.]+)s", line)

    return FlowRule(
        switch=switch,
        priority=values["priority"] if values["priority"] is not None else 0,
        cookie=cookie_m.group(1) if cookie_m else "0x0",
        duration_s=float(dur_m.group(1)) if dur_m else None,
        idle_timeout=values["idle_timeout"],
        hard_timeout=values["hard_timeout"],
        n_packets=values["n_packets"] or 0,
        n_bytes=values["n_bytes"] or 0,
        output=output,
        nw_src=src_m.group(1) if src_m else None,
        nw_dst=dst_m.group(1) if dst_m else None,
        proto=proto,
        raw=line.strip(),
    )


def parse_flow_dump(path: str) -> FlowDump:
    """Parse one ``ovs-ofctl dump-flows`` snapshot covering every switch."""
    rules: List[FlowRule] = []
    t = 0.0
    switch = "?"
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.rstrip("\n")
            if line.startswith("# t="):
                try:
                    t = float(line.split("=", 1)[1])
                except ValueError:
                    pass
                continue
            m = re.match(r"=====\s*(\S+)\s*=====", line)
            if m:
                switch = m.group(1)
                continue
            rule = _parse_flow_line(switch, line)
            if rule is not None:
                rules.append(rule)
    return FlowDump(path=path, t=t, rules=rules)


def parse_all_flow_dumps(raw_dir: str) -> List[FlowDump]:
    paths = sorted(glob.glob(os.path.join(raw_dir, "flow_dumps", "*.txt")))
    return [parse_flow_dump(p) for p in paths]


# --------------------------------------------------------------------------
# CSV telemetry
# --------------------------------------------------------------------------

def read_csv(path: str) -> List[Dict[str, str]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def to_float(value: str) -> Optional[float]:
    """Empty string means 'not measured' and must stay distinct from 0.0."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# Run directory
# --------------------------------------------------------------------------

@dataclass
class Run:
    path: str
    config: Dict[str, object]
    manifest: Dict[str, object]

    @property
    def variant(self) -> str:
        return str(self.config.get("variant", "?"))

    @property
    def workload(self) -> str:
        return str(self.config.get("workload", "?"))

    @property
    def seed(self) -> int:
        return int(self.config.get("seed", -1))

    @property
    def raw_dir(self) -> str:
        return os.path.join(self.path, "raw")

    @property
    def complete(self) -> bool:
        return bool(self.manifest.get("complete", False))


def load_run(path: str) -> Optional[Run]:
    cfg_path = os.path.join(path, "config.json")
    man_path = os.path.join(path, "manifest.json")
    if not os.path.exists(cfg_path):
        return None
    try:
        with open(cfg_path, encoding="utf-8") as fh:
            config = json.load(fh)
    except json.JSONDecodeError:
        return None
    manifest: Dict[str, object] = {}
    if os.path.exists(man_path):
        try:
            with open(man_path, encoding="utf-8") as fh:
                manifest = json.load(fh)
        except json.JSONDecodeError:
            manifest = {}
    return Run(path=path, config=config, manifest=manifest)


#: Marker the runner puts in the directory name of a control-experiment run
#: (``--tag diag-frozen`` and friends).
DIAGNOSTIC_TAG = "-diag-"


def is_diagnostic_run(run_id: str) -> bool:
    """Is this a control-experiment run rather than a normal measurement?

    Control runs deliberately cripple or scramble the agent (a frozen all-zero
    table, a permuted action mapping). They are complete, valid runs and they
    answer their own question, but they are *not* samples of the variant's
    normal behaviour. Mixing them into the A/B/C comparison silently corrupts
    it: when this was first run, three frozen runs pulled variant C's measured
    heavy-workload throughput from 13.68 down to 9.32 Mbit/s and inflated its
    sample count from 5 to 14.
    """
    return DIAGNOSTIC_TAG in run_id


def discover_runs(runs_root: str, include_diagnostics: bool = False) -> List[Run]:
    """Load every run directory, including incomplete ones.

    Incomplete runs are returned too, so the analysis can *report* how many
    were excluded instead of quietly analysing whatever happened to work.

    Control-experiment runs are excluded by default; pass
    ``include_diagnostics=True`` to get them (``analysis/diagnostics.py``
    does, because they are exactly what it analyses).
    """
    out: List[Run] = []
    for entry in sorted(os.listdir(runs_root)):
        full = os.path.join(runs_root, entry)
        if not os.path.isdir(full):
            continue
        if not include_diagnostics and is_diagnostic_run(entry):
            continue
        run = load_run(full)
        if run is not None:
            out.append(run)
    return out
