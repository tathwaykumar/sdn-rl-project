"""Topology model: the real configured network, plus live state when available.

The static structure comes straight from ``topo.spec`` -- the same module the
Mininet builder and the controller import. The dashboard therefore cannot show
a topology that differs from the one under test; there is no second definition
to drift.

Live state (link up/down, per-link utilisation, which path is carrying rules)
is overlaid from the active run's telemetry when there is one, and is clearly
absent rather than invented when there is not.
"""

from __future__ import annotations

import os
import sys
from typing import Dict, List, Optional, Tuple

from .config import PROJECT_ROOT, DataSource
from .telemetry import (
    _read_csv_tail, _to_float, active_paths, file_age_s, link_states_from_events,
)

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from topo import spec  # noqa: E402


def link_id(a: str, b: str) -> str:
    """Stable, direction-independent link identifier (matches events.csv)."""
    return "-".join(sorted([a, b]))


def static_topology() -> Dict[str, object]:
    """The configured topology, read from topo.spec."""
    nodes: List[Dict[str, object]] = []
    for name in spec.SWITCHES:
        nodes.append({
            "id": name,
            "kind": "switch",
            "label": name,
            "dpid": spec.DPID_BY_SWITCH[name],
            "is_edge": name in ("s1", "s4"),
        })
    for host in spec.HOSTS:
        nodes.append({
            "id": host.name,
            "kind": "host",
            "label": host.name,
            "ip": host.ip,
            "mac": host.mac,
            "attached_to": host.switch,
            "attached_port": host.port,
        })

    links: List[Dict[str, object]] = []
    for host in spec.HOSTS:
        links.append({
            "id": link_id(host.name, host.switch),
            "kind": "access",
            "source": host.name,
            "target": host.switch,
            "source_port": 0,
            "target_port": host.port,
            "bandwidth_mbit": spec.HOST_LINK_BW_MBIT,
            "delay_ms": spec.HOST_LINK_DELAY_MS,
            "paths": [],
        })
    for core in spec.CORE_LINKS:
        pair = frozenset({core.a, core.b})
        on_paths = [name for name, members in spec.PATH_LINKS.items()
                    if pair in members]
        links.append({
            "id": link_id(core.a, core.b),
            "kind": "core",
            "source": core.a,
            "target": core.b,
            "source_port": core.a_port,
            "target_port": core.b_port,
            "bandwidth_mbit": core.bw_mbit,
            "delay_ms": core.delay_ms,
            "queue_packets": spec.CORE_LINK_QUEUE_PKTS,
            "paths": on_paths,
        })

    paths: Dict[str, object] = {}
    for name, chain in spec.PATH_SWITCHES[("s1", "s4")].items():
        paths[name] = {
            "name": name,
            "switches": list(chain),
            "links": [link_id(chain[i], chain[i + 1]) for i in range(len(chain) - 1)],
            "hops": spec.path_hop_count(name),
            "bottleneck_mbit": spec.PATH_CAPACITY_BPS[name] / 1e6,
        }

    return {
        "source": DataSource.CONFIG.value,
        "nodes": nodes,
        "links": links,
        "paths": paths,
        "controller": {
            "ip": spec.CONTROLLER_IP,
            "port": spec.CONTROLLER_PORT,
            "openflow_version": "1.3",
        },
        "recovery_link": link_id(*spec.RECOVERY_LINK),
        "description": spec.describe(),
    }


# --------------------------------------------------------------------------
# Per-link utilisation from raw port counters
# --------------------------------------------------------------------------

def link_utilisation(raw_dir: str) -> Dict[str, object]:
    """Utilisation per link direction, from the two most recent port samples.

    OpenFlow port counters are cumulative since switch start, so a rate only
    exists between two samples. With fewer than two samples there is no rate
    to report -- and reporting zero would claim an idle network rather than an
    unmeasured one.
    """
    path = os.path.join(raw_dir, "raw_port_stats.csv")
    rows = _read_csv_tail(path, limit=400)
    if not rows:
        return {"available": False, "reason": "no port statistics recorded yet"}

    by_time: Dict[float, Dict[Tuple[str, int], float]] = {}
    for row in rows:
        t = _to_float(row.get("t"))
        tx = _to_float(row.get("tx_bytes"))
        port = _to_float(row.get("port"))
        switch = row.get("switch")
        if t is None or tx is None or port is None or not switch:
            continue
        by_time.setdefault(t, {})[(switch, int(port))] = tx

    times = sorted(by_time)
    if len(times) < 2:
        return {"available": False,
                "reason": "need two counter samples to compute a rate"}

    t_prev, t_now = times[-2], times[-1]
    dt = t_now - t_prev
    if dt <= 0:
        return {"available": False, "reason": "non-positive sampling interval"}

    prev, now = by_time[t_prev], by_time[t_now]
    per_link: Dict[str, Dict[str, object]] = {}

    for core in spec.CORE_LINKS:
        entry: Dict[str, object] = {
            "link_id": link_id(core.a, core.b),
            "capacity_mbit": core.bw_mbit,
            "directions": {},
            "utilisation": None,
        }
        best = 0.0
        measured = False
        for src, src_port, dst in ((core.a, core.a_port, core.b),
                                   (core.b, core.b_port, core.a)):
            key = (src, src_port)
            if key not in prev or key not in now:
                continue
            delta = now[key] - prev[key]
            if delta < 0:
                continue  # counter reset: no usable rate for this interval
            bps = delta * 8.0 / dt
            util = bps / (core.bw_mbit * 1e6) if core.bw_mbit else None
            entry["directions"][f"{src}->{dst}"] = {
                "bps": bps,
                "utilisation": util,
            }
            measured = True
            if util is not None:
                best = max(best, util)
        entry["utilisation"] = best if measured else None
        per_link[entry["link_id"]] = entry

    return {
        "available": True,
        "source": DataSource.LIVE.value,
        "t": t_now,
        "interval_s": dt,
        "age_s": file_age_s(path),
        "links": per_link,
    }


def host_traffic(raw_dir: str) -> Dict[str, object]:
    """Per-host sent/received bytes, packets and throughput.

    OpenFlow has no per-host counters; what exists is the switch's own
    counters on the access port facing that host. The direction is easy to
    get backwards, so it is spelled out here rather than left implicit:

    * the switch's ``tx`` counter on that port is traffic the switch sent
      *out to* the host -- i.e. what the host **received**;
    * the switch's ``rx`` counter is traffic the switch received *from* the
      host -- i.e. what the host **sent**.

    Cumulative totals (bytes/packets) come straight from the latest sample,
    since OpenFlow port counters are monotonic since switch start. Rates need
    two samples and the same delta-with-reset-guard pattern as
    ``link_utilisation``, so a single sample reports totals but no rate.
    """
    path = os.path.join(raw_dir, "raw_port_stats.csv")
    rows = _read_csv_tail(path, limit=400)
    if not rows:
        return {"available": False, "reason": "no port statistics recorded yet"}

    by_time: Dict[float, Dict[Tuple[str, int], Dict[str, float]]] = {}
    for row in rows:
        t = _to_float(row.get("t"))
        switch = row.get("switch")
        port = _to_float(row.get("port"))
        tx_b = _to_float(row.get("tx_bytes"))
        rx_b = _to_float(row.get("rx_bytes"))
        tx_p = _to_float(row.get("tx_packets"))
        rx_p = _to_float(row.get("rx_packets"))
        if None in (t, switch, port, tx_b, rx_b, tx_p, rx_p):
            continue
        by_time.setdefault(t, {})[(switch, int(port))] = {
            "tx_bytes": tx_b, "rx_bytes": rx_b, "tx_packets": tx_p, "rx_packets": rx_p,
        }

    times = sorted(by_time)
    if not times:
        return {"available": False, "reason": "no usable port-counter rows"}

    t_now = times[-1]
    now = by_time[t_now]
    prev = by_time[times[-2]] if len(times) >= 2 else None
    dt = (t_now - times[-2]) if prev else None

    hosts: Dict[str, Dict[str, object]] = {}
    for host in spec.HOSTS:
        key = (host.switch, host.port)
        cur = now.get(key)
        if cur is None:
            continue
        entry: Dict[str, object] = {
            "received_bytes": cur["tx_bytes"],   # switch tx -> host received
            "sent_bytes": cur["rx_bytes"],        # switch rx -> host sent
            "received_packets": cur["tx_packets"],
            "sent_packets": cur["rx_packets"],
            "receive_bps": None,
            "send_bps": None,
        }
        if prev is not None and dt and dt > 0:
            old = prev.get(key)
            if old is not None:
                d_tx = cur["tx_bytes"] - old["tx_bytes"]
                d_rx = cur["rx_bytes"] - old["rx_bytes"]
                if d_tx >= 0:
                    entry["receive_bps"] = d_tx * 8.0 / dt
                if d_rx >= 0:
                    entry["send_bps"] = d_rx * 8.0 / dt
        hosts[host.name] = entry

    return {
        "available": bool(hosts),
        "reason": None if hosts else "no access-port counters matched a known host",
        "source": DataSource.LIVE.value,
        "t": t_now,
        "interval_s": dt,
        "age_s": file_age_s(path),
        "hosts": hosts,
        "note": (
            "Counted at the switch's access port facing each host: 'received' "
            "is the switch's tx counter (switch -> host), 'sent' is the "
            "switch's rx counter (host -> switch)."
        ),
    }


# --------------------------------------------------------------------------
# Combined live view
# --------------------------------------------------------------------------

def topology_state(raw_dir: Optional[str], live: bool) -> Dict[str, object]:
    """Static topology plus whatever live state is genuinely available."""
    topo = static_topology()

    if not raw_dir or not os.path.isdir(raw_dir):
        topo["live"] = {
            "available": False,
            "reason": ("no experiment is running, so there is no link state, "
                       "utilisation or active path to show"),
            "source": DataSource.UNAVAILABLE.value,
        }
        return topo

    states = link_states_from_events(raw_dir)
    utilisation = link_utilisation(raw_dir)
    paths = active_paths(raw_dir)
    traffic = host_traffic(raw_dir)

    link_state: Dict[str, Dict[str, object]] = {}
    for link in topo["links"]:
        lid = str(link["id"])
        observed = states.get(lid)
        util_entry = (utilisation.get("links", {}) or {}).get(lid) \
            if utilisation.get("available") else None
        link_state[lid] = {
            # Only core links are reported on: the controller observes port
            # status on switch-to-switch links, and claiming knowledge of
            # access-link state would be an invention.
            "state": (observed["state"] if observed
                      else ("up" if link["kind"] == "core" else "unknown")),
            "state_observed": observed is not None,
            "state_changed_at": observed["t"] if observed else None,
            "utilisation": (util_entry or {}).get("utilisation"),
            "directions": (util_entry or {}).get("directions", {}),
        }

    topo["live"] = {
        "available": True,
        "source": DataSource.LIVE.value if live else DataSource.HISTORICAL.value,
        "link_state": link_state,
        "utilisation_available": utilisation.get("available", False),
        "utilisation_reason": utilisation.get("reason"),
        "utilisation_interval_s": utilisation.get("interval_s"),
        "active_paths": paths,
        "age_s": utilisation.get("age_s"),
        "host_traffic_available": traffic.get("available", False),
        "host_traffic_reason": traffic.get("reason"),
        "hosts": traffic.get("hosts", {}),
    }
    return topo
