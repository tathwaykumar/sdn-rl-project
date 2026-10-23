"""The API's view of the privileged launcher daemon.

Kept in its own module so ``config.probe_capabilities`` can report launcher
availability without importing the experiment manager, and so the reachability
check is cached: ``probe_capabilities`` is called on nearly every request, and
connecting to a Unix socket on each one would be wasteful and would make a
dead daemon slow down the whole UI rather than simply showing as unavailable.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Dict, Optional

from dashboard.launcher.client import (
    LauncherClient, LauncherError, LauncherUnavailable,
)
from dashboard.launcher.protocol import socket_path

#: Availability is re-probed at most this often. Short enough that starting
#: the daemon is reflected in the UI promptly, long enough that it is not a
#: per-request cost.
_CACHE_TTL_S = 5.0

_LOCK = threading.Lock()
_CACHE: Dict[str, object] = {"checked_at": 0.0, "available": False, "detail": ""}

CLIENT = LauncherClient()


def launcher_state(force: bool = False) -> Dict[str, object]:
    """Is the daemon reachable? Cached; ``force`` bypasses the cache."""
    now = time.time()
    with _LOCK:
        fresh = (now - float(_CACHE["checked_at"])) < _CACHE_TTL_S
        if fresh and not force:
            return {
                "available": bool(_CACHE["available"]),
                "detail": str(_CACHE["detail"]),
                "socket_path": CLIENT.socket_path,
            }

    available = False
    if not os.path.exists(CLIENT.socket_path):
        detail = f"no socket at {CLIENT.socket_path}"
    else:
        try:
            CLIENT.ping()
            available = True
            detail = "launcher daemon reachable"
        except LauncherUnavailable as exc:
            detail = str(exc).splitlines()[0]
        except LauncherError as exc:
            detail = exc.message

    with _LOCK:
        _CACHE["checked_at"] = now
        _CACHE["available"] = available
        _CACHE["detail"] = detail

    return {"available": available, "detail": detail,
            "socket_path": CLIENT.socket_path}


def invalidate() -> None:
    """Drop the cached result, so the next check really probes.

    Called after an operation that should change launcher state, so the UI is
    not told "available" by a stale cache entry immediately after a failure.
    """
    with _LOCK:
        _CACHE["checked_at"] = 0.0
