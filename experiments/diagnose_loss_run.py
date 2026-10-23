"""Targeted diagnostic: where are packets actually dropped?

The OpenFlow port counters report almost no drops while the hosts clearly
experience congestion. One of these is wrong, or they are measuring different
things. This finds out by sampling the Linux traffic-control queues directly,
alongside the experiment, and comparing three independent views:

  1. ``tc -s qdisc`` on the switch-side veth interfaces -- the HTB/netem
     queues Mininet installs to enforce link bandwidth and delay. If the
     shaper is dropping, it is counted here.
  2. OpenFlow port counters (``tx_dropped``), collected by the controller.
  3. The host's own ping stream.

Mininet puts hosts in network namespaces but leaves switches in the root
namespace, so the switch-side interfaces are visible here without entering a
namespace.

Writes to its own directory under ``results/diagnostics/``. It never touches
the primary matrix or any existing run.

    sudo python3 -m experiments.diagnose_loss_run --variant A --duration 60
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from typing import Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from topo import spec  # noqa: E402

DIAG_ROOT = os.path.join(PROJECT_ROOT, "results", "diagnostics")

#: Switch-side interface names Mininet creates for the core links, e.g.
#: s1-eth3. These carry the HTB/netem queues that enforce bandwidth.
def core_interfaces() -> List[str]:
    names = []
    for link in spec.CORE_LINKS:
        names.append(f"{link.a}-eth{link.a_port}")
        names.append(f"{link.b}-eth{link.b_port}")
    return names


_QDISC_RE = re.compile(
    r"qdisc\s+(\S+)\s+(\S+).*?"
    r"Sent\s+(\d+)\s+bytes\s+(\d+)\s+pkt\s+"
    r"\(dropped\s+(\d+),\s+overlimits\s+(\d+)\s+requeues\s+(\d+)\)",
    re.DOTALL,
)


def sample_qdiscs(interfaces: List[str]) -> Dict[str, List[Dict[str, object]]]:
    """Parse `tc -s qdisc show dev X` for each interface."""
    out: Dict[str, List[Dict[str, object]]] = {}
    for iface in interfaces:
        try:
            proc = subprocess.run(
                ["tc", "-s", "qdisc", "show", "dev", iface],
                capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode != 0:
            continue
        entries = []
        for match in _QDISC_RE.finditer(proc.stdout):
            entries.append({
                "kind": match.group(1),
                "handle": match.group(2),
                "bytes": int(match.group(3)),
                "packets": int(match.group(4)),
                "dropped": int(match.group(5)),
                "overlimits": int(match.group(6)),
                "requeues": int(match.group(7)),
            })
        if entries:
            out[iface] = entries
    return out


class QdiscSampler(threading.Thread):
    def __init__(self, out_path: str, interval: float = 2.0):
        super().__init__(daemon=True)
        self.out_path = out_path
        self.interval = interval
        self._stop = threading.Event()
        self.samples = 0

    def run(self) -> None:
        interfaces = core_interfaces()
        with open(self.out_path, "a", encoding="utf-8") as fh:
            while not self._stop.is_set():
                payload = {
                    "t": time.time(),
                    "qdiscs": sample_qdiscs(interfaces),
                }
                fh.write(json.dumps(payload) + "\n")
                fh.flush()
                self.samples += 1
                self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()


def summarise(path: str) -> Dict[str, object]:
    """Total qdisc drops over the sampling window, per interface."""
    first: Dict[str, Dict[str, int]] = {}
    last: Dict[str, Dict[str, int]] = {}
    samples = 0
    try:
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            samples += 1
            for iface, entries in payload.get("qdiscs", {}).items():
                totals = {
                    "dropped": sum(int(e["dropped"]) for e in entries),
                    "packets": sum(int(e["packets"]) for e in entries),
                    "overlimits": sum(int(e["overlimits"]) for e in entries),
                }
                first.setdefault(iface, totals)
                last[iface] = totals
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        pass

    per_iface = {}
    total_dropped = total_packets = 0
    for iface, end in last.items():
        start = first.get(iface, {"dropped": 0, "packets": 0, "overlimits": 0})
        dropped = max(0, end["dropped"] - start["dropped"])
        packets = max(0, end["packets"] - start["packets"])
        per_iface[iface] = {
            "dropped": dropped,
            "packets": packets,
            "overlimits": max(0, end["overlimits"] - start["overlimits"]),
            "drop_fraction": (dropped / (dropped + packets)) if (dropped + packets) else 0.0,
        }
        total_dropped += dropped
        total_packets += packets

    return {
        "samples": samples,
        "per_interface": per_iface,
        "total_dropped": total_dropped,
        "total_packets": total_packets,
        "overall_drop_fraction": (
            total_dropped / (total_dropped + total_packets)
            if (total_dropped + total_packets) else 0.0
        ),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--variant", default="A", choices=["A", "B", "C"])
    parser.add_argument("--workload", default="heavy",
                        choices=["light", "heavy", "churn", "failure"])
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--seed", type=int, default=4242)
    parser.add_argument("--label", default="lossdiag")
    args = parser.parse_args(argv)

    if os.geteuid() != 0:
        print("ERROR: must run as root (Mininet requires it)", file=sys.stderr)
        return 2

    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = f"{stamp}-{args.label}-{args.variant}-{args.workload}"
    diag_dir = os.path.join(DIAG_ROOT, name)
    os.makedirs(diag_dir, exist_ok=True)
    qdisc_path = os.path.join(diag_dir, "qdisc_samples.jsonl")

    print(f"diagnostic directory: {diag_dir}")
    print("NOTE: this writes only under results/diagnostics/ and never touches "
          "the primary matrix.")

    tag = f"{args.label}-{args.variant}"
    argv_run = [
        sys.executable, "-m", "experiments.run_experiment",
        "--variant", args.variant, "--workload", args.workload,
        "--seed", str(args.seed), "--duration", str(args.duration),
        "--tag", tag,
    ]

    env = dict(os.environ)
    env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONWARNINGS"] = "ignore::SyntaxWarning"

    log_path = os.path.join(diag_dir, "runner.log")
    sampler = QdiscSampler(qdisc_path, interval=2.0)

    print(f"launching: {' '.join(argv_run)}")
    with open(log_path, "wb") as log_fh:
        proc = subprocess.Popen(
            argv_run, cwd=PROJECT_ROOT, env=env, stdout=log_fh,
            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, shell=False,
        )
        # Let Mininet build the topology before sampling, so the interfaces
        # exist and the first sample is a real baseline.
        time.sleep(12)
        sampler.start()
        proc.wait()
        sampler.stop()
        sampler.join(timeout=10)

    summary = summarise(qdisc_path)
    summary["returncode"] = proc.returncode
    summary["qdisc_samples"] = sampler.samples
    summary["run_tag"] = tag
    with open(os.path.join(diag_dir, "qdisc_summary.json"), "w",
              encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True)

    print()
    print("TRAFFIC-CONTROL QUEUE DROPS (the shaper Mininet installs)")
    print("=" * 72)
    for iface, stats in sorted(summary["per_interface"].items()):
        if stats["packets"] or stats["dropped"]:
            print(f"  {iface:12} sent={stats['packets']:>8}  "
                  f"dropped={stats['dropped']:>7}  "
                  f"({stats['drop_fraction']:.2%})  "
                  f"overlimits={stats['overlimits']}")
    print("-" * 72)
    print(f"  TOTAL        sent={summary['total_packets']:>8}  "
          f"dropped={summary['total_dropped']:>7}  "
          f"({summary['overall_drop_fraction']:.2%})")
    print()
    print(f"summary -> {os.path.join(diag_dir, 'qdisc_summary.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
