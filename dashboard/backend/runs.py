"""Discovering runs and identifying which one (if any) is live.

A run directory is *in progress* when it has a `config.json` but no
`manifest.json` -- the runner writes the manifest only after teardown. That
alone is not enough, because an interrupted run also leaves no manifest, so
liveness is confirmed against the actual process table.

Nothing in this module writes to a run directory. Run output is immutable
evidence; the dashboard is strictly a reader.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import RUNS_DIR

#: Process command fragments that indicate project experiment activity.
EXPERIMENT_PROCESS_MARKERS = (
    "experiments.run_experiment",
    "experiments.run_diagnostics",
    "experiments.smoke",
    "controller.launcher",
)


@dataclass
class ProcessInfo:
    pid: int
    cmd: str
    elapsed_s: int


def scan_experiment_processes() -> List[ProcessInfo]:
    """Processes belonging to this project's experiment machinery.

    Matching is on the project's own module paths, so it cannot pick up
    unrelated user processes.
    """
    out: List[ProcessInfo] = []
    try:
        proc = subprocess.run(
            ["ps", "-eo", "pid,etimes,cmd"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return out

    for line in proc.stdout.splitlines()[1:]:
        parts = line.strip().split(None, 2)
        if len(parts) != 3:
            continue
        pid_s, elapsed_s, cmd = parts
        if "ps -eo" in cmd or " grep " in cmd:
            continue
        if not any(marker in cmd for marker in EXPERIMENT_PROCESS_MARKERS):
            continue
        try:
            out.append(ProcessInfo(int(pid_s), cmd, int(elapsed_s)))
        except ValueError:
            continue
    return out


@dataclass
class RunRef:
    """A run directory with just enough metadata to list and classify it."""

    run_id: str
    path: str
    config: Dict[str, object] = field(default_factory=dict)
    manifest: Optional[Dict[str, object]] = None
    mtime: float = 0.0

    @property
    def variant(self) -> str:
        return str(self.config.get("variant", "?"))

    @property
    def workload(self) -> str:
        return str(self.config.get("workload", "?"))

    @property
    def seed(self) -> int:
        try:
            return int(self.config.get("seed", -1))
        except (TypeError, ValueError):
            return -1

    @property
    def raw_dir(self) -> str:
        return os.path.join(self.path, "raw")

    @property
    def has_manifest(self) -> bool:
        return self.manifest is not None

    @property
    def complete(self) -> bool:
        return bool(self.manifest and self.manifest.get("complete"))

    def status(self, live_run_id: Optional[str]) -> str:
        """One of: running | complete | incomplete | interrupted."""
        if live_run_id and self.run_id == live_run_id:
            return "running"
        if not self.has_manifest:
            # No manifest and not running: the runner never finished teardown.
            return "interrupted"
        return "complete" if self.complete else "incomplete"

    def started_at(self) -> Optional[float]:
        cfg = os.path.join(self.path, "config.json")
        try:
            return os.path.getmtime(cfg)
        except OSError:
            return None

    def ended_at(self) -> Optional[float]:
        man = os.path.join(self.path, "manifest.json")
        try:
            return os.path.getmtime(man)
        except OSError:
            return None

    def git_commit(self) -> Optional[str]:
        prov = self.config.get("provenance")
        if isinstance(prov, dict):
            value = prov.get("git_commit")
            return str(value) if value else None
        return None


def _load_json(path: str) -> Optional[Dict[str, object]]:
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        # A half-written file during an active run is normal, not an error.
        return None


def load_run_ref(path: str) -> Optional[RunRef]:
    config = _load_json(os.path.join(path, "config.json"))
    if config is None:
        return None
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        mtime = 0.0
    return RunRef(
        run_id=os.path.basename(path),
        path=path,
        config=config,
        manifest=_load_json(os.path.join(path, "manifest.json")),
        mtime=mtime,
    )


def discover_runs(runs_dir: str = RUNS_DIR) -> List[RunRef]:
    """All run directories, newest first."""
    if not os.path.isdir(runs_dir):
        return []
    refs: List[RunRef] = []
    try:
        entries = os.listdir(runs_dir)
    except OSError:
        return []
    for entry in entries:
        full = os.path.join(runs_dir, entry)
        if not os.path.isdir(full):
            continue
        ref = load_run_ref(full)
        if ref is not None:
            refs.append(ref)
    refs.sort(key=lambda r: r.started_at() or r.mtime, reverse=True)
    return refs


def find_run(run_id: str, runs_dir: str = RUNS_DIR) -> Optional[RunRef]:
    """Look up one run, rejecting any path trickery in ``run_id``."""
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        return None
    path = os.path.join(runs_dir, run_id)
    if os.path.realpath(os.path.dirname(path)) != os.path.realpath(runs_dir):
        return None
    if not os.path.isdir(path):
        return None
    return load_run_ref(path)


@dataclass
class LiveStatus:
    """Whether an experiment is running right now, and which one."""

    active: bool = False
    run_id: Optional[str] = None
    run_path: Optional[str] = None
    variant: Optional[str] = None
    workload: Optional[str] = None
    seed: Optional[int] = None
    started_at: Optional[float] = None
    elapsed_s: Optional[float] = None
    duration_s: Optional[float] = None
    matrix_running: bool = False
    processes: List[Dict[str, object]] = field(default_factory=list)
    detail: str = ""

    def as_dict(self) -> Dict[str, object]:
        return {
            "active": self.active,
            "run_id": self.run_id,
            "variant": self.variant,
            "workload": self.workload,
            "seed": self.seed,
            "started_at": self.started_at,
            "elapsed_s": round(self.elapsed_s, 1) if self.elapsed_s else None,
            "duration_s": self.duration_s,
            "matrix_running": self.matrix_running,
            "processes": self.processes,
            "detail": self.detail,
        }


def live_status(runs_dir: str = RUNS_DIR) -> LiveStatus:
    """Identify the running experiment, if any.

    Both signals must agree: a run directory without a manifest *and* a live
    experiment process. Either alone is ambiguous -- an interrupted run leaves
    a manifest-less directory behind forever, and a controller process can
    briefly outlive its run.
    """
    status = LiveStatus()
    procs = scan_experiment_processes()
    status.processes = [
        {"pid": p.pid, "cmd": p.cmd, "elapsed_s": p.elapsed_s} for p in procs
    ]
    status.matrix_running = any("--matrix" in p.cmd for p in procs)
    runner_alive = any(
        "experiments.run_experiment" in p.cmd or "experiments.run_diagnostics" in p.cmd
        for p in procs
    )

    candidates = [r for r in discover_runs(runs_dir) if not r.has_manifest]
    if not candidates:
        status.detail = (
            "No run directory is awaiting a manifest."
            if not runner_alive else
            "An experiment process is running but has not created a run "
            "directory yet."
        )
        status.active = False
        return status

    newest = candidates[0]
    if not runner_alive:
        status.detail = (
            f"{newest.run_id} has no manifest but no experiment process is "
            "running; it was most likely interrupted."
        )
        status.active = False
        return status

    started = newest.started_at()
    status.active = True
    status.run_id = newest.run_id
    status.run_path = newest.path
    status.variant = newest.variant
    status.workload = newest.workload
    status.seed = newest.seed
    status.started_at = started
    status.elapsed_s = (time.time() - started) if started else None
    try:
        status.duration_s = float(newest.config.get("duration_s"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        status.duration_s = None
    status.detail = (
        f"Run {newest.run_id} is in progress"
        + (" as part of a matrix sweep." if status.matrix_running else ".")
    )
    return status
