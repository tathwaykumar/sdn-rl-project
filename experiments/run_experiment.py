"""Experiment runner: one run, or the whole matrix.

A "run" is one (variant, workload, seed) triple.  Each run gets its own
directory under ``results/runs/`` containing:

    config.json          exactly what was run, including software versions,
                         git commit, reward weights, agent hyperparameters
    raw/                 untouched measurement output -- iperf3 JSON, ping
                         text, OpenFlow counter CSVs, periodic flow-table
                         dumps straight from ovs-ofctl
    workload.json        the seeded traffic schedule as actually executed
    controller.log       the controller's own stderr/stdout
    manifest.json        completeness check: which expected artefacts exist
    policy.json          learned greedy policy (RL variants only)

Nothing in a run directory is ever edited after the run.  ``analysis/`` reads
these directories and writes derived CSVs elsewhere, so every number in the
report traces back to a raw file, and a parsing fix never requires re-running
an experiment.

Must be run as root, because Mininet is::

    sudo python3 -m experiments.run_experiment --variant A --workload heavy --seed 1
    sudo python3 -m experiments.run_experiment --matrix --seeds 5
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from agent.actions import TIMEOUT_CLASSES_S  # noqa: E402
from agent.qlearning import QLearningConfig  # noqa: E402
from agent.reward import RewardNormalisers, RewardWeights  # noqa: E402
from experiments.controller_proc import ControllerProcess  # noqa: E402
from experiments.workloads import build_workload  # noqa: E402
from topo import spec  # noqa: E402

VARIANT_APPS = {
    "A": "controller.baseline",
    "B": "controller.rl_route",
    "C": "controller.rl_joint",
}

VARIANT_LABELS = {
    "A": "conventional routing, fixed flow timeouts",
    "B": "RL routing, fixed flow timeouts",
    "C": "joint RL routing + RL flow timeouts",
}

DEFAULT_WORKLOADS = ("light", "heavy", "churn", "failure")
DEFAULT_DURATION_S = 90
DEFAULT_FIXED_IDLE_TIMEOUT_S = 15


# --------------------------------------------------------------------------
# Provenance
# --------------------------------------------------------------------------

def _cmd(args: List[str]) -> str:
    """Run a command and return its first line, or "" if it failed.

    Returning the *error text* on failure (as this used to) is worse than
    returning nothing: a provenance field reading "fatal: ..." looks like a
    recorded value and silently passes any check that only tests for
    non-emptiness.
    """
    try:
        out = subprocess.run(args, capture_output=True, text=True, timeout=15)
        if out.returncode != 0:
            return ""
        text = (out.stdout or "").strip()
        return text.splitlines()[0] if text else ""
    except Exception:
        return ""


#: Experiments run as root while the checkout is owned by the developer, so
#: git refuses to operate on it ("detected dubious ownership") unless the
#: directory is explicitly trusted for this invocation. This is scoped to a
#: single command via -c; it does not change the user's git configuration.
_GIT_BASE = ["git", "-c", f"safe.directory={PROJECT_ROOT}", "-C", PROJECT_ROOT]


def _git_commit() -> Optional[str]:
    """Current commit hash, or None if it genuinely cannot be determined."""
    value = _cmd(_GIT_BASE + ["rev-parse", "HEAD"])
    return value or None


def _git_dirty() -> Optional[bool]:
    """Whether the tree has uncommitted changes, or None if unknown.

    None matters: reporting False when the check failed would claim the run
    came from a clean tree without having established it.
    """
    try:
        out = subprocess.run(
            _GIT_BASE + ["status", "--porcelain"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    return bool(out.stdout.strip())


def provenance() -> Dict[str, object]:
    """Record the exact software stack. Results without this are not reproducible."""
    return {
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git_commit(),
        "git_dirty": _git_dirty(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "kernel": platform.release(),
        "os_release": _cmd(["bash", "-c", "grep PRETTY_NAME /etc/os-release | cut -d= -f2"]),
        "ovs_version": _cmd(["ovs-vsctl", "--version"]),
        "mininet_version": _cmd(["mn", "--version"]),
        "os_ken_version": _cmd(
            ["bash", "-c", "dpkg-query -W -f='${Version}' python3-os-ken 2>/dev/null"]
        ),
        "iperf3_version": _cmd(["iperf3", "--version"]),
        "is_wsl": "microsoft" in platform.release().lower(),
    }


# --------------------------------------------------------------------------
# Flow-table dumper
# --------------------------------------------------------------------------

class FlowDumper(threading.Thread):
    """Periodically snapshot every switch's flow table with ovs-ofctl.

    These dumps are the project's primary behavioural evidence.  The controller
    log says what the agent *decided*; these files say what the switches were
    actually doing, which is the claim that matters.  They are written raw and
    parsed later by ``analysis/``.
    """

    def __init__(self, out_dir: str, interval: float = 5.0):
        super().__init__(daemon=True)
        self.out_dir = out_dir
        self.interval = interval
        self._stop = threading.Event()
        self.samples = 0
        os.makedirs(out_dir, exist_ok=True)

    def run(self) -> None:
        while not self._stop.is_set():
            t = time.time()
            lines = [f"# t={t:.4f}"]
            for name in spec.SWITCHES:
                out = subprocess.run(
                    ["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", name],
                    capture_output=True, text=True,
                )
                lines.append(f"===== {name} =====")
                lines.append(out.stdout.strip() or f"<no output: {out.stderr.strip()}>")
            path = os.path.join(self.out_dir, f"flows_{self.samples:04d}_{t:.0f}.txt")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines) + "\n")
            self.samples += 1
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()


class ControlListener(threading.Thread):
    """Apply link-state commands handed to a *running* experiment.

    The dashboard cannot touch Mininet: it is unprivileged, and the Mininet
    session belongs to this process. So an operator action arrives as a line
    appended to ``<run_dir>/control.jsonl`` by the privileged launcher, and
    this thread -- inside the process that owns ``net`` -- validates and
    applies it.

    Validation here is not a formality. The link name is checked against
    ``topo.spec``'s real core links before anything is touched, so a command
    naming a link that does not exist, or a host access link, is rejected
    rather than passed to Mininet. Every command and its outcome is recorded,
    so an injected failure is as auditable as a scheduled one.
    """

    POLL_INTERVAL_S = 0.5

    def __init__(self, run_dir: str, net, events: List[Dict[str, object]]):
        super().__init__(daemon=True)
        self.control_path = os.path.join(run_dir, "control.jsonl")
        self.result_path = os.path.join(run_dir, "control_results.jsonl")
        self.net = net
        self.events = events
        self._stop = threading.Event()
        self._consumed = 0
        #: Unordered switch pairs that are genuinely links in this topology.
        self._valid_links = {
            frozenset({link.a, link.b}) for link in spec.CORE_LINKS
        }

    def run(self) -> None:
        while not self._stop.is_set():
            try:
                self._poll()
            except Exception:  # a bad command must not kill the listener
                pass
            self._stop.wait(self.POLL_INTERVAL_S)

    def _poll(self) -> None:
        if not os.path.exists(self.control_path):
            return
        with open(self.control_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        if len(lines) <= self._consumed:
            return
        pending = lines[self._consumed:]
        self._consumed = len(lines)
        for line in pending:
            line = line.strip()
            if line:
                self._apply(line)

    def _apply(self, line: str) -> None:
        try:
            command = json.loads(line)
        except json.JSONDecodeError:
            self._record({"ok": False, "error": "malformed command", "raw": line[:200]})
            return

        action = command.get("action")
        link = command.get("link")
        if action not in ("link_down", "link_up"):
            self._record({**command, "ok": False, "error": "unknown action"})
            return
        if not isinstance(link, str) or "-" not in link:
            self._record({**command, "ok": False, "error": "malformed link"})
            return

        a, _, b = link.partition("-")
        if frozenset({a, b}) not in self._valid_links:
            self._record({
                **command, "ok": False,
                "error": f"{link} is not a core link in this topology",
            })
            return

        state = "down" if action == "link_down" else "up"
        try:
            self.net.configLinkStatus(a, b, state)
        except Exception as exc:
            self._record({**command, "ok": False,
                          "error": f"{type(exc).__name__}: {exc}"})
            return

        applied_at = time.time()
        self._record({**command, "ok": True, "applied_at": applied_at,
                      "state": state})
        self.events.append({
            "kind": f"injected_{action}",
            "link": link,
            "t": applied_at,
            "source": "dashboard",
        })
        print(f"  [control] {action} on {link} applied")

    def _record(self, payload: Dict[str, object]) -> None:
        try:
            with open(self.result_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, sort_keys=True) + "\n")
                fh.flush()
        except OSError:
            pass

    def stop(self) -> None:
        self._stop.set()


# --------------------------------------------------------------------------
# One run
# --------------------------------------------------------------------------

def build_run_config(
    variant: str,
    workload: str,
    seed: int,
    run_dir: str,
    duration_s: int,
    poll_interval_s: float,
    fixed_idle_timeout_s: int,
    frozen_agent: bool,
    shuffle_actions: bool,
    qtable_in: Optional[str],
    epsilon_decay_steps: Optional[int],
) -> Dict[str, object]:
    raw_dir = os.path.join(run_dir, "raw")
    agent_cfg = QLearningConfig()
    if epsilon_decay_steps is None:
        # Decay over the first half of the run so the second half is mostly
        # exploitation. Analysis reports the second half separately for exactly
        # this reason.
        epsilon_decay_steps = max(10, int(duration_s / poll_interval_s / 2))
    agent_cfg.epsilon_decay_steps = epsilon_decay_steps

    return {
        "variant": variant,
        "variant_label": VARIANT_LABELS[variant],
        "controller_app": VARIANT_APPS[variant],
        "workload": workload,
        "seed": seed,
        "duration_s": duration_s,
        "run_dir": run_dir,
        "raw_dir": raw_dir,
        "poll_interval_s": poll_interval_s,
        "fixed_idle_timeout_s": fixed_idle_timeout_s,
        "hard_timeout_s": 120,
        "occupancy_cap_rules": 100,
        "timeout_classes_s": list(TIMEOUT_CLASSES_S),
        "frozen_agent": frozen_agent,
        "shuffle_actions": shuffle_actions,
        "qtable_in": qtable_in,
        "qtable_out": os.path.join(run_dir, "agent.qtable.json"),
        "agent": {
            "alpha": agent_cfg.alpha,
            "gamma": agent_cfg.gamma,
            "epsilon_start": agent_cfg.epsilon_start,
            "epsilon_end": agent_cfg.epsilon_end,
            "epsilon_decay_steps": agent_cfg.epsilon_decay_steps,
            "optimistic_init": agent_cfg.optimistic_init,
        },
        "reward_weights": RewardWeights().as_dict(),
        "reward_normalisers": {
            "throughput_ref_bps": RewardNormalisers().throughput_ref_bps,
            "msg_rate_ref": RewardNormalisers().msg_rate_ref,
            "occupancy_cap_rules": RewardNormalisers().occupancy_cap_rules,
        },
        "topology": spec.describe(),
        "provenance": provenance(),
    }


def hand_back_ownership(path: str) -> None:
    """Give run output to the user who owns the project, not to root.

    Mininet forces the runner to run as root, so everything it writes lands
    root-owned and the user cannot read their own results, delete them, or let
    git see them without sudo. Ownership is matched to the project directory,
    and SUDO_UID is preferred when present.
    """
    try:
        if os.geteuid() != 0:
            return
        uid = int(os.environ.get("SUDO_UID") or os.stat(PROJECT_ROOT).st_uid)
        gid = int(os.environ.get("SUDO_GID") or os.stat(PROJECT_ROOT).st_gid)
        for root, dirs, files in os.walk(path):
            os.chown(root, uid, gid)
            for name in dirs + files:
                try:
                    os.chown(os.path.join(root, name), uid, gid)
                except OSError:
                    pass
    except Exception:
        pass  # cosmetic; never fail a completed run over file ownership


def write_manifest(run_dir: str, cfg: Dict[str, object], extra: Dict[str, object]) -> Dict[str, object]:
    """Record which expected artefacts actually exist.

    A run that produced no iperf3 JSON is a failed run, and the manifest is how
    the analysis knows to exclude it rather than quietly averaging over
    whatever happened to survive.
    """
    raw_dir = os.path.join(run_dir, "raw")
    expected = {
        "snapshots.csv": os.path.join(raw_dir, "snapshots.csv"),
        "decisions.csv": os.path.join(raw_dir, "decisions.csv"),
        "events.csv": os.path.join(raw_dir, "events.csv"),
        "raw_port_stats.csv": os.path.join(raw_dir, "raw_port_stats.csv"),
        "raw_flow_stats.csv": os.path.join(raw_dir, "raw_flow_stats.csv"),
        "raw_table_stats.csv": os.path.join(raw_dir, "raw_table_stats.csv"),
        "controller_counters.csv": os.path.join(raw_dir, "controller_counters.csv"),
        "workload.json": os.path.join(run_dir, "workload.json"),
        "controller.log": os.path.join(run_dir, "controller.log"),
    }
    present = {}
    for name, path in expected.items():
        present[name] = {
            "exists": os.path.exists(path),
            "bytes": os.path.getsize(path) if os.path.exists(path) else 0,
        }

    iperf_files = [f for f in os.listdir(raw_dir) if f.startswith("iperf3_") and f.endswith(".json")]
    ping_files = [f for f in os.listdir(raw_dir) if f.startswith("ping_") and f.endswith(".txt")]
    flow_dumps = os.path.join(raw_dir, "flow_dumps")
    n_dumps = len(os.listdir(flow_dumps)) if os.path.isdir(flow_dumps) else 0

    manifest = {
        "variant": cfg["variant"],
        "workload": cfg["workload"],
        "seed": cfg["seed"],
        "expected_artefacts": present,
        "iperf3_json_files": sorted(iperf_files),
        "ping_files": sorted(ping_files),
        "flow_dump_samples": n_dumps,
        **extra,
    }
    # A run is complete only if every expected artefact is non-empty and there
    # is at least one iperf3 result and one flow dump to analyse.
    manifest["complete"] = (
        all(v["exists"] and v["bytes"] > 0 for v in present.values())
        and bool(iperf_files)
        and n_dumps > 0
    )
    with open(os.path.join(run_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
    return manifest


def run_single(
    variant: str,
    workload_name: str,
    seed: int,
    duration_s: int = DEFAULT_DURATION_S,
    poll_interval_s: float = 1.0,
    fixed_idle_timeout_s: int = DEFAULT_FIXED_IDLE_TIMEOUT_S,
    out_root: Optional[str] = None,
    frozen_agent: bool = False,
    shuffle_actions: bool = False,
    qtable_in: Optional[str] = None,
    epsilon_decay_steps: Optional[int] = None,
    tag: str = "",
    verbose: bool = False,
) -> Dict[str, object]:
    if variant not in VARIANT_APPS:
        raise ValueError(f"unknown variant {variant!r}")

    out_root = out_root or os.path.join(PROJECT_ROOT, "results", "runs")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = f"-{tag}" if tag else ""
    run_dir = os.path.join(
        out_root, f"{stamp}-{variant}-{workload_name}-seed{seed}{suffix}"
    )
    raw_dir = os.path.join(run_dir, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    cfg = build_run_config(
        variant, workload_name, seed, run_dir, duration_s, poll_interval_s,
        fixed_idle_timeout_s, frozen_agent, shuffle_actions, qtable_in,
        epsilon_decay_steps,
    )
    config_path = os.path.join(run_dir, "config.json")
    with open(config_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2, sort_keys=True)

    print(f"\n=== RUN {variant}/{workload_name}/seed{seed} -> {os.path.basename(run_dir)}")

    from mininet.log import setLogLevel
    from topo.diamond import build_network, verify_port_mapping

    setLogLevel("warning")

    # Clear any leftover Mininet state. Stale bridges from a crashed previous
    # run would attach to our controller and corrupt the counters.
    subprocess.run(["mn", "-c"], capture_output=True)

    status: Dict[str, object] = {"ok": False, "errors": []}
    controller = ControllerProcess(
        app_module=VARIANT_APPS[variant],
        config_path=config_path,
        log_path=os.path.join(run_dir, "controller.log"),
        project_root=PROJECT_ROOT,
        verbose=verbose,
    )
    net = None
    dumper = None
    controller_listener = None
    try:
        controller.start()
        if not controller.wait_ready(timeout=25):
            status["errors"].append("controller failed to listen")
            raise RuntimeError("controller not ready")

        net = build_network()
        problems = verify_port_mapping(net)
        if problems:
            # Abort rather than measure: a port mismatch means the controller's
            # idea of the topology is wrong, so every routing result would be
            # meaningless.
            status["errors"].extend(problems)
            raise RuntimeError(f"port mapping mismatch: {problems}")

        # Give the switches time to finish the OF handshake and receive their
        # table-miss rules before traffic starts.
        time.sleep(3.0)

        dumper = FlowDumper(os.path.join(raw_dir, "flow_dumps"), interval=5.0)
        dumper.start()

        def link_control(a: str, b: str, action: str) -> None:
            net.configLinkStatus(a, b, action)

        workload = build_workload(workload_name, seed, raw_dir, duration_s)

        # Accept operator link-state commands for the lifetime of the run.
        # Started after the topology is verified so a command can never be
        # applied to a half-built network.
        injected_events: List[Dict[str, object]] = []
        controller_listener = ControlListener(run_dir, net, injected_events)
        controller_listener.start()

        result = workload.run(net, link_control=link_control)
        if injected_events:
            result.events.extend(injected_events)

        with open(os.path.join(run_dir, "workload.json"), "w", encoding="utf-8") as fh:
            json.dump(result.as_dict(), fh, indent=2, sort_keys=True)

        status["ok"] = True
        status["workload_errors"] = result.errors
    except Exception as exc:
        status["errors"].append(f"{type(exc).__name__}: {exc}")
        print(f"  !! run failed: {exc}")
    finally:
        if controller_listener is not None:
            controller_listener.stop()
            controller_listener.join(timeout=5)
        if dumper is not None:
            dumper.stop()
            dumper.join(timeout=10)
        # Let the controller observe the post-traffic drain before shutdown, so
        # the final telemetry interval is not truncated mid-measurement.
        time.sleep(2.0)
        if net is not None:
            try:
                net.stop()
            except Exception as exc:
                status["errors"].append(f"net.stop failed: {exc}")
        rc = controller.stop()
        status["controller_exit_code"] = rc
        subprocess.run(["mn", "-c"], capture_output=True)

    manifest = write_manifest(run_dir, cfg, {
        "run_status": status,
        "flow_dump_interval_s": 5.0,
    })
    hand_back_ownership(run_dir)
    print(
        f"  complete={manifest['complete']} "
        f"iperf_files={len(manifest['iperf3_json_files'])} "
        f"flow_dumps={manifest['flow_dump_samples']} "
        f"controller_rc={status.get('controller_exit_code')}"
    )
    if status["errors"]:
        print(f"  errors: {status['errors']}")
    return {"run_dir": run_dir, "manifest": manifest, "status": status}


# --------------------------------------------------------------------------
# Matrix
# --------------------------------------------------------------------------

def run_matrix(
    variants: List[str],
    workloads: List[str],
    seeds: List[int],
    duration_s: int,
    out_root: Optional[str] = None,
    **kwargs,
) -> Dict[str, object]:
    """Run every (variant, workload, seed) combination.

    Seeds are the outer loop so that a partial matrix still contains all
    variants for the seeds it reached.  Ordering seeds innermost would leave an
    interrupted matrix with, say, five seeds of A and none of C -- unusable for
    comparison.
    """
    results = []
    total = len(variants) * len(workloads) * len(seeds)
    done = 0
    started = time.time()

    for seed in seeds:
        for workload in workloads:
            for variant in variants:
                done += 1
                elapsed = time.time() - started
                rate = elapsed / max(1, done - 1) if done > 1 else 0
                eta = rate * (total - done + 1)
                print(f"\n[{done}/{total}] elapsed={elapsed/60:.1f}min "
                      f"eta={eta/60:.1f}min")
                try:
                    res = run_single(
                        variant=variant,
                        workload_name=workload,
                        seed=seed,
                        duration_s=duration_s,
                        out_root=out_root,
                        **kwargs,
                    )
                    results.append({
                        "variant": variant, "workload": workload, "seed": seed,
                        "run_dir": res["run_dir"],
                        "complete": res["manifest"]["complete"],
                        "errors": res["status"]["errors"],
                    })
                except Exception as exc:
                    print(f"  !! unrecoverable: {exc}")
                    results.append({
                        "variant": variant, "workload": workload, "seed": seed,
                        "run_dir": None, "complete": False,
                        "errors": [f"{type(exc).__name__}: {exc}"],
                    })

    n_complete = sum(1 for r in results if r["complete"])
    summary = {
        "requested_runs": total,
        "attempted_runs": len(results),
        "complete_runs": n_complete,
        "incomplete_runs": len(results) - n_complete,
        "variants": variants,
        "workloads": workloads,
        "seeds": seeds,
        "duration_s": duration_s,
        "wall_clock_s": round(time.time() - started, 1),
        "runs": results,
    }
    out_root = out_root or os.path.join(PROJECT_ROOT, "results", "runs")
    path = os.path.join(out_root, f"matrix-{datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True)
    print(f"\n=== MATRIX DONE: {n_complete}/{total} complete; summary -> {path}")
    return summary


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--variant", choices=sorted(VARIANT_APPS), help="single run variant")
    p.add_argument("--workload", choices=sorted(DEFAULT_WORKLOADS), help="single run workload")
    p.add_argument("--seed", type=int, default=1)
    p.add_argument("--duration", type=int, default=DEFAULT_DURATION_S)
    p.add_argument("--poll-interval", type=float, default=1.0)
    p.add_argument("--idle-timeout", type=int, default=DEFAULT_FIXED_IDLE_TIMEOUT_S)
    p.add_argument("--matrix", action="store_true", help="run the full matrix")
    p.add_argument("--variants", default="A,B,C")
    p.add_argument("--workloads", default=",".join(DEFAULT_WORKLOADS))
    p.add_argument("--seeds", type=int, default=5, help="number of seeds (1..N)")
    p.add_argument("--out", default=None, help="output root (default results/runs)")
    p.add_argument("--frozen-agent", action="store_true",
                   help="null control: select actions but never learn or explore")
    p.add_argument("--shuffle-actions", action="store_true",
                   help="diagnostic: permute action->(path,timeout) mapping")
    p.add_argument("--qtable-in", default=None)
    p.add_argument("--epsilon-decay-steps", type=int, default=None)
    p.add_argument("--tag", default="", help="suffix for the run directory name")
    p.add_argument("--verbose", action="store_true")
    args = p.parse_args(argv)

    if os.geteuid() != 0:
        print("ERROR: must run as root (Mininet requires it)")
        return 2
    if shutil.which("iperf3") is None:
        print("ERROR: iperf3 not found")
        return 2

    if args.matrix:
        run_matrix(
            variants=[v.strip() for v in args.variants.split(",") if v.strip()],
            workloads=[w.strip() for w in args.workloads.split(",") if w.strip()],
            seeds=list(range(1, args.seeds + 1)),
            duration_s=args.duration,
            out_root=args.out,
            poll_interval_s=args.poll_interval,
            fixed_idle_timeout_s=args.idle_timeout,
            frozen_agent=args.frozen_agent,
            shuffle_actions=args.shuffle_actions,
            qtable_in=args.qtable_in,
            epsilon_decay_steps=args.epsilon_decay_steps,
            tag=args.tag,
            verbose=args.verbose,
        )
        return 0

    if not args.variant or not args.workload:
        print("ERROR: need --variant and --workload, or --matrix")
        return 2

    res = run_single(
        variant=args.variant,
        workload_name=args.workload,
        seed=args.seed,
        duration_s=args.duration,
        poll_interval_s=args.poll_interval,
        fixed_idle_timeout_s=args.idle_timeout,
        out_root=args.out,
        frozen_agent=args.frozen_agent,
        shuffle_actions=args.shuffle_actions,
        qtable_in=args.qtable_in,
        epsilon_decay_steps=args.epsilon_decay_steps,
        tag=args.tag,
        verbose=args.verbose,
    )
    return 0 if res["manifest"]["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
