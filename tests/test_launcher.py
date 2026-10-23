"""Tests for the privileged launcher: protocol, daemon logic, and lifecycle.

The launcher is the one component that runs as root, so these tests focus on
the boundary: what it accepts, what it refuses, and whether it can be made to
act on anything the caller names.

The lifecycle tests run a *real* daemon over a *real* Unix socket in a temp
directory, with the command it launches swapped for a harmless one. That
exercises the actual transport, peer check, process-group handling and stop
path without needing root or Mininet, so these run in the normal suite.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import warnings

import pytest

warnings.filterwarnings("ignore", category=SyntaxWarning)

from dashboard.launcher import daemon as daemon_mod  # noqa: E402
from dashboard.launcher import protocol  # noqa: E402
from dashboard.launcher.client import (  # noqa: E402
    LauncherClient, LauncherError, LauncherUnavailable,
)
from dashboard.launcher.protocol import ProtocolError  # noqa: E402


# ------------------------------------------------------------------ protocol
class TestValidation:
    def test_accepts_a_well_formed_request(self):
        params = protocol.validate_start({
            "variant": "B", "workload": "heavy", "seed": 3,
            "duration_s": 90, "idle_timeout": 15,
        })
        assert params["variant"] == "B"
        assert params["idle_timeout"] == 15

    @pytest.mark.parametrize("variant", ["D", "a", "", None, 1, "A; rm -rf /"])
    def test_rejects_unknown_variant(self, variant):
        with pytest.raises(ProtocolError, match="variant"):
            protocol.validate_start({
                "variant": variant, "workload": "heavy", "seed": 1,
                "duration_s": 60,
            })

    @pytest.mark.parametrize("workload", ["bogus", "", None, "heavy&&id"])
    def test_rejects_unknown_workload(self, workload):
        with pytest.raises(ProtocolError, match="workload"):
            protocol.validate_start({
                "variant": "A", "workload": workload, "seed": 1,
                "duration_s": 60,
            })

    @pytest.mark.parametrize("seed", [0, -1, 10_001, "1", 1.5, None, True])
    def test_rejects_out_of_range_or_non_integer_seed(self, seed):
        with pytest.raises(ProtocolError, match="seed"):
            protocol.validate_start({
                "variant": "A", "workload": "heavy", "seed": seed,
                "duration_s": 60,
            })

    @pytest.mark.parametrize("duration", [0, 9, 601, "60", None, False])
    def test_rejects_out_of_range_duration(self, duration):
        with pytest.raises(ProtocolError, match="duration_s"):
            protocol.validate_start({
                "variant": "A", "workload": "heavy", "seed": 1,
                "duration_s": duration,
            })

    def test_rejects_unlisted_idle_timeout(self):
        with pytest.raises(ProtocolError, match="idle_timeout"):
            protocol.validate_start({
                "variant": "A", "workload": "heavy", "seed": 1,
                "duration_s": 60, "idle_timeout": 7,
            })

    def test_rejects_idle_timeout_for_variant_c(self):
        """C chooses its own timeout; accepting one would be a silent lie."""
        with pytest.raises(ProtocolError, match="variant C"):
            protocol.validate_start({
                "variant": "C", "workload": "heavy", "seed": 1,
                "duration_s": 60, "idle_timeout": 15,
            })

    def test_bool_is_not_accepted_as_an_integer(self):
        """True == 1 in Python; a bool must not slip through as a seed."""
        with pytest.raises(ProtocolError):
            protocol.validate_start({
                "variant": "A", "workload": "heavy", "seed": True,
                "duration_s": 60,
            })

    @pytest.mark.parametrize("bad", [
        "", "exp-bad", "../../etc/passwd", "exp-20261023-125758-ZZZZZZZZ",
        "exp-20261023-125758-c3da5252; id", None, 42,
    ])
    def test_rejects_malformed_experiment_id(self, bad):
        with pytest.raises(ProtocolError, match="experiment_id"):
            protocol.validate_experiment_id(bad)

    def test_accepts_a_well_formed_experiment_id(self):
        value = "exp-20261023-125758-c3da5252"
        assert protocol.validate_experiment_id(value) == value

    @pytest.mark.parametrize("link", [
        "s1", "s1-h2", "../s1-s2", "s1-s2; reboot", "", None, "sA-sB",
    ])
    def test_rejects_malformed_link(self, link):
        with pytest.raises(ProtocolError, match="link"):
            protocol.validate_inject({
                "experiment_id": "exp-20261023-125758-c3da5252",
                "action": "link_down", "link": link,
            })

    @pytest.mark.parametrize("action", ["down", "reboot", "", None])
    def test_rejects_unknown_link_action(self, action):
        with pytest.raises(ProtocolError, match="action"):
            protocol.validate_inject({
                "experiment_id": "exp-20261023-125758-c3da5252",
                "action": action, "link": "s1-s2",
            })

    def test_rejects_unknown_op(self):
        with pytest.raises(ProtocolError, match="op"):
            protocol.validate_request({"op": "exec"})

    def test_rejects_oversized_payload(self):
        with pytest.raises(ProtocolError, match="too large"):
            protocol.decode(b"x" * (protocol.MAX_REQUEST_BYTES + 1))

    def test_rejects_non_object_request(self):
        with pytest.raises(ProtocolError):
            protocol.decode(b"[1,2,3]")


def test_allowlists_match_the_runner():
    """The daemon's independent allowlist must not drift from the runner."""
    from experiments.run_experiment import DEFAULT_WORKLOADS, VARIANT_APPS
    from agent.actions import TIMEOUT_CLASSES_S
    assert set(protocol.ALLOWED_VARIANTS) == set(VARIANT_APPS)
    assert set(protocol.ALLOWED_WORKLOADS) == set(DEFAULT_WORKLOADS)
    assert set(protocol.ALLOWED_IDLE_TIMEOUTS) == set(TIMEOUT_CLASSES_S)


def test_built_command_contains_no_caller_controlled_strings():
    """Everything in the argv comes from constants or validated integers."""
    argv = daemon_mod._build_argv({
        "variant": "C", "workload": "churn", "seed": 7,
        "duration_s": 120, "idle_timeout": None,
    })
    assert argv[1:3] == ["-m", "experiments.run_experiment"]
    assert "--idle-timeout" not in argv  # variant C never gets one
    for part in argv:
        assert not any(ch in part for ch in ";|&$`><\n")


def test_built_command_includes_idle_timeout_for_a_and_b():
    argv = daemon_mod._build_argv({
        "variant": "B", "workload": "light", "seed": 1,
        "duration_s": 30, "idle_timeout": 60,
    })
    assert "--idle-timeout" in argv and "60" in argv


# -------------------------------------------------------------------- daemon
@pytest.fixture()
def harmless_daemon(tmp_path, monkeypatch):
    """A real daemon on a temp socket that launches `sleep` instead of Mininet.

    This keeps the transport, peer-credential check, process-group handling
    and stop path under test without needing root or touching the network.
    """
    import sys

    def fake_argv(params):
        # Long enough that the test controls when it ends.
        return [sys.executable, "-c", "import time; time.sleep(120)"]

    monkeypatch.setattr(daemon_mod, "_build_argv", fake_argv)
    monkeypatch.setattr(daemon_mod, "_external_experiment", lambda: None)
    monkeypatch.setattr(daemon_mod, "LOG_DIR", str(tmp_path / "logs"))

    sock = str(tmp_path / "launcher.sock")
    d = daemon_mod.LauncherDaemon(
        allow_uid=os.getuid(), socket_path=sock, allow_gid=None
    )
    thread = threading.Thread(target=d.serve_forever, daemon=True)
    thread.start()

    deadline = time.time() + 10
    while not os.path.exists(sock) and time.time() < deadline:
        time.sleep(0.05)
    assert os.path.exists(sock), "daemon did not create its socket"

    yield d, LauncherClient(socket_path=sock)

    # Deterministic teardown: ask the daemon to stop, unblock accept(), then
    # wait for the serving thread to finish its own cleanup before the next
    # test builds another daemon. Tearing down from two threads at once used
    # to make unrelated tests fail intermittently.
    d._stopping.set()
    try:
        if d._server is not None:
            d._server.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    thread.join(timeout=20)
    try:
        d._cleanup()  # idempotent; a no-op if the thread already did it
    except Exception:
        pass


class TestDaemonLifecycle:
    def test_ping_and_empty_status(self, harmless_daemon):
        _, client = harmless_daemon
        assert client.ping()["pong"] is True
        assert client.status()["current"] is None

    def test_start_reports_a_running_experiment(self, harmless_daemon):
        _, client = harmless_daemon
        reply = client.start("A", "light", seed=1, duration_s=30)
        experiment = reply["experiment"]
        assert protocol.EXPERIMENT_ID_RE.match(experiment["experiment_id"])
        assert experiment["running"] is True
        assert experiment["pid"] > 0

        current = client.status()["current"]
        assert current["experiment_id"] == experiment["experiment_id"]

    def test_duplicate_launch_is_refused(self, harmless_daemon):
        _, client = harmless_daemon
        client.start("A", "light", seed=1, duration_s=30)
        with pytest.raises(LauncherError, match="already running"):
            client.start("B", "heavy", seed=2, duration_s=30)

    def test_stop_ends_the_process_and_clears_state(self, harmless_daemon):
        _, client = harmless_daemon
        experiment = client.start("A", "light", seed=1, duration_s=30)["experiment"]
        pid = experiment["pid"]

        result = client.stop(experiment["experiment_id"])["experiment"]
        assert result["experiment_id"] == experiment["experiment_id"]
        assert client.status()["current"] is None

        # The child must actually be gone, not merely forgotten.
        deadline = time.time() + 5
        while time.time() < deadline:
            if not os.path.exists(f"/proc/{pid}"):
                break
            time.sleep(0.1)
        assert not os.path.exists(f"/proc/{pid}"), "process survived stop"

    def test_stop_refuses_a_mismatched_id(self, harmless_daemon):
        """Stop is addressed by id so the wrong experiment cannot be killed."""
        _, client = harmless_daemon
        client.start("A", "light", seed=1, duration_s=30)
        with pytest.raises(LauncherError, match="does not match"):
            client.stop("exp-20200101-000000-deadbeef")

    def test_stop_with_nothing_running_is_refused(self, harmless_daemon):
        _, client = harmless_daemon
        with pytest.raises(LauncherError, match="no experiment"):
            client.stop("exp-20200101-000000-deadbeef")

    def test_start_after_stop_is_allowed(self, harmless_daemon):
        _, client = harmless_daemon
        first = client.start("A", "light", seed=1, duration_s=30)["experiment"]
        client.stop(first["experiment_id"])
        second = client.start("B", "heavy", seed=2, duration_s=30)["experiment"]
        assert second["experiment_id"] != first["experiment_id"]

    def test_invalid_start_is_refused_at_the_daemon(self, harmless_daemon):
        """The daemon validates even though the client already did."""
        _, client = harmless_daemon
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(10)
            conn.connect(client.socket_path)
            conn.sendall(protocol.encode({
                "op": "start", "variant": "ZZZ", "workload": "heavy",
                "seed": 1, "duration_s": 60,
            }))
            conn.shutdown(socket.SHUT_WR)
            reply = json.loads(conn.recv(65536).decode())
        assert reply["ok"] is False
        assert "variant" in reply["error"]

    def test_unknown_op_is_refused(self, harmless_daemon):
        _, client = harmless_daemon
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(10)
            conn.connect(client.socket_path)
            conn.sendall(protocol.encode({"op": "exec", "cmd": "id"}))
            conn.shutdown(socket.SHUT_WR)
            reply = json.loads(conn.recv(65536).decode())
        assert reply["ok"] is False

    def test_malformed_json_is_refused_without_crashing(self, harmless_daemon):
        _, client = harmless_daemon
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
            conn.settimeout(10)
            conn.connect(client.socket_path)
            conn.sendall(b"{not json\n")
            conn.shutdown(socket.SHUT_WR)
            reply = json.loads(conn.recv(65536).decode())
        assert reply["ok"] is False
        # The daemon must still be serving.
        assert client.ping()["pong"] is True

    def test_inject_refused_when_nothing_is_running(self, harmless_daemon):
        _, client = harmless_daemon
        with pytest.raises(LauncherError, match="not running"):
            client.inject_link("exp-20200101-000000-deadbeef",
                               "link_down", "s1-s2")

    def test_daemon_shutdown_stops_a_running_experiment(self, harmless_daemon):
        """An orphaned runner would hold Mininet and TCP 6653 forever."""
        d, client = harmless_daemon
        experiment = client.start("A", "light", seed=1, duration_s=30)["experiment"]
        pid = experiment["pid"]
        d._stopping.set()
        d._cleanup()
        deadline = time.time() + 6
        while time.time() < deadline:
            if not os.path.exists(f"/proc/{pid}"):
                break
            time.sleep(0.1)
        assert not os.path.exists(f"/proc/{pid}")


class TestClientErrors:
    def test_missing_socket_reports_how_to_start_the_daemon(self, tmp_path):
        client = LauncherClient(socket_path=str(tmp_path / "absent.sock"))
        assert client.available() is False
        with pytest.raises(LauncherUnavailable, match="dashboard.launcher.daemon"):
            client.ping()

    def test_stale_socket_file_is_reported_clearly(self, tmp_path):
        stale = tmp_path / "stale.sock"
        stale.write_text("")  # a file, not a socket
        client = LauncherClient(socket_path=str(stale))
        with pytest.raises(LauncherUnavailable):
            client.ping()

    def test_client_validates_before_crossing_the_boundary(self, tmp_path):
        """Bad input must not even reach the privileged process."""
        client = LauncherClient(socket_path=str(tmp_path / "absent.sock"))
        with pytest.raises(ProtocolError):
            client.start("Z", "heavy", seed=1, duration_s=60)
        with pytest.raises(ProtocolError):
            client.stop("not-an-id")
