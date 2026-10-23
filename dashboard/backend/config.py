"""Dashboard configuration and the data-source contract.

The single most important design decision in this backend:

    **The dashboard never queries the switches itself while an experiment is
    running.** It reads the telemetry the controller and runner already write.

The controller flushes ``decisions.csv``, ``snapshots.csv`` and ``events.csv``
after every row, the runner dumps every switch's flow table with ``ovs-ofctl``
every 5 seconds, and ``ping`` is line-buffered into ``ping_h1.txt``. All of that
is on disk, live, already. Reading it costs the experiment nothing.

Querying OpenFlow directly from the dashboard would have been easier and would
have been wrong: this project *measures controller message load* as a result,
and a dashboard polling for statistics would inflate exactly that metric. The
project's own definition separates reactive controller messages (PacketIn +
FlowMod) from monitoring traffic, and the dashboard must not blur that line.
Reading files adds zero OpenFlow messages, so A/B/C comparisons are identical
whether or not anyone is watching.

Consequence to be honest about: flow-table views are as fresh as the 5-second
dump cadence, not instantaneous. Every response carries the age of its data.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

PROJECT_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)
RUNS_DIR = os.path.join(PROJECT_ROOT, "results", "runs")
ANALYSIS_OUT = os.path.join(PROJECT_ROOT, "analysis", "out")

#: How often the backend re-reads the active run's files, in seconds.
#: 1.0 matches the controller's own polling interval: reading faster would
#: produce duplicate rows, not fresher data.
LIVE_POLL_INTERVAL_S = 1.0

#: The runner dumps flow tables on this cadence. Flow-table views can never be
#: fresher than this, and the UI says so rather than implying real-time.
FLOW_DUMP_INTERVAL_S = 5.0

#: Beyond this age, live data is marked stale in the UI instead of being shown
#: as if it were current.
STALE_AFTER_S = 12.0

API_HOST = os.environ.get("SDNRL_API_HOST", "127.0.0.1")
API_PORT = int(os.environ.get("SDNRL_API_PORT", "8000"))

#: Dev-server origins allowed by CORS. Loopback only: this API can start
#: experiments when privileged, so it must not be reachable from the network.
ALLOWED_ORIGINS: List[str] = [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:4173", "http://127.0.0.1:4173",
]


class DataSource(str, Enum):
    """Where a payload came from. Sent with every response that carries data.

    The UI renders these differently and never shows MOCK as if it were LIVE.
    """

    LIVE = "live"                # read from a currently-running experiment
    HISTORICAL = "historical"    # read from a completed run directory
    CONFIG = "config"            # static project configuration (topo.spec)
    MOCK = "mock"                # synthetic; demo mode only, always labelled
    UNAVAILABLE = "unavailable"  # genuinely not measurable right now


class Mode(str, Enum):
    LIVE = "live"
    MOCK = "mock"


def current_mode() -> Mode:
    """Mock mode is opt-in via SDNRL_DASHBOARD_MOCK=1 and always visible in UI."""
    return Mode.MOCK if os.environ.get("SDNRL_DASHBOARD_MOCK") == "1" else Mode.LIVE


@dataclass
class Capabilities:
    """What this backend process can actually do, determined at runtime.

    Reported to the UI so it can disable controls rather than offering buttons
    that fail. Nothing here is assumed; each field is probed.
    """

    mode: str = Mode.LIVE.value
    can_read_runs: bool = False
    can_start_experiments: bool = False
    can_query_switches_directly: bool = False
    is_root: bool = False
    reason_cannot_start: Optional[str] = None
    reason_cannot_query: Optional[str] = None
    project_root: str = PROJECT_ROOT
    runs_dir: str = RUNS_DIR
    notes: List[str] = field(default_factory=list)


def probe_capabilities() -> Capabilities:
    caps = Capabilities(mode=current_mode().value)
    caps.is_root = (os.geteuid() == 0)
    caps.can_read_runs = os.path.isdir(RUNS_DIR) and os.access(RUNS_DIR, os.R_OK)

    # Starting an experiment means starting Mininet, which requires root.
    # The API deliberately does not acquire privilege for itself: a web service
    # that can become root is a much larger attack surface than this project
    # needs. When unprivileged, the UI shows the exact command to run instead.
    if caps.is_root:
        caps.can_start_experiments = True
    else:
        caps.can_start_experiments = False
        caps.reason_cannot_start = (
            "The API is running unprivileged. Mininet requires root, so "
            "experiments cannot be launched from the browser. Start the API "
            "as root to enable this, or run the printed command in a terminal."
        )

    # Direct ovs-ofctl queries need root too, and would add OpenFlow messages
    # the project counts as a measured result. Disabled unless root AND no
    # experiment is in flight; see flows.py.
    if caps.is_root:
        caps.can_query_switches_directly = True
    else:
        caps.reason_cannot_query = (
            "ovs-ofctl requires root. Flow tables are read from the run's "
            "5-second flow dumps instead, which adds no OpenFlow traffic."
        )

    caps.notes.append(
        "Live telemetry is read from the active run's files; the dashboard "
        "issues no OpenFlow requests of its own, so it does not affect the "
        "controller-message metric."
    )
    caps.notes.append(
        f"Flow tables refresh at most every {FLOW_DUMP_INTERVAL_S:.0f}s "
        "(the runner's dump cadence)."
    )
    return caps
