"""Wire protocol and validation shared by the privileged daemon and its client.

Design constraints this module exists to enforce:

* **The daemon runs as root; the web API does not.** Everything crossing that
  boundary is defined here, and it is deliberately tiny: five operations, each
  with a fixed set of enumerated or numeric fields.
* **No paths, no commands, no filenames cross the boundary.** The client sends
  a variant letter, a workload name and three integers. The daemon builds the
  command line itself from its own constants. There is nothing a caller can
  say that becomes part of a shell command, because there is no shell.
* **Validation happens on the daemon side too.** The API validates as well,
  but a privileged service that trusts its caller's validation is a privileged
  service with no validation. Both sides call the same ``validate_start``.

Transport is newline-delimited JSON over a Unix domain socket. One request and
one response per connection, so the daemon holds no per-connection state.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, Optional, Tuple

#: Where the daemon listens. Under /run so it is tmpfs-backed and disappears on
#: reboot; the daemon creates the directory with restrictive permissions.
DEFAULT_SOCKET_DIR = "/run/sdn-rl"
DEFAULT_SOCKET_PATH = os.path.join(DEFAULT_SOCKET_DIR, "launcher.sock")

#: Override for development or for systems without a writable /run.
SOCKET_ENV_VAR = "SDNRL_LAUNCHER_SOCKET"

PROTOCOL_VERSION = 1

#: Largest request we will read. A request is a handful of short fields; this
#: is far above that and bounds the memory a client can make us allocate.
MAX_REQUEST_BYTES = 8192

# --------------------------------------------------------------------------
# Allowed values. These are duplicated from the runner deliberately: the
# daemon must not import the whole experiment stack just to know what a valid
# variant is, and a narrower, independently-stated allowlist is the point.
# tests/test_launcher.py asserts these stay in step with the runner.
# --------------------------------------------------------------------------
ALLOWED_VARIANTS = ("A", "B", "C")
ALLOWED_WORKLOADS = ("light", "heavy", "churn", "failure")
ALLOWED_IDLE_TIMEOUTS = (5, 15, 60)

MIN_SEED, MAX_SEED = 1, 10_000
MIN_DURATION_S, MAX_DURATION_S = 10, 600

#: Link actions the UI may inject into a running experiment.
ALLOWED_LINK_ACTIONS = ("link_down", "link_up")

#: Experiment ids the daemon mints and the client quotes back. Constrained so
#: an id can never contain path separators or shell metacharacters.
EXPERIMENT_ID_RE = re.compile(r"^exp-[0-9]{8}-[0-9]{6}-[0-9a-f]{8}$")

#: Link identifiers are two switch names joined by a hyphen, nothing else.
LINK_ID_RE = re.compile(r"^s[1-9][0-9]?-s[1-9][0-9]?$")

OPS = ("ping", "status", "start", "stop", "inject")


class ProtocolError(ValueError):
    """Malformed or disallowed request. Always safe to show to the user."""


def socket_path() -> str:
    return os.environ.get(SOCKET_ENV_VAR) or DEFAULT_SOCKET_PATH


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _require_int(payload: Dict[str, Any], key: str, low: int, high: int) -> int:
    value = payload.get(key)
    # bool is a subclass of int; accepting it would let `true` become 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise ProtocolError(f"{key} must be an integer")
    if not (low <= value <= high):
        raise ProtocolError(f"{key} must be between {low} and {high}")
    return value


def validate_start(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a start request, returning a normalised parameter dict.

    Raises ProtocolError with a message intended for display. Called by both
    the API and the daemon.
    """
    variant = payload.get("variant")
    if variant not in ALLOWED_VARIANTS:
        raise ProtocolError(
            f"variant must be one of {list(ALLOWED_VARIANTS)}"
        )

    workload = payload.get("workload")
    if workload not in ALLOWED_WORKLOADS:
        raise ProtocolError(
            f"workload must be one of {list(ALLOWED_WORKLOADS)}"
        )

    seed = _require_int(payload, "seed", MIN_SEED, MAX_SEED)
    duration_s = _require_int(payload, "duration_s", MIN_DURATION_S, MAX_DURATION_S)

    idle_timeout = payload.get("idle_timeout")
    if idle_timeout is not None:
        if isinstance(idle_timeout, bool) or not isinstance(idle_timeout, int):
            raise ProtocolError("idle_timeout must be an integer or null")
        if idle_timeout not in ALLOWED_IDLE_TIMEOUTS:
            raise ProtocolError(
                f"idle_timeout must be one of {list(ALLOWED_IDLE_TIMEOUTS)} or null"
            )
        if variant == "C":
            # Variant C selects its own timeout. Silently ignoring the field
            # would mean the UI could show a setting that has no effect.
            raise ProtocolError(
                "variant C selects its own idle timeout; do not pass one"
            )

    return {
        "variant": variant,
        "workload": workload,
        "seed": seed,
        "duration_s": duration_s,
        "idle_timeout": idle_timeout,
    }


def validate_experiment_id(value: Any) -> str:
    if not isinstance(value, str) or not EXPERIMENT_ID_RE.match(value):
        raise ProtocolError("experiment_id is malformed")
    return value


def validate_inject(payload: Dict[str, Any]) -> Tuple[str, str, str]:
    """Validate a link-injection request -> (experiment_id, action, link)."""
    experiment_id = validate_experiment_id(payload.get("experiment_id"))

    action = payload.get("action")
    if action not in ALLOWED_LINK_ACTIONS:
        raise ProtocolError(
            f"action must be one of {list(ALLOWED_LINK_ACTIONS)}"
        )

    link = payload.get("link")
    if not isinstance(link, str) or not LINK_ID_RE.match(link):
        raise ProtocolError("link must look like 's1-s2'")
    return experiment_id, action, link


def validate_request(raw: Dict[str, Any]) -> Dict[str, Any]:
    """Shape-check any request before it is dispatched."""
    if not isinstance(raw, dict):
        raise ProtocolError("request must be a JSON object")
    op = raw.get("op")
    if op not in OPS:
        raise ProtocolError(f"op must be one of {list(OPS)}")
    return raw


# --------------------------------------------------------------------------
# Framing
# --------------------------------------------------------------------------

def encode(message: Dict[str, Any]) -> bytes:
    return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")


def decode(data: bytes) -> Dict[str, Any]:
    if len(data) > MAX_REQUEST_BYTES:
        raise ProtocolError("request too large")
    try:
        parsed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"malformed JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ProtocolError("request must be a JSON object")
    return parsed


def ok(**fields: Any) -> Dict[str, Any]:
    return {"ok": True, "version": PROTOCOL_VERSION, **fields}


def error(message: str, **fields: Any) -> Dict[str, Any]:
    return {"ok": False, "version": PROTOCOL_VERSION, "error": message, **fields}
