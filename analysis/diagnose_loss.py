"""Audit how packet loss is measured, and cross-validate it against other evidence.

Written to answer one question: is the reported packet loss a property of the
network, or of the measurement?

It compares, per workload and variant:

* what the current metric reports (``no answer yet`` lines / probes accounted for);
* how many of those probes *later received a reply* -- delivered, just slow;
* how many never replied at all, which is the only thing that is unambiguously
  a lost packet;
* what the OpenFlow port counters say about drops over the same runs;
* RTT relative to the probe interval, which is the thing that decides whether
  ``ping -O`` reports a probe as outstanding.

Read-only. It never writes to a run directory.

    python3 -m analysis.diagnose_loss [--workload heavy]
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import statistics
import sys
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from analysis.parsers import is_diagnostic_run, load_run  # noqa: E402

REPLY_RE = re.compile(r"^\[(\d+\.\d+)\].*?icmp_seq=(\d+).*?time=([\d.]+)\s*ms")
NOANS_RE = re.compile(r"^\[(\d+\.\d+)\]\s*no answer yet for icmp_seq=(\d+)")

#: The probe interval the workloads use. ping -O reports a probe as
#: outstanding when its reply has not arrived by the time the next probe is
#: sent, so this value is the threshold that decides what gets reported.
PROBE_INTERVAL_S = 0.2

VARIANTS = ("A", "B", "C")
WORKLOADS = ("light", "heavy", "churn", "failure")


class PingAudit:
    """Full accounting of one ping stream."""

    def __init__(self) -> None:
        self.replies: Dict[int, float] = {}
        self.outstanding: Dict[int, float] = {}

    def add_file(self, path: str) -> None:
        for line in open(path, encoding="utf-8", errors="replace"):
            m = REPLY_RE.match(line)
            if m:
                self.replies[int(m.group(2))] = float(m.group(3))
                continue
            m = NOANS_RE.match(line)
            if m:
                self.outstanding[int(m.group(2))] = float(m.group(1))

    @property
    def seqs(self) -> set:
        return set(self.replies) | set(self.outstanding)

    @property
    def slow_but_delivered(self) -> set:
        """Reported outstanding, then answered. Delivered, not lost."""
        return set(self.outstanding) & set(self.replies)

    @property
    def never_answered(self) -> set:
        """No reply ever seen. The only unambiguous loss."""
        return set(self.outstanding) - set(self.replies)

    @property
    def reported_loss(self) -> Optional[float]:
        """What the current metric computes."""
        total = len(self.seqs)
        return (len(self.outstanding) / total) if total else None

    @property
    def true_loss(self) -> Optional[float]:
        total = len(self.seqs)
        return (len(self.never_answered) / total) if total else None

    @property
    def rtts(self) -> List[float]:
        return list(self.replies.values())


def of_drop_totals(raw_dir: str) -> Tuple[int, int]:
    """(dropped, transmitted) summed over every port, from OpenFlow counters."""
    path = os.path.join(raw_dir, "raw_port_stats.csv")
    if not os.path.exists(path):
        return 0, 0
    last: Dict[Tuple[str, str], Tuple[int, int]] = {}
    try:
        with open(path, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    key = (row["switch"], row["port"])
                    last[key] = (int(row["tx_dropped"]), int(row["tx_packets"]))
                except (KeyError, ValueError):
                    continue
    except OSError:
        return 0, 0
    dropped = sum(d for d, _ in last.values())
    sent = sum(t for _, t in last.values())
    return dropped, sent


def collect(workload: Optional[str] = None) -> Dict[str, Dict[str, dict]]:
    runs_dir = os.path.join(PROJECT_ROOT, "results", "runs")
    out: Dict[str, Dict[str, dict]] = {}

    for entry in sorted(os.listdir(runs_dir)):
        path = os.path.join(runs_dir, entry)
        if not os.path.isdir(path) or is_diagnostic_run(entry):
            continue
        run = load_run(path)
        if run is None or not run.complete:
            continue
        # Only the primary matrix: the 90-second runs.
        if run.config.get("duration_s") != 90:
            continue
        if workload and run.workload != workload:
            continue

        cell = out.setdefault(run.workload, {}).setdefault(run.variant, {
            "runs": 0, "seqs": 0, "replied": 0, "outstanding": 0,
            "slow_but_delivered": 0, "never": 0, "rtts": [],
            "of_dropped": 0, "of_sent": 0, "per_run_reported": [],
            "per_run_true": [],
        })

        audit = PingAudit()
        ping_path = os.path.join(run.raw_dir, "ping_h1.txt")
        if os.path.exists(ping_path):
            audit.add_file(ping_path)

        if not audit.seqs:
            continue

        cell["runs"] += 1
        cell["seqs"] += len(audit.seqs)
        cell["replied"] += len(audit.replies)
        cell["outstanding"] += len(audit.outstanding)
        cell["slow_but_delivered"] += len(audit.slow_but_delivered)
        cell["never"] += len(audit.never_answered)
        cell["rtts"].extend(audit.rtts)
        if audit.reported_loss is not None:
            cell["per_run_reported"].append(audit.reported_loss)
        if audit.true_loss is not None:
            cell["per_run_true"].append(audit.true_loss)

        dropped, sent = of_drop_totals(run.raw_dir)
        cell["of_dropped"] += dropped
        cell["of_sent"] += sent

    return out


def _pct(x: Optional[float]) -> str:
    return "n/a" if x is None else f"{x * 100:6.1f}%"


def _median(values: List[float]) -> Optional[float]:
    return statistics.median(values) if values else None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workload", default=None, choices=WORKLOADS)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    data = collect(args.workload)
    if not data:
        print("no matching runs found")
        return 1

    if args.json:
        slim = {
            w: {
                v: {k: val for k, val in cell.items() if k != "rtts"}
                for v, cell in variants.items()
            }
            for w, variants in data.items()
        }
        print(json.dumps(slim, indent=2, sort_keys=True))
        return 0

    print()
    print("PACKET-LOSS MEASUREMENT AUDIT (primary 90s matrix only; "
          "diagnostic and off-duration runs excluded)")
    print("=" * 104)
    print(f"Probe: ICMP h1 -> h3, one every {PROBE_INTERVAL_S:g}s.")
    print("'ping -O' prints 'no answer yet' when a reply has not arrived by "
          "the time the NEXT probe is sent,")
    print(f"i.e. after {PROBE_INTERVAL_S:g}s -- not after the -W 1s timeout. A "
          "slow-but-delivered probe therefore")
    print("produces BOTH a 'no answer yet' line and, later, a reply line.")
    print()

    header = (f"{'workload':9} {'var':4} {'runs':>5} {'probes':>8} "
              f"{'reported':>9} {'slow+ok':>9} {'never':>7} {'TRUE loss':>10} "
              f"{'medRTT':>8} {'OF drops':>9}")
    print(header)
    print("-" * len(header))

    for workload in WORKLOADS:
        if workload not in data:
            continue
        for variant in VARIANTS:
            cell = data[workload].get(variant)
            if not cell or not cell["runs"]:
                continue
            reported = cell["outstanding"] / cell["seqs"] if cell["seqs"] else None
            true = cell["never"] / cell["seqs"] if cell["seqs"] else None
            med = _median(cell["rtts"])
            print(
                f"{workload:9} {variant:4} {cell['runs']:>5} {cell['seqs']:>8} "
                f"{_pct(reported):>9} {cell['slow_but_delivered']:>9} "
                f"{cell['never']:>7} {_pct(true):>10} "
                f"{(f'{med:.0f}ms' if med else 'n/a'):>8} "
                f"{cell['of_dropped']:>9}"
            )
        print()

    print("Columns")
    print("  reported  : what the current metric reports "
          "(all 'no answer yet' lines / probes)")
    print("  slow+ok   : probes reported outstanding that LATER received a "
          "reply -- delivered, counted as lost")
    print("  never     : probes that never received a reply -- unambiguously lost")
    print("  TRUE loss : never / probes")
    print("  OF drops  : tx_dropped summed across all switch ports, from "
          "OpenFlow counters")
    return 0


if __name__ == "__main__":
    sys.exit(main())
