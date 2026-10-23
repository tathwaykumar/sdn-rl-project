"""Safe experiment control.

Rules this module exists to enforce:

* **Validated enumerations only.** Variant, workload and seed are checked
  against the runner's own tables before anything is launched. The subprocess
  is built as an argv list with ``shell=False``, so no input of any kind can
  become a shell command. The API exposes no free-form command field.
* **One experiment at a time.** Experiments share Mininet, Open vSwitch and TCP
  6653 exclusively. Starting a second one would corrupt both, so a start is
  refused while anything is running -- including a matrix sweep this process
  did not launch.
* **Only stop what we started.** Stop targets the PID this backend launched and
  nothing else. A matrix sweep started from a terminal is reported but is not
  stoppable from the browser: silently killing someone's two-hour experiment
  because a web button was clicked is not a tradeoff worth making.
* **No privilege escalation.** Mininet needs root and this API does not try to
  obtain it. Unprivileged, start is refused with the exact command to run.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import launcher_link
from .config import PROJECT_ROOT, probe_capabilities
from .launcher_link import CLIENT as LAUNCHER
from .runs import live_status, scan_experiment_processes

from dashboard.launcher.client import LauncherError, LauncherUnavailable
from dashboard.launcher.protocol import ProtocolError

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from experiments.run_experiment import (  # noqa: E402
    DEFAULT_DURATION_S, DEFAULT_WORKLOADS, VARIANT_APPS, VARIANT_LABELS,
)

MIN_DURATION_S = 10
MAX_DURATION_S = 600
MIN_SEED = 1
MAX_SEED = 10_000
#: Idle timeout offered by the UI for variants A and B. Variant C selects its
#: own, so the control is disabled there rather than silently ignored.
ALLOWED_IDLE_TIMEOUTS = (5, 15, 60)


class ExperimentError(Exception):
    """Raised with a human-readable reason; surfaced to the UI verbatim."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class LaunchedExperiment:
    pid: int
    variant: str
    workload: str
    seed: int
    duration_s: int
    started_at: float
    log_path: str
    argv: List[str] = field(default_factory=list)
    finished_at: Optional[float] = None
    returncode: Optional[int] = None
    stopped_by_user: bool = False

    def as_dict(self) -> Dict[str, object]:
        return {
            "pid": self.pid,
            "variant": self.variant,
            "workload": self.workload,
            "seed": self.seed,
            "duration_s": self.duration_s,
            "started_at": self.started_at,
            "elapsed_s": round(
                (self.finished_at or time.time()) - self.started_at, 1
            ),
            "log_path": self.log_path,
            "command": " ".join(self.argv),
            "finished_at": self.finished_at,
            "returncode": self.returncode,
            "stopped_by_user": self.stopped_by_user,
            "running": self.finished_at is None,
        }


def options() -> Dict[str, object]:
    """Everything the UI needs to build a valid form."""
    return {
        "variants": [
            {"id": v, "label": VARIANT_LABELS[v], "app": VARIANT_APPS[v],
             "selects_own_timeout": v == "C"}
            for v in sorted(VARIANT_APPS)
        ],
        "workloads": [
            {"id": w, "description": _WORKLOAD_HELP.get(w, "")}
            for w in DEFAULT_WORKLOADS
        ],
        "duration": {"min": MIN_DURATION_S, "max": MAX_DURATION_S,
                     "default": DEFAULT_DURATION_S, "unit": "seconds"},
        "seed": {"min": MIN_SEED, "max": MAX_SEED, "default": 1},
        "idle_timeouts": list(ALLOWED_IDLE_TIMEOUTS),
        "notes": [
            "Variant C selects its own idle timeout, so the fixed-timeout "
            "control does not apply to it.",
            "A link failure is injected by the 'failure' workload partway "
            "through the run; it is not a separate manual control, because "
            "the runner owns the Mininet session.",
        ],
    }


_WORKLOAD_HELP = {
    "light": "One 1 Mbit/s flow. Path A is genuinely the better choice here.",
    "heavy": "Two saturating TCP flows; more load than path A can carry.",
    "churn": "24 short flows with distinct 5-tuples; stresses the flow table.",
    "failure": "Heavy traffic with a mid-run cut of link s1-s2, then repair.",
}


class ExperimentManager:
    """Owns at most one launched experiment at a time."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._proc: Optional[subprocess.Popen] = None
        self._record: Optional[LaunchedExperiment] = None
        self._history: List[LaunchedExperiment] = []

    # ------------------------------------------------------------- validation
    @staticmethod
    def _validate(variant: str, workload: str, seed: int, duration_s: int,
                  idle_timeout: Optional[int]) -> None:
        if variant not in VARIANT_APPS:
            raise ExperimentError(
                f"Unknown variant {variant!r}. Valid: {sorted(VARIANT_APPS)}"
            )
        if workload not in DEFAULT_WORKLOADS:
            raise ExperimentError(
                f"Unknown workload {workload!r}. Valid: {list(DEFAULT_WORKLOADS)}"
            )
        if not isinstance(seed, int) or not (MIN_SEED <= seed <= MAX_SEED):
            raise ExperimentError(
                f"Seed must be an integer in [{MIN_SEED}, {MAX_SEED}]"
            )
        if not isinstance(duration_s, int) or not (
            MIN_DURATION_S <= duration_s <= MAX_DURATION_S
        ):
            raise ExperimentError(
                f"Duration must be an integer in "
                f"[{MIN_DURATION_S}, {MAX_DURATION_S}] seconds"
            )
        if idle_timeout is not None and idle_timeout not in ALLOWED_IDLE_TIMEOUTS:
            raise ExperimentError(
                f"Idle timeout must be one of {list(ALLOWED_IDLE_TIMEOUTS)}"
            )

    def _reap(self) -> None:
        """Update the record if the child has exited."""
        if self._proc is not None and self._record is not None:
            rc = self._proc.poll()
            if rc is not None and self._record.finished_at is None:
                self._record.finished_at = time.time()
                self._record.returncode = rc
                self._history.append(self._record)
                self._proc = None

    # ------------------------------------------------------------------ status
    def status(self) -> Dict[str, object]:
        with self._lock:
            self._reap()
            launched = self._record.as_dict() if self._record else None
        live = live_status()
        caps = probe_capabilities()

        launcher_payload: Optional[Dict[str, object]] = None
        launcher_error: Optional[str] = None
        launcher_pids: set = set()
        if caps.launcher_available:
            try:
                reply = LAUNCHER.status()
                current = reply.get("current")
                launcher_payload = {
                    "current": current,
                    "history": reply.get("history", []),
                }
                if isinstance(current, dict) and current.get("pid"):
                    launcher_pids.add(int(current["pid"]))
            except (LauncherUnavailable, LauncherError) as exc:
                launcher_error = str(exc)
                launcher_link.invalidate()

        # A process is "external" only if neither we nor the launcher started
        # it. Without this, an experiment the user launched from the browser
        # would be reported as a foreign process and block further use.
        external = [
            p for p in scan_experiment_processes()
            if "run_experiment" in p.cmd
            and not (self._record and p.pid == self._record.pid)
            and p.pid not in launcher_pids
        ]

        launcher_running = bool(
            launcher_payload and launcher_payload.get("current")
        )
        blocked = self._blocked_reason(
            caps, live.active, bool(external), launcher_running
        )
        return {
            "launched_by_dashboard": launched,
            "launcher": launcher_payload,
            "launcher_available": caps.launcher_available,
            "launcher_detail": caps.launcher_detail,
            "launcher_error": launcher_error,
            "start_mechanism": caps.start_mechanism,
            "live_run": live.as_dict(),
            "external_experiment_running": bool(external),
            "external_processes": [
                {"pid": p.pid, "cmd": p.cmd, "elapsed_s": p.elapsed_s}
                for p in external
            ],
            "can_start": (
                caps.can_start_experiments
                and not live.active
                and not external
                and not launcher_running
            ),
            "blocked_reason": blocked,
            "can_inject_link": launcher_running,
            "history": [h.as_dict() for h in self._history[-20:]],
        }

    @staticmethod
    def _blocked_reason(caps, live_active: bool, external: bool,
                        launcher_running: bool = False) -> Optional[str]:
        if not caps.can_start_experiments:
            return caps.reason_cannot_start
        if launcher_running:
            return (
                "An experiment started from this dashboard is still running. "
                "Stop it before starting another."
            )
        if live_active or external:
            return (
                "An experiment is already running. Mininet, Open vSwitch and "
                "TCP 6653 are exclusive resources, so starting another would "
                "corrupt both runs."
            )
        return None

    # ------------------------------------------------------------------- start
    def start(self, variant: str, workload: str, seed: int,
              duration_s: int = DEFAULT_DURATION_S,
              idle_timeout: Optional[int] = None) -> Dict[str, object]:
        self._validate(variant, workload, seed, duration_s, idle_timeout)

        caps = probe_capabilities()
        argv = self.build_command(variant, workload, seed, duration_s, idle_timeout)

        # Preferred path: hand the request to the privileged daemon, which
        # does its own validation and owns the process. The API stays
        # unprivileged.
        if caps.launcher_available:
            return self._start_via_launcher(
                variant, workload, seed, duration_s, idle_timeout
            )

        live = live_status()
        external = [p for p in scan_experiment_processes() if "run_experiment" in p.cmd]
        with self._lock:
            self._reap()
            if self._proc is not None:
                raise ExperimentError(
                    "This dashboard already has an experiment running.", status=409
                )
            if live.active or external:
                raise ExperimentError(
                    "An experiment is already running (possibly started "
                    "outside the dashboard). Refusing to start a second one: "
                    "they would share Mininet and the OpenFlow port and "
                    "corrupt each other. "
                    + (live.detail or ""),
                    status=409,
                )
            if not caps.can_start_experiments:
                raise ExperimentError(
                    (caps.reason_cannot_start or "Cannot start experiments.")
                    + " Equivalent command: sudo " + " ".join(argv),
                    status=503,
                )

            log_dir = os.path.join(PROJECT_ROOT, "results", "dashboard_logs")
            os.makedirs(log_dir, exist_ok=True)
            log_path = os.path.join(
                log_dir,
                f"{time.strftime('%Y%m%d-%H%M%S')}-{variant}-{workload}-seed{seed}.log",
            )
            log_fh = open(log_path, "ab", buffering=0)

            env = dict(os.environ)
            env["PYTHONPATH"] = PROJECT_ROOT + os.pathsep + env.get("PYTHONPATH", "")
            env["PYTHONWARNINGS"] = "ignore::SyntaxWarning"

            proc = subprocess.Popen(
                argv,
                cwd=PROJECT_ROOT,
                env=env,
                stdout=log_fh,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                shell=False,            # argv list; nothing is shell-parsed
                start_new_session=True,  # own process group, so stop() is precise
            )
            self._proc = proc
            self._record = LaunchedExperiment(
                pid=proc.pid, variant=variant, workload=workload, seed=seed,
                duration_s=duration_s, started_at=time.time(),
                log_path=log_path, argv=argv,
            )
            return self._record.as_dict()

    def _start_via_launcher(self, variant: str, workload: str, seed: int,
                            duration_s: int,
                            idle_timeout: Optional[int]) -> Dict[str, object]:
        """Delegate the launch to the privileged daemon."""
        # Variant C picks its own timeout, so passing one would be a silent
        # lie in the UI. The daemon rejects it; drop it here with the same
        # reasoning so the user never sees a confusing refusal.
        effective_timeout = None if variant == "C" else idle_timeout
        try:
            reply = LAUNCHER.start(
                variant=variant, workload=workload, seed=seed,
                duration_s=duration_s, idle_timeout=effective_timeout,
            )
        except LauncherUnavailable as exc:
            launcher_link.invalidate()
            raise ExperimentError(str(exc), status=503)
        except LauncherError as exc:
            # The daemon refused: already running, validation, launch failure.
            status = 409 if "already running" in exc.message else 400
            raise ExperimentError(exc.message, status=status)
        except ProtocolError as exc:
            raise ExperimentError(str(exc), status=400)

        experiment = reply.get("experiment", {})
        if isinstance(experiment, dict):
            experiment = dict(experiment)
            experiment["started_via"] = "launcher"
        return experiment

    @staticmethod
    def build_command(variant: str, workload: str, seed: int, duration_s: int,
                      idle_timeout: Optional[int] = None) -> List[str]:
        """The exact runner invocation. Shown in the UI even when we cannot run it."""
        argv = [
            sys.executable, "-m", "experiments.run_experiment",
            "--variant", variant,
            "--workload", workload,
            "--seed", str(seed),
            "--duration", str(duration_s),
            "--tag", "dashboard",
        ]
        if idle_timeout is not None and variant != "C":
            argv += ["--idle-timeout", str(idle_timeout)]
        return argv

    # -------------------------------------------------------------------- stop
    def stop(self, experiment_id: Optional[str] = None,
             timeout_s: float = 30.0) -> Dict[str, object]:
        """Stop only the experiment this dashboard started.

        SIGTERM first and a generous wait: the runner's teardown stops Mininet,
        flushes telemetry and writes the manifest. SIGKILL would leave a
        half-written run directory and orphan the controller, which is exactly
        the failure mode that corrupted earlier development runs.

        When the launcher owns the experiment, the stop is addressed to it by
        ``experiment_id``. The id is required in that case: stopping "whatever
        is running" from a web button is how the wrong experiment gets killed.
        """
        caps = probe_capabilities()
        if caps.launcher_available:
            return self._stop_via_launcher(experiment_id)

        with self._lock:
            self._reap()
            if self._proc is None or self._record is None:
                raise ExperimentError(
                    "The dashboard has no running experiment to stop. "
                    "Experiments started outside the dashboard are deliberately "
                    "not stoppable from here.",
                    status=409,
                )
            proc, record = self._proc, self._record

        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        except PermissionError as exc:
            raise ExperimentError(f"Not permitted to signal the runner: {exc}",
                                  status=403)

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.3)

        escalated = False
        if proc.poll() is None:
            escalated = True
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

        with self._lock:
            record.stopped_by_user = True
            record.finished_at = time.time()
            record.returncode = proc.returncode
            self._history.append(record)
            self._proc = None
            result = record.as_dict()

        result["escalated_to_sigkill"] = escalated
        result["note"] = (
            "The run directory may be marked incomplete; the analysis excludes "
            "incomplete runs and reports how many it excluded."
            if escalated else
            "The runner was asked to stop and tore down cleanly."
        )
        return result

    def _stop_via_launcher(self, experiment_id: Optional[str]) -> Dict[str, object]:
        try:
            status = LAUNCHER.status()
        except (LauncherUnavailable, LauncherError) as exc:
            launcher_link.invalidate()
            raise ExperimentError(str(exc), status=503)

        current = status.get("current")
        if not isinstance(current, dict):
            raise ExperimentError(
                "No experiment started from this dashboard is running. "
                "Experiments started in a terminal are deliberately not "
                "stoppable from the browser.",
                status=409,
            )

        running_id = str(current.get("experiment_id", ""))
        if experiment_id and experiment_id != running_id:
            raise ExperimentError(
                f"experiment_id {experiment_id!r} is not the running "
                f"experiment ({running_id!r}); refusing to stop it.",
                status=409,
            )

        try:
            reply = LAUNCHER.stop(running_id)
        except LauncherUnavailable as exc:
            launcher_link.invalidate()
            raise ExperimentError(str(exc), status=503)
        except LauncherError as exc:
            raise ExperimentError(exc.message, status=409)
        except ProtocolError as exc:
            raise ExperimentError(str(exc), status=400)

        result = dict(reply.get("experiment", {}))
        result["note"] = (
            "The runner was asked to stop. A run cut short is marked "
            "incomplete in its manifest, and the analysis excludes incomplete "
            "runs and reports how many it excluded."
        )
        return result

    def inject_link(self, experiment_id: str, action: str,
                    link: str) -> Dict[str, object]:
        """Ask the running experiment to bring a link down or up.

        The dashboard never touches Mininet. The launcher appends a command to
        the run's control file and the runner -- which owns the Mininet
        session -- validates the link against the real topology and applies
        it. The result is observable in the flow dumps and the event log like
        any other link event, so nothing here is simulated.
        """
        caps = probe_capabilities()
        if not caps.launcher_available:
            raise ExperimentError(
                "Link injection needs the privileged launcher, which is not "
                "running. " + (caps.reason_cannot_start or ""),
                status=503,
            )
        try:
            reply = LAUNCHER.inject_link(experiment_id, action, link)
        except LauncherUnavailable as exc:
            launcher_link.invalidate()
            raise ExperimentError(str(exc), status=503)
        except LauncherError as exc:
            raise ExperimentError(exc.message, status=409)
        except ProtocolError as exc:
            raise ExperimentError(str(exc), status=400)
        return {
            "queued": reply.get("command"),
            "note": (
                "Queued for the runner. It is applied within about a second "
                "and will appear in the event log and the next flow dump; the "
                "topology view will not show it until the switches confirm."
            ),
        }

    def read_log(self, lines: int = 200) -> Dict[str, object]:
        caps = probe_capabilities()
        if caps.launcher_available:
            launcher_log = self._read_launcher_log(lines)
            if launcher_log is not None:
                return launcher_log

        with self._lock:
            record = self._record
        if record is None:
            return {"available": False, "reason": "no experiment started from the dashboard"}
        try:
            with open(record.log_path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.readlines()
        except OSError as exc:
            return {"available": False, "reason": f"cannot read log: {exc}"}
        # Mininet's tc emits a harmless htb quantum warning on every link.
        filtered = [ln for ln in content if "sch_htb" not in ln]
        return {
            "available": True,
            "path": record.log_path,
            "lines": [ln.rstrip("\n") for ln in filtered[-lines:]],
            "truncated": len(filtered) > lines,
        }


    def _read_launcher_log(self, lines: int) -> Optional[Dict[str, object]]:
        """Tail the log of the launcher's current or most recent experiment."""
        try:
            status = LAUNCHER.status()
        except (LauncherUnavailable, LauncherError):
            launcher_link.invalidate()
            return None

        entry = status.get("current")
        if not isinstance(entry, dict):
            history = status.get("history") or []
            entry = history[-1] if history else None
        if not isinstance(entry, dict):
            return None

        path = entry.get("log_path")
        if not isinstance(path, str) or not os.path.exists(path):
            return {"available": False,
                    "reason": "the launcher has not written a log yet"}
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                content = fh.readlines()
        except OSError as exc:
            return {"available": False, "reason": f"cannot read log: {exc}"}

        # Mininet's tc emits a harmless htb quantum warning on every link.
        filtered = [ln for ln in content if "sch_htb" not in ln]
        return {
            "available": True,
            "path": path,
            "experiment_id": entry.get("experiment_id"),
            "running": entry.get("running"),
            "source": "launcher",
            "lines": [ln.rstrip("\n") for ln in filtered[-lines:]],
            "truncated": len(filtered) > lines,
        }


MANAGER = ExperimentManager()
