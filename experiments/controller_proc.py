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
    def start(self) -> None:
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
