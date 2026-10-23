"""Synthetic data for demo mode and frontend tests.

Every payload produced here carries ``source: "mock"``, and the API sets a
``X-SDNRL-Data-Source: mock`` header on top. The frontend shows a persistent
banner whenever mock mode is active.

This exists so the UI can be developed and tested without Mininet -- which
matters here because the real system needs root and exclusive access to the
OpenFlow port, and an experiment matrix may be running. It is deliberately
*not* a simulator: the numbers are obviously synthetic and are never mixed with
live data. A reader must never have to wonder whether a figure was measured.
"""

from __future__ import annotations

import math
import random
import time
from typing import Dict, List

from .config import DataSource
from .topology import static_topology

_RNG = random.Random(20261023)
_START = time.time()


def _phase() -> float:
    """Slow oscillation so the demo visibly changes without being random noise."""
    return (time.time() - _START) / 20.0


def mock_topology_state() -> Dict[str, object]:
    topo = static_topology()
    topo["source"] = DataSource.MOCK.value
    phase = _phase()
    use_b = math.sin(phase) > 0

    link_state: Dict[str, Dict[str, object]] = {}
    for link in topo["links"]:
        lid = str(link["id"])
        on_b = "B" in (link.get("paths") or [])
        on_a = "A" in (link.get("paths") or [])
        util = 0.0
        if link["kind"] == "core":
            if (use_b and on_b) or (not use_b and on_a):
                util = 0.55 + 0.25 * abs(math.sin(phase * 1.7))
            else:
                util = 0.05 * abs(math.cos(phase))
        link_state[lid] = {
            "state": "up",
            "state_observed": False,
            "state_changed_at": None,
            "utilisation": util,
            "directions": {},
        }

    topo["live"] = {
        "available": True,
        "source": DataSource.MOCK.value,
        "link_state": link_state,
        "utilisation_available": True,
        "utilisation_interval_s": 1.0,
        "active_paths": {
            "available": True,
            "source": DataSource.MOCK.value,
            "rule_counts": {"A": 2 if not use_b else 0, "B": 6 if use_b else 0},
            "packet_counts": {"A": 1200, "B": 4300},
            "share": {"A": 0.0 if use_b else 1.0, "B": 1.0 if use_b else 0.0},
            "dominant_path": "B" if use_b else "A",
            "total_edge_rules": 6 if use_b else 2,
            "note": "MOCK DATA - not a measurement.",
        },
        "age_s": 0.4,
    }
    return topo


def mock_flow_table(switch: str | None = None) -> Dict[str, object]:
    now = time.time()
    use_b = math.sin(_phase()) > 0
    out_port = 4 if use_b else 3
    rules: List[Dict[str, object]] = []

    for switch_name in ("s1", "s2", "s3", "s4", "s5"):
        if switch and switch_name != switch:
            continue
        rules.append({
            "switch": switch_name, "kind": "table_miss", "priority": 0,
            "cookie": "0x0", "table_id": 0, "idle_timeout": 0, "hard_timeout": 0,
            "duration_s": 120.0, "n_packets": 40, "n_bytes": 3200,
            "output_port": "CONTROLLER", "nw_src": None, "nw_dst": None,
            "proto": None, "installed_at": now - 120,
            "raw": "MOCK priority=0 actions=CONTROLLER:65535",
        })
        rules.append({
            "switch": switch_name, "kind": "infrastructure", "priority": 1,
            "cookie": "0x0", "table_id": 0, "idle_timeout": 0, "hard_timeout": 0,
            "duration_s": 120.0, "n_packets": 18, "n_bytes": 1500,
            "output_port": "drop", "nw_src": None, "nw_dst": None,
            "proto": "ipv6", "installed_at": now - 120,
            "raw": "MOCK priority=1,ipv6 actions=drop",
        })
        if switch_name in ("s1", "s4"):
            for i in range(3):
                rules.append({
                    "switch": switch_name, "kind": "routed", "priority": 10,
                    "cookie": "0x5d4e0001", "table_id": 0,
                    "idle_timeout": 15 if i else 5, "hard_timeout": 120,
                    "duration_s": 3.0 + i, "n_packets": 500 + 100 * i,
                    "n_bytes": 700000 + 1000 * i,
                    "output_port": str(out_port if switch_name == "s1" else 1),
                    "nw_src": "10.0.0.1", "nw_dst": "10.0.0.3", "proto": "tcp",
                    "installed_at": now - 3 - i,
                    "raw": "MOCK priority=10,tcp,... actions=output:%d" % out_port,
                })

    counts: Dict[str, Dict[str, int]] = {}
    for rule in rules:
        entry = counts.setdefault(str(rule["switch"]),
                                  {"total": 0, "routed": 0, "table_miss": 0,
                                   "infrastructure": 0})
        entry["total"] += 1
        entry[str(rule["kind"])] += 1

    return {
        "available": True,
        "source": DataSource.MOCK.value,
        "dump_t": now, "age_s": 1.0, "stale": False, "dump_interval_s": 5.0,
        "dump_file": "MOCK", "switch": switch, "rules": rules,
        "counts_by_switch": counts,
        "routed_timeout_distribution": {"5": 2, "15": 4},
        "note": "MOCK DATA - synthetic rules, not read from any switch.",
    }


def mock_decisions(limit: int = 40) -> List[Dict[str, object]]:
    now = time.time()
    out: List[Dict[str, object]] = []
    for i in range(limit):
        t = now - (limit - i)
        use_b = math.sin((t - _START) / 20.0) > 0
        path = "B" if use_b else "A"
        idle = _RNG.choice([5, 15, 60])
        out.append({
            "t": t, "step": i, "variant": "C",
            "state": f"{_RNG.randint(0,3)}-{_RNG.randint(0,3)}-{_RNG.randint(0,2)}-{_RNG.randint(0,2)}",
            "action": f"path={path},idle={idle}", "path": path,
            "idle_timeout": idle,
            "reward": round(_RNG.uniform(-0.2, 0.6), 4),
            "epsilon": round(max(0.05, 0.9 - i * 0.02), 4),
            "q_before": round(_RNG.uniform(-0.1, 0.4), 4),
            "q_after": round(_RNG.uniform(-0.1, 0.5), 4),
            "td_error": round(_RNG.uniform(-0.2, 0.2), 4),
            "exploratory": _RNG.random() < 0.2,
            "greedy_action": f"path={path},idle=15",
            "usable_paths": ["A", "B"],
            "reward_terms": {
                "throughput": round(_RNG.uniform(0.3, 0.9), 3),
                "utilisation": round(_RNG.uniform(0.2, 0.8), 3),
                "loss": round(_RNG.uniform(0.0, 0.1), 3),
                "occupancy": round(_RNG.uniform(0.05, 0.3), 3),
                "controller_msgs": round(_RNG.uniform(0.0, 0.4), 3),
            },
        })
    return out


def mock_metrics() -> Dict[str, object]:
    phase = _phase()
    return {
        "available": True,
        "source": DataSource.MOCK.value,
        "t": time.time(),
        "interval_s": 1.0,
        "age_s": 0.5,
        "values": {
            "delivered_bps": 6.0e6 + 2.0e6 * math.sin(phase),
            "max_link_util": 0.55 + 0.3 * abs(math.sin(phase * 1.3)),
            "loss_fraction": max(0.0, 0.02 * math.sin(phase * 2)),
            "total_rules": 40 + int(12 * abs(math.cos(phase))),
            "occupancy_fraction": 0.3 + 0.1 * abs(math.cos(phase)),
            "rule_hit_ratio": 0.98,
            "table_misses": 3,
            "miss_rate_per_s": 3.0,
            "packet_in_rate": 2.5,
            "flow_mod_rate": 7.5,
            "controller_msg_rate": 25.0,
            "util_A": 0.3, "util_B": 0.6,
        },
        "note": "MOCK DATA - not a measurement.",
    }


def mock_series(limit: int = 90) -> Dict[str, object]:
    now = time.time()
    points: List[Dict[str, object]] = []
    for i in range(limit):
        t = now - (limit - i)
        phase = (t - _START) / 20.0
        points.append({
            "t": t, "dt": 1.0, "valid": True, "note": "",
            "delivered_bps": 6.0e6 + 2.0e6 * math.sin(phase),
            "max_link_util": 0.55 + 0.3 * abs(math.sin(phase * 1.3)),
            "loss_fraction": max(0.0, 0.02 * math.sin(phase * 2)),
            "total_rules": 40 + int(12 * abs(math.cos(phase))),
            "occupancy_fraction": 0.3,
            "rule_hit_ratio": 0.98,
            "table_misses": 3, "miss_rate_per_s": 3.0,
            "packet_in_rate": 2.5, "flow_mod_rate": 7.5,
            "controller_msg_rate": 25.0,
            "util_A": 0.3 + 0.2 * math.sin(phase),
            "util_B": 0.6 - 0.2 * math.sin(phase),
        })
    return {"points": points, "count": len(points), "source": DataSource.MOCK.value,
            "age_s": 0.5, "interval_s": 1.0, "metric_meta": {}}
