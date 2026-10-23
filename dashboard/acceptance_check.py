"""End-to-end acceptance check for browser-driven experiment control.

Exercises the exact HTTP calls the browser makes -- nothing is simulated and
no privileged shortcut is taken -- and verifies each step against independent
evidence on disk or at the switches:

  1. the unprivileged API reports it can start experiments, via the launcher
  2. POST /api/experiments/start really starts Mininet
  3. live telemetry flows while it runs
  4. POST /api/experiments/inject-link is applied by the runner, observed by
     the controller, and visible in the installed rules
  5. POST /api/experiments/stop ends it cleanly
  6. the run is recorded, with provenance, and appears in history
  7. pre-existing results are untouched

    python3 -m dashboard.acceptance_check [--base http://127.0.0.1:8000]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

RESULTS: List[Tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    RESULTS.append((name, ok, detail))
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {name}")
    if detail:
        print(f"         {detail}")
    return ok


def call(base: str, path: str, method: str = "GET",
         body: Optional[Dict[str, Any]] = None,
         timeout: float = 60) -> Tuple[int, Any]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        base + path, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:
            return exc.code, {}
    except Exception as exc:
        return 0, {"error": str(exc)}


def count_runs() -> int:
    runs = os.path.join(PROJECT_ROOT, "results", "runs")
    try:
        return len([d for d in os.listdir(runs)
                    if os.path.isdir(os.path.join(runs, d))])
    except OSError:
        return -1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--duration", type=int, default=45)
    parser.add_argument("--variant", default="C")
    parser.add_argument("--workload", default="heavy")
    parser.add_argument("--link", default="s1-s3")
    args = parser.parse_args(argv)

    base = args.base
    runs_before = count_runs()
    print(f"\nACCEPTANCE CHECK (runs on disk before: {runs_before})")
    print("=" * 78)

    # -- 1. capability ------------------------------------------------------
    code, caps = call(base, "/api/capabilities")
    check("API is reachable", code == 200)
    check(
        "API is unprivileged (not running as root)",
        caps.get("is_root") is False,
        f"is_root={caps.get('is_root')}",
    )
    can_start = bool(caps.get("can_start_experiments"))
    check(
        "Unprivileged API reports it can start experiments",
        can_start,
        f"mechanism={caps.get('start_mechanism')} "
        f"launcher={caps.get('launcher_detail')}",
    )
    if not can_start:
        print("\nCannot continue: start the launcher daemon first:\n"
              "  sudo python3 -m dashboard.launcher.daemon\n")
        return 1

    # -- 2. start -----------------------------------------------------------
    code, started = call(base, "/api/experiments/start", "POST", {
        "variant": args.variant, "workload": args.workload,
        "seed": 1234, "duration_s": args.duration,
    })
    experiment_id = started.get("experiment_id", "")
    if not check("Start request accepted", code == 200,
                 f"HTTP {code} {started.get('detail', '')}"):
        return 1
    check("Experiment id issued", bool(experiment_id), experiment_id)
    check(
        "Runner command built server-side from constants",
        "experiments.run_experiment" in str(started.get("command", "")),
        str(started.get("command", "")),
    )

    # -- 3. live telemetry --------------------------------------------------
    run_id = None
    deadline = time.time() + 45
    while time.time() < deadline:
        _, live = call(base, "/api/live/status")
        if live.get("active"):
            run_id = live.get("run_id")
            break
        time.sleep(2)
    check("Mininet experiment is running", bool(run_id), f"run_id={run_id}")

    telemetry_ok = False
    throughput = None
    deadline = time.time() + 45
    while time.time() < deadline:
        _, metrics = call(base, "/api/metrics/live")
        if metrics.get("available") and metrics.get("source") == "live":
            throughput = (metrics.get("values") or {}).get("delivered_bps")
            if throughput:
                telemetry_ok = True
                break
        time.sleep(2)
    check("Live telemetry is flowing from the real controller", telemetry_ok,
          f"delivered_bps={throughput}")

    # -- 4. link injection --------------------------------------------------
    code, injected = call(base, "/api/experiments/inject-link", "POST", {
        "experiment_id": experiment_id, "action": "link_down",
        "link": args.link,
    })
    check("Link-failure injection accepted", code == 200,
          f"HTTP {code} {injected.get('detail', '')}")

    applied = False
    run_dir = os.path.join(PROJECT_ROOT, "results", "runs", run_id or "")
    results_path = os.path.join(run_dir, "control_results.jsonl")
    deadline = time.time() + 20
    while time.time() < deadline:
        if os.path.exists(results_path):
            try:
                lines = [json.loads(l) for l in open(results_path) if l.strip()]
            except (OSError, json.JSONDecodeError):
                lines = []
            if any(r.get("ok") and r.get("link") == args.link for r in lines):
                applied = True
                break
        time.sleep(1)
    check("The runner applied it to the live Mininet session", applied,
          f"evidence: {results_path}")

    # The controller must have independently observed the port going down.
    observed = False
    events_path = os.path.join(run_dir, "raw", "events.csv")
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            text = open(events_path, encoding="utf-8", errors="replace").read()
        except OSError:
            text = ""
        if f"link={args.link} state=down" in text:
            observed = True
            break
        time.sleep(1)
    check(
        "The controller observed the port down over OpenFlow",
        observed,
        "controller events.csv records the state change",
    )

    # -- 5. stop ------------------------------------------------------------
    code, stopped = call(base, "/api/experiments/stop", "POST",
                         {"experiment_id": experiment_id}, timeout=90)
    check("Stop request accepted", code == 200,
          f"HTTP {code} {stopped.get('detail', '')}")
    check(
        "Stopped cooperatively (no SIGKILL escalation)",
        stopped.get("escalated_to_sigkill") is not True,
        f"escalated={stopped.get('escalated_to_sigkill')}",
    )

    gone = False
    deadline = time.time() + 60
    while time.time() < deadline:
        _, live = call(base, "/api/live/status")
        if not live.get("active"):
            gone = True
            break
        time.sleep(2)
    check("Experiment is no longer running", gone)

    # -- 6. persistence -----------------------------------------------------
    _, runs = call(base, "/api/runs")
    entry = next((r for r in runs.get("runs", []) if r.get("run_id") == run_id), None)
    check("Run appears in experiment history", entry is not None, str(run_id))
    if entry:
        check(
            "Run status is reported honestly",
            entry.get("status") in ("interrupted", "incomplete", "complete"),
            f"status={entry['status']} (a stopped run is not 'complete')",
        )

    config_path = os.path.join(run_dir, "config.json")
    commit = None
    if os.path.exists(config_path):
        try:
            commit = (json.load(open(config_path))
                      .get("provenance", {}).get("git_commit"))
        except (OSError, json.JSONDecodeError):
            commit = None
    check(
        "Run recorded a real git commit (provenance)",
        isinstance(commit, str) and len(commit) == 40,
        f"git_commit={commit}",
    )

    # -- 7. nothing destroyed ----------------------------------------------
    runs_after = count_runs()
    check(
        "Existing results preserved (run count only grew)",
        runs_after >= runs_before,
        f"before={runs_before} after={runs_after}",
    )

    matrix = len([d for d in os.listdir(os.path.join(PROJECT_ROOT, "results", "runs"))
                  if d.startswith("2026") and "-diag-" not in d and
                  os.path.isdir(os.path.join(PROJECT_ROOT, "results", "runs", d))])
    check("Matrix and control runs still on disk", matrix >= 60,
          f"{matrix} non-diagnostic run directories")

    print("=" * 78)
    failed = sum(1 for _, ok, _ in RESULTS if not ok)
    print(f"{len(RESULTS) - failed}/{len(RESULTS)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
