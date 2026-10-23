"""Unprivileged client for the launcher daemon.

Used by the FastAPI backend. Every method either returns the daemon's reply or
raises :class:`LauncherUnavailable` / :class:`LauncherError` with a message
written for the person looking at the dashboard, because "something went
wrong" in a control panel that starts real experiments is not good enough.

Connections are short-lived: one request, one reply, close. There is no
persistent state to get out of step with the daemon.
"""

from __future__ import annotations

import os
import socket
from typing import Any, Dict, Optional

from . import protocol
from .protocol import ProtocolError

DEFAULT_TIMEOUT_S = 20.0
#: Starting an experiment makes the daemon wait briefly to catch an immediate
#: failure, and stopping it waits for a cooperative shutdown. Both need more
#: than the default.
START_TIMEOUT_S = 30.0
STOP_TIMEOUT_S = 60.0


class LauncherUnavailable(RuntimeError):
    """The daemon is not running or cannot be reached."""


class LauncherError(RuntimeError):
    """The daemon refused the request; ``message`` is safe to display."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class LauncherClient:
    def __init__(self, socket_path: Optional[str] = None):
        self.socket_path = socket_path or protocol.socket_path()

    # ------------------------------------------------------------- transport
    def _request(self, payload: Dict[str, Any],
                 timeout: float = DEFAULT_TIMEOUT_S) -> Dict[str, Any]:
        if not os.path.exists(self.socket_path):
            raise LauncherUnavailable(
                f"The privileged launcher is not running (no socket at "
                f"{self.socket_path}). Start it with: "
                f"sudo python3 -m dashboard.launcher.daemon"
            )
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(timeout)
                conn.connect(self.socket_path)
                conn.sendall(protocol.encode(payload))
                conn.shutdown(socket.SHUT_WR)

                chunks = []
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if b"\n" in chunk:
                        break
                raw = b"".join(chunks).strip()
        except PermissionError as exc:
            raise LauncherUnavailable(
                "Permission denied connecting to the launcher socket. The "
                "dashboard must run as the user the daemon was started for "
                f"(--allow-uid). Detail: {exc}"
            ) from exc
        except (ConnectionRefusedError, FileNotFoundError) as exc:
            raise LauncherUnavailable(
                "The launcher socket exists but nothing is listening; the "
                "daemon may have crashed. Restart it with: "
                "sudo python3 -m dashboard.launcher.daemon"
            ) from exc
        except socket.timeout as exc:
            raise LauncherUnavailable(
                f"The launcher did not respond within {timeout:.0f}s."
            ) from exc
        except OSError as exc:
            raise LauncherUnavailable(
                f"Cannot reach the launcher: {exc}"
            ) from exc

        if not raw:
            raise LauncherUnavailable("The launcher closed the connection "
                                      "without replying.")
        try:
            reply = protocol.decode(raw)
        except ProtocolError as exc:
            raise LauncherUnavailable(f"Malformed reply from launcher: {exc}")

        if not reply.get("ok"):
            raise LauncherError(str(reply.get("error", "the launcher refused "
                                                       "the request")))
        return reply

    # --------------------------------------------------------------- queries
    def available(self) -> bool:
        try:
            self.ping()
            return True
        except (LauncherUnavailable, LauncherError):
            return False

    def ping(self) -> Dict[str, Any]:
        return self._request({"op": "ping"}, timeout=5.0)

    def status(self) -> Dict[str, Any]:
        return self._request({"op": "status"})

    # --------------------------------------------------------------- actions
    def start(self, variant: str, workload: str, seed: int, duration_s: int,
              idle_timeout: Optional[int] = None) -> Dict[str, Any]:
        # Validate before crossing the privilege boundary as well as after:
        # the user gets a faster, clearer error, and the daemon never sees a
        # request we already know is invalid.
        params = protocol.validate_start({
            "variant": variant, "workload": workload, "seed": seed,
            "duration_s": duration_s, "idle_timeout": idle_timeout,
        })
        return self._request({"op": "start", **params}, timeout=START_TIMEOUT_S)

    def stop(self, experiment_id: str) -> Dict[str, Any]:
        protocol.validate_experiment_id(experiment_id)
        return self._request(
            {"op": "stop", "experiment_id": experiment_id},
            timeout=STOP_TIMEOUT_S,
        )

    def inject_link(self, experiment_id: str, action: str, link: str) -> Dict[str, Any]:
        protocol.validate_inject({
            "experiment_id": experiment_id, "action": action, "link": link,
        })
        return self._request({
            "op": "inject", "experiment_id": experiment_id,
            "action": action, "link": link,
        })
