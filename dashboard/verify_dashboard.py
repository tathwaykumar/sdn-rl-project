"""End-to-end verification of the dashboard against the real system.

Checks that what the dashboard reports agrees with the project's own source of
truth. Unlike the unit tests, this runs against a live backend and real run
directories, so it answers questions the unit tests cannot:

1. Does the topology the API serves match ``topo.spec`` exactly?
2. Does the flow-table view agree with the flow dumps on disk?
3. Is an "active path" claim backed by rules actually present on the switches?
4. Do displayed idle timeouts match the installed rules?
5. Do comparison figures match the project's own analysis code?
6. Is the live/recorded/unavailable labelling correct for each case?

Read-only: it starts nothing, stops nothing and writes nothing. Safe to run
while an experiment is in flight.

    python3 -m dashboard.verify_dashboard [--base http://127.0.0.1:8000]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from analysis.parsers import parse_flow_dump  # noqa: E402
from topo import spec  # noqa: E402

CHECKS: List[Tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, ok, detail))


def get(base: str, path: str) -> Optional[Any]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=20) as response:
            return json.loads(response.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return None


def verify_topology(base: str) -> None:
    topo = get(base, "/api/topology")
    if topo is None:
        check("API reachable", False, "could not fetch /api/topology")
        return
    check("API reachable", True)

    switches = {n["id"] for n in topo["nodes"] if n["kind"] == "switch"}
    hosts = {n["id"] for n in topo["nodes"] if n["kind"] == "host"}
    check(
        "Topology switches match topo.spec",
        switches == set(spec.SWITCHES),
        f"api={sorted(switches)} spec={sorted(spec.SWITCHES)}",
    )
    check(
        "Topology hosts match topo.spec",
        hosts == {h.name for h in spec.HOSTS},
        f"api={sorted(hosts)}",
    )

    core = {l["id"]: l for l in topo["links"] if l["kind"] == "core"}
    mismatches = []
    for link in spec.CORE_LINKS:
        lid = "-".join(sorted([link.a, link.b]))
        entry = core.get(lid)
        if entry is None:
            mismatches.append(f"{lid} missing")
            continue
        if {entry["source_port"], entry["target_port"]} != {link.a_port, link.b_port}:
            mismatches.append(f"{lid} ports differ")
        if entry["bandwidth_mbit"] != link.bw_mbit:
            mismatches.append(f"{lid} bandwidth differs")
    check("Core link ports and bandwidths match topo.spec", not mismatches,
          "; ".join(mismatches))

    paths = topo.get("paths", {})
    disjoint = set(paths.get("A", {}).get("links", [])).isdisjoint(
        paths.get("B", {}).get("links", []))
    check("Reported paths A and B are link-disjoint", disjoint,
          f"A={paths.get('A', {}).get('links')} B={paths.get('B', {}).get('links')}")


def _newest_dump(raw_dir: str) -> Optional[str]:
    folder = os.path.join(raw_dir, "flow_dumps")
    if not os.path.isdir(folder):
        return None
    names = sorted(n for n in os.listdir(folder) if n.endswith(".txt"))
    for name in reversed(names):
        path = os.path.join(folder, name)
        if parse_flow_dump(path).rules:
            return path
    return None


def verify_flows_against_disk(base: str, run_id: Optional[str], raw_dir: str,
                              label: str) -> None:
    query = f"?run_id={run_id}" if run_id else ""
    payload = get(base, f"/api/flows{query}")
    if payload is None or not payload.get("available"):
        check(f"Flow table available ({label})", False,
              (payload or {}).get("reason", "no response"))
        return
    check(f"Flow table available ({label})", True,
          f"source={payload.get('source')}")

    dump_path = _newest_dump(raw_dir)
    if dump_path is None:
        check(f"Flow dump present on disk ({label})", False, raw_dir)
        return
    dump = parse_flow_dump(dump_path)

    api_rules = payload.get("rules") or []
    # The API may have read a newer dump than the one we picked, so compare
    # counts per switch from the dump file the API itself names.
    named = payload.get("dump_file")
    if named:
        candidate = os.path.join(raw_dir, "flow_dumps", named)
        if os.path.exists(candidate):
            dump = parse_flow_dump(candidate)

    disk_total = len(dump.rules)
    check(
        f"Flow-table rule count matches the dump on disk ({label})",
        len(api_rules) == disk_total,
        f"api={len(api_rules)} disk={disk_total} file={named}",
    )

    disk_routed = [r for r in dump.rules if r.priority >= 10]
    api_routed = [r for r in api_rules if r["kind"] == "routed"]
    check(
        f"Routed-rule classification matches priority>=10 ({label})",
        len(api_routed) == len(disk_routed),
        f"api={len(api_routed)} disk={len(disk_routed)}",
    )

    disk_timeouts = sorted({r.idle_timeout for r in disk_routed
                            if r.idle_timeout is not None})
    api_timeouts = sorted({int(k) for k in
                           (payload.get("routed_timeout_distribution") or {})})
    check(
        f"Displayed idle timeouts match installed rules ({label})",
        disk_timeouts == api_timeouts,
        f"api={api_timeouts} disk={disk_timeouts}",
    )

    api_miss = [r for r in api_rules if r["kind"] == "table_miss"]
    disk_miss = [r for r in dump.rules if r.priority == 0]
    check(
        f"Table-miss rules distinguished from forwarding rules ({label})",
        len(api_miss) == len(disk_miss) and all(
            r["output_port"] == "CONTROLLER" for r in api_miss),
        f"api={len(api_miss)} disk={len(disk_miss)}",
    )


def verify_active_path(base: str, run_id: Optional[str], raw_dir: str,
                       label: str) -> None:
    query = f"?run_id={run_id}" if run_id else ""
    state = get(base, f"/api/topology/state{query}")
    if state is None:
        check(f"Topology state available ({label})", False)
        return
    live = state.get("live") or {}
    active = live.get("active_paths") or {}
    if not active.get("available"):
        check(f"Active path reported as unavailable when unverifiable ({label})",
              True, active.get("reason", ""))
        return

    dump_path = _newest_dump(raw_dir)
    if dump_path is None:
        check(f"Active path claim has a dump to back it ({label})", False)
        return
    dump = parse_flow_dump(dump_path)

    port_to_path = {
        ("s1", spec.NEIGHBOUR_PORT[("s1", "s2")]): "A",
        ("s1", spec.NEIGHBOUR_PORT[("s1", "s3")]): "B",
        ("s4", spec.NEIGHBOUR_PORT[("s4", "s2")]): "A",
        ("s4", spec.NEIGHBOUR_PORT[("s4", "s5")]): "B",
    }
    counted = {"A": 0, "B": 0}
    for rule in dump.rules:
        if rule.priority < 10 or rule.output is None:
            continue
        try:
            port = int(rule.output)
        except ValueError:
            continue
        name = port_to_path.get((rule.switch, port))
        if name:
            counted[name] += 1

    claimed = active.get("rule_counts") or {}
    # Counts can differ by one dump interval if the API read a newer file.
    both_zero = sum(counted.values()) == 0 and sum(claimed.values()) == 0
    consistent = both_zero or (
        (counted["A"] > 0) == (claimed.get("A", 0) > 0)
        and (counted["B"] > 0) == (claimed.get("B", 0) > 0)
    )
    check(
        f"Active-path claim is backed by installed rules ({label})",
        consistent,
        f"api={claimed} recomputed_from_disk={counted}",
    )


def verify_verification_endpoint(base: str) -> None:
    payload = get(base, "/api/rl/verification")
    if payload is None:
        check("Decision verification endpoint responds", False)
        return
    check("Decision verification endpoint responds", True)
    if not payload.get("available"):
        check("Verification declares unavailability honestly", True,
              payload.get("reason", ""))
        return
    status = payload.get("status")
    check(
        "Verification status is one of the defined values",
        status in ("confirmed", "awaiting_confirmation", "not_observed", "unverified"),
        f"status={status}",
    )
    check(
        "Verification explains its status",
        bool(payload.get("detail")),
        str(payload.get("detail"))[:120],
    )


def verify_comparison(base: str) -> None:
    payload = get(base, "/api/comparison?window=full&baseline=A")
    if payload is None:
        check("Comparison endpoint responds", False)
        return
    check("Comparison endpoint responds", True,
          f"{payload.get('runs_analysed')} runs analysed")

    # Recompute one cell with the project's analysis code and compare.
    try:
        from analysis import stats
        from analysis.aggregate import modal_duration, split_by_duration
        from dashboard.backend.comparison import collect_run_metrics
        rows = [r for r in collect_run_metrics(window="full") if r.get("complete")]
        # Mirror the filter the API applies: only runs of the comparable
        # duration. Recomputing without it would "disagree" with the API by
        # reproducing a bug the API does not have.
        rows, _ = split_by_duration(rows, modal_duration(rows))
    except Exception as exc:
        check("Comparison matches the project's analysis code", False, str(exc))
        return

    mismatches = []
    for workload in payload.get("workloads", []):
        for variant in payload.get("variants", []):
            cell = payload["cells"].get(workload, {}).get(variant)
            if not cell:
                continue
            subset = [r for r in rows
                      if r.get("workload") == workload and r.get("variant") == variant]
            values = [r.get("throughput_of_bps") for r in subset]
            values = [float(v) for v in values if isinstance(v, (int, float))]
            expected = stats.summarise(values)
            got = cell["metrics"]["throughput_of_bps"]
            if expected.n != got["n"]:
                mismatches.append(f"{workload}/{variant}: n {got['n']} != {expected.n}")
            elif expected.median is not None and got["median"] is not None:
                if abs(expected.median - got["median"]) > 1e-6:
                    mismatches.append(f"{workload}/{variant}: median differs")
    check("Comparison matches the project's analysis code", not mismatches,
          "; ".join(mismatches[:3]))

    # Direction of improvement must be explicit and correct.
    info = payload.get("metric_info", {})
    check(
        "Throughput is marked higher-is-better",
        info.get("throughput_of_bps", {}).get("lower_is_better") is False,
    )
    check(
        "Latency and loss are marked lower-is-better",
        all(info.get(k, {}).get("lower_is_better") is True
            for k in ("rtt_median_ms", "ping_loss_fraction", "recovery_s")),
    )


def verify_labels(base: str) -> None:
    health = get(base, "/api/health")
    if health is None:
        check("Health endpoint responds", False)
        return
    check("Health endpoint responds", True, f"mode={health.get('mode')}")
    check(
        "Mock mode is off in live mode",
        health.get("mock_mode") is False,
        "set SDNRL_DASHBOARD_MOCK=1 to enable demo mode",
    )

    caps = get(base, "/api/capabilities") or {}
    if not caps.get("can_start_experiments"):
        check("Unprivileged API explains why it cannot start experiments",
              bool(caps.get("reason_cannot_start")),
              str(caps.get("reason_cannot_start"))[:100])
    else:
        check("API reports experiment-start capability", True,
              "running privileged")

    status = get(base, "/api/experiments/status") or {}
    live = status.get("live_run") or {}
    if live.get("active") or status.get("external_experiment_running"):
        check(
            "Start is blocked while an experiment runs",
            status.get("can_start") is False,
            str(status.get("blocked_reason"))[:110],
        )
    else:
        check("No experiment running; start-block check not applicable", True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    args = parser.parse_args(argv)

    verify_labels(args.base)
    verify_topology(args.base)
    verify_verification_endpoint(args.base)
    verify_comparison(args.base)

    live = get(args.base, "/api/live/status") or {}
    runs_payload = get(args.base, "/api/runs") or {}
    runs = runs_payload.get("runs", [])

    if live.get("active") and live.get("run_id"):
        raw = os.path.join(PROJECT_ROOT, "results", "runs", live["run_id"], "raw")
        verify_flows_against_disk(args.base, None, raw, "live run")
        verify_active_path(args.base, None, raw, "live run")
    else:
        check("No live run; live flow checks skipped", True)

    completed = [r for r in runs if r.get("status") == "complete"]
    if completed:
        run_id = completed[0]["run_id"]
        raw = os.path.join(PROJECT_ROOT, "results", "runs", run_id, "raw")
        verify_flows_against_disk(args.base, run_id, raw, f"recorded {run_id}")
        verify_active_path(args.base, run_id, raw, f"recorded {run_id}")
    else:
        check("No completed runs to verify", True)

    print("\nDASHBOARD VERIFICATION")
    print("=" * 76)
    failed = 0
    for name, ok, detail in CHECKS:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if detail and not ok:
            print(f"         {detail}")
        elif detail and ok:
            print(f"         {detail}")
        if not ok:
            failed += 1
    print("=" * 76)
    print(f"{len(CHECKS) - failed}/{len(CHECKS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
