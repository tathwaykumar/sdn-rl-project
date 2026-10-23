"""Start, wait for and stop the controller as a child process.

The controller runs in its own process rather than inside the experiment
runner.  Two reasons:

* Mininet needs root and the controller does not.  Separate processes keep that
  boundary explicit and let the controller run unprivileged if desired.
* A controller crash then shows up as a dead process and a non-zero exit code
  instead of taking the experiment harness down with it.

Readiness is detected by connecting to the OpenFlow port, not by sleeping.  A
fixed sleep would either waste time or -- worse -- let Mininet start against a
controller that is not listening yet, which produces switches stuck in a
reconnect loop and a run full of zeros.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from typing import Optional


class ControllerProcess:
    def __init__(
        self,
        app_module: str,
        config_path: str,
        log_path: str,
        port: int = 6653,
        listen_host: str = "127.0.0.1",
        project_root: Optional[str] = None,
        verbose: bool = False,
    ):
        self.app_module = app_module
        self.config_path = config_path
        self.log_path = log_path
        self.port = port
        self.listen_host = listen_host
        self.project_root = project_root or os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))
        )
        self.verbose = verbose
        self.proc: Optional[subprocess.Popen] = None
        self._log_fh = None

    # ------------------------------------------------------------------ start
    def port_in_use(self) -> bool:
        try:
            with socket.create_connection((self.listen_host, self.port), timeout=0.5):
                return True
        except OSError:
            return False

    @staticmethod
    def kill_stale_controllers() -> int:
        """Kill leftover launcher processes from previous runs.

        This is not housekeeping, it is correctness.  A controller orphaned by
        an interrupted run keeps the OpenFlow port bound; the next run's
        controller then fails to bind, its listener thread dies, and Mininet
        happily connects to the *old* controller instead.  Everything looks
        healthy and the entire run measures stale code.  That happened during
        development and silently invalidated several runs, so every run now
        starts by clearing the ground.

        The pattern is specific to this project's launcher module, so it cannot
        match unrelated user processes.
        """
        killed = 0
        pattern = "controller.launcher"
        try:
            out = subprocess.run(["ps", "-eo", "pid,cmd"], capture_output=True, text=True)
            me = os.getpid()
            for line in out.stdout.splitlines():
                if pattern not in line or "grep" in line:
                    continue
                parts = line.split(None, 1)
                if len(parts) != 2:
                    continue
                try:
                    pid = int(parts[0])
                except ValueError:
                    continue
                if pid == me:
                    continue
                try:
                    os.kill(pid, signal.SIGKILL)
                    killed += 1
                except (ProcessLookupError, PermissionError):
                    pass
        except Exception:
            pass
        if killed:
            time.sleep(1.0)  # let the port be released
        return killed

    def start(self) -> None:
        stale = self.kill_stale_controllers()
        if stale:
            print(f"  (cleared {stale} stale controller process(es))")
        if self.port_in_use():
            raise RuntimeError(
                f"port {self.port} is already in use by something that is not ours; "
                "refusing to start, because Mininet would connect to that process "
                "instead and the run would silently measure the wrong controller"
            )

        env = dict(os.environ)
        env["SDNRL_CONFIG"] = self.config_path
        env["PYTHONPATH"] = self.project_root + os.pathsep + env.get("PYTHONPATH", "")
        # os-ken 4.1.1 emits a SyntaxWarning from vrrp.py on Python 3.14
        # ('break' in a 'finally' block). It is harmless and unrelated to us,
        # but it would be the first thing in every controller log.
        env["PYTHONWARNINGS"] = "ignore::SyntaxWarning"

        os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)
        self._log_fh = open(self.log_path, "a", buffering=1, encoding="utf-8")

        cmd = [
            sys.executable,
            "-m",
            "controller.launcher",
            "--app",
            self.app_module,
            "--port",
            str(self.port),
            "--listen-host",
            self.listen_host,
        ]
        if self.verbose:
            cmd.append("--verbose")

        self.proc = subprocess.Popen(
            cmd,
            cwd=self.project_root,
            env=env,
            stdout=self._log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # own process group, so stop() is precise
        )

    def wait_ready(self, timeout: float = 20.0) -> bool:
        """Return True once the OpenFlow port accepts connections."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc is not None and self.proc.poll() is not None:
                return False  # died during startup
            try:
                with socket.create_connection((self.listen_host, self.port), timeout=0.5):
                    return True
            except OSError:
                time.sleep(0.2)
        return False

    # ------------------------------------------------------------------- stop
    def stop(self, timeout: float = 15.0) -> Optional[int]:
        """SIGTERM, wait for a clean shutdown, escalate only if needed.

        The clean path matters: the controller flushes telemetry and saves the
        Q-table in its signal handler.  SIGKILL would lose both.
        """
        if self.proc is None:
            return None
        if self.proc.poll() is not None:
            return self.proc.returncode

        try:
            self.proc.send_signal(signal.SIGTERM)
        except ProcessLookupError:
            return self.proc.returncode

        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                break
            time.sleep(0.2)
        else:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            self.proc.wait(timeout=5)

        if self._log_fh is not None:
            self._log_fh.flush()
            self._log_fh.close()
            self._log_fh = None
        return self.proc.returncode

    def is_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def __enter__(self) -> "ControllerProcess":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()
