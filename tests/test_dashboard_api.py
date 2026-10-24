"""Tests for the dashboard backend.

Grouped around the guarantees the dashboard makes:

* it reports the *real* topology, not a second definition that could drift;
* it never presents a missing measurement as a number;
* it never presents mock data as a measurement;
* it refuses to start an experiment while one is running, and refuses to stop
  one it did not start;
* its comparison arithmetic gets the direction of "better" right.

These tests use a temporary runs directory and never touch real experiment
output, so they are safe to run while an experiment is in flight.
"""

from __future__ import annotations

import json
import os
import warnings

import pytest

warnings.filterwarnings("ignore", category=SyntaxWarning)

from fastapi.testclient import TestClient  # noqa: E402

from dashboard.backend import telemetry  # noqa: E402
from dashboard.backend.app import app  # noqa: E402
from dashboard.backend.comparison import METRIC_INFO, _delta  # noqa: E402
from dashboard.backend.experiments import (  # noqa: E402
    ExperimentError, ExperimentManager,
)
from dashboard.backend.runs import RunRef, live_status  # noqa: E402
from dashboard.backend.topology import link_id, static_topology  # noqa: E402
from topo import spec  # noqa: E402


@pytest.fixture()
def client():
    return TestClient(app)


# --------------------------------------------------------------- API shape
def test_health_reports_mode_and_activity(client):
    body = client.get("/api/health").json()
    assert body["ok"] is True
    assert body["mode"] in ("live", "mock")
    assert isinstance(body["experiment_active"], bool)
    assert isinstance(body["matrix_running"], bool)


def test_capabilities_explains_what_it_cannot_do(client):
    body = client.get("/api/capabilities").json()
    assert set(["can_start_experiments", "can_read_runs", "is_root"]) <= set(body)
    # Unprivileged is the expected and recommended posture; when it cannot
    # start experiments it must say why rather than just disabling a button.
    if not body["can_start_experiments"]:
        assert body["reason_cannot_start"]


def test_every_data_response_carries_a_source_header(client):
    for path in ("/api/health", "/api/topology", "/api/runs"):
        response = client.get(path)
        assert response.headers["X-SDNRL-Data-Source"] in ("live", "mock")


# --------------------------------------------------------------- topology
def test_topology_matches_the_real_project_spec(client):
    body = client.get("/api/topology").json()
    switches = {n["id"] for n in body["nodes"] if n["kind"] == "switch"}
    hosts = {n["id"] for n in body["nodes"] if n["kind"] == "host"}
    assert switches == set(spec.SWITCHES)
    assert hosts == {h.name for h in spec.HOSTS}
    assert body["source"] == "config"


def test_topology_links_match_spec_ports(client):
    body = client.get("/api/topology").json()
    core = {l["id"]: l for l in body["links"] if l["kind"] == "core"}
    assert len(core) == len(spec.CORE_LINKS)
    for link in spec.CORE_LINKS:
        entry = core[link_id(link.a, link.b)]
        ports = {entry["source_port"], entry["target_port"]}
        assert ports == {link.a_port, link.b_port}
        assert entry["bandwidth_mbit"] == link.bw_mbit


def test_topology_exposes_two_link_disjoint_paths(client):
    body = client.get("/api/topology").json()
    paths = body["paths"]
    assert set(paths) == {"A", "B"}
    assert set(paths["A"]["links"]).isdisjoint(set(paths["B"]["links"]))


def test_topology_state_without_a_run_is_unavailable_not_empty():
    """Absent live state must be reported, not rendered as an idle network."""
    from dashboard.backend.topology import topology_state
    payload = topology_state(None, False)
    assert payload["live"]["available"] is False
    assert payload["live"]["reason"]


# ----------------------------------------------------------- flow parsing
DUMP = """\
# t=1792742100.0
===== s1 =====
OFPST_FLOW reply (OF1.3) (xid=0x2):
 cookie=0x5d4e0001, duration=9.0s, table=0, n_packets=18, n_bytes=1710, idle_timeout=15, hard_timeout=120, send_flow_rem priority=10,tcp,nw_src=10.0.0.1,nw_dst=10.0.0.3,tp_src=1,tp_dst=5201 actions=output:3
 cookie=0x5d4e0001, duration=2.0s, table=0, n_packets=4, n_bytes=400, idle_timeout=5, hard_timeout=120, send_flow_rem priority=10,tcp,nw_src=10.0.0.2,nw_dst=10.0.0.4,tp_src=2,tp_dst=5202 actions=output:4
 cookie=0x0, duration=20.0s, table=0, n_packets=22, n_bytes=1848, priority=1,ipv6 actions=drop
 cookie=0x0, duration=20.0s, table=0, n_packets=10, n_bytes=676, priority=0 actions=CONTROLLER:65535
"""


@pytest.fixture()
def raw_dir(tmp_path):
    raw = tmp_path / "raw"
    (raw / "flow_dumps").mkdir(parents=True)
    (raw / "flow_dumps" / "flows_0000_1792742100.txt").write_text(DUMP)
    return str(raw)


def test_flow_table_classifies_rules(raw_dir):
    payload = telemetry.flow_table(raw_dir, "s1")
    assert payload["available"] is True
    kinds = {r["kind"] for r in payload["rules"]}
    assert kinds == {"routed", "infrastructure", "table_miss"}
    counts = payload["counts_by_switch"]["s1"]
    assert counts["routed"] == 2
    assert counts["table_miss"] == 1
    assert counts["infrastructure"] == 1


def test_flow_table_reports_timeouts_only_for_routed_rules(raw_dir):
    payload = telemetry.flow_table(raw_dir)
    assert payload["routed_timeout_distribution"] == {"15": 1, "5": 1}


def test_flow_table_absent_is_unavailable(tmp_path):
    payload = telemetry.flow_table(str(tmp_path))
    assert payload["available"] is False
    assert payload["source"] == "unavailable"
    assert "reason" in payload


def test_active_path_derived_from_installed_rules(raw_dir):
    """Path activity must come from egress ports, not from agent intent."""
    payload = telemetry.active_paths(raw_dir)
    assert payload["available"] is True
    # s1 port 3 -> path A, port 4 -> path B (from topo.spec).
    assert payload["rule_counts"] == {"A": 1, "B": 1}
    assert payload["total_edge_rules"] == 2


def test_active_path_unavailable_without_a_dump(tmp_path):
    payload = telemetry.active_paths(str(tmp_path))
    assert payload["available"] is False
    assert "cannot confirm" in payload["reason"]


# ---------------------------------------------------------- decisions
def test_decode_state_expands_bins():
    decoded = telemetry.decode_state("3-0-1-2")
    assert decoded["decoded"] is True
    assert "saturated" in decoded["util_a"]["label"]
    assert "idle" in decoded["util_b"]["label"]
    assert decoded["miss_rate"]["bin"] == 2


def test_decode_state_rejects_malformed_input():
    assert telemetry.decode_state("nonsense")["decoded"] is False
    assert telemetry.decode_state("1-2-3")["decoded"] is False


def test_decisions_keep_missing_agent_fields_null(tmp_path):
    """Variant A has no agent; its Q-values must stay null, not become 0."""
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "decisions.csv").write_text(
        "t,step,variant,state,action,path,idle_timeout,exploratory,greedy_action,"
        "epsilon,reward,q_before,q_after,td_error,usable_paths,"
        "r_throughput,r_utilisation,r_loss,r_occupancy,r_controller_msgs\n"
        "100.0,0,A,0-0-0-0,\"path=A,idle=15\",A,15,,,,0.25,,,,A|B,0.3,0.1,0.0,0.0,0.0\n"
    )
    rows = telemetry.read_decisions(str(raw))
    assert len(rows) == 1
    row = rows[0]
    assert row["variant"] == "A"
    assert row["reward"] == pytest.approx(0.25)
    assert row["q_before"] is None
    assert row["epsilon"] is None
    assert row["exploratory"] is None  # not tracked, not False
    assert row["usable_paths"] == ["A", "B"]


def test_decision_vs_installed_awaits_confirmation(tmp_path):
    """A decision newer than the last dump must not be claimed as installed."""
    raw = tmp_path / "raw"
    (raw / "flow_dumps").mkdir(parents=True)
    (raw / "flow_dumps" / "flows_0000_1792742100.txt").write_text(DUMP)
    # Decision timestamped after the dump.
    (raw / "decisions.csv").write_text(
        "t,step,variant,state,action,path,idle_timeout,exploratory,greedy_action,"
        "epsilon,reward,q_before,q_after,td_error,usable_paths,"
        "r_throughput,r_utilisation,r_loss,r_occupancy,r_controller_msgs\n"
        "1792742200.0,5,C,1-1-0-0,\"path=B,idle=5\",B,5,0,\"path=B,idle=5\","
        "0.3,0.4,0.1,0.2,0.1,A|B,0.5,0.2,0.0,0.1,0.1\n"
    )
    payload = telemetry.decision_vs_installed(str(raw))
    assert payload["status"] == "awaiting_confirmation"
    assert "predates" in payload["detail"]


# ------------------------------------------------------- experiment safety
def test_validation_rejects_unknown_variant_and_workload():
    manager = ExperimentManager()
    with pytest.raises(ExperimentError, match="Unknown variant"):
        manager._validate("Z", "heavy", 1, 60, None)
    with pytest.raises(ExperimentError, match="Unknown workload"):
        manager._validate("A", "nonsense", 1, 60, None)


def test_validation_bounds_seed_and_duration():
    manager = ExperimentManager()
    with pytest.raises(ExperimentError, match="Seed"):
        manager._validate("A", "heavy", 0, 60, None)
    with pytest.raises(ExperimentError, match="Duration"):
        manager._validate("A", "heavy", 1, 5, None)
    with pytest.raises(ExperimentError, match="Duration"):
        manager._validate("A", "heavy", 1, 9999, None)


def test_validation_rejects_unlisted_idle_timeout():
    manager = ExperimentManager()
    with pytest.raises(ExperimentError, match="Idle timeout"):
        manager._validate("A", "heavy", 1, 60, 7)


def test_built_command_is_an_argv_list_with_no_shell_metacharacters():
    """The runner is launched with shell=False; nothing may be interpolated."""
    argv = ExperimentManager.build_command("C", "heavy", 3, 90, None)
    assert "-m" in argv and "experiments.run_experiment" in argv
    assert "--variant" in argv and "C" in argv
    assert all(not any(ch in part for ch in ";|&$`><") for part in argv)


def test_idle_timeout_not_passed_for_variant_c():
    """Variant C chooses its own timeout; forcing one would be a silent lie."""
    argv = ExperimentManager.build_command("C", "heavy", 1, 60, 15)
    assert "--idle-timeout" not in argv
    argv_b = ExperimentManager.build_command("B", "heavy", 1, 60, 15)
    assert "--idle-timeout" in argv_b


def _force_direct_path(monkeypatch, is_root: bool = True):
    """Make the manager use its own subprocess path, not the launcher.

    Both paths exist and both are tested; this pins which one a test means.
    """
    from dashboard.backend import config as config_mod
    from dashboard.backend import experiments as exp_module

    caps = config_mod.Capabilities(
        mode="live",
        can_read_runs=True,
        can_start_experiments=is_root,
        is_root=is_root,
        start_mechanism="root_api" if is_root else "none",
        launcher_available=False,
        launcher_detail="launcher disabled for this test",
        reason_cannot_start=None if is_root else "unprivileged and no launcher",
    )
    monkeypatch.setattr(exp_module, "probe_capabilities", lambda: caps)
    return caps


def test_start_refuses_while_another_experiment_runs(monkeypatch):
    from dashboard.backend import experiments as exp_module
    from dashboard.backend.runs import LiveStatus

    _force_direct_path(monkeypatch)
    busy = LiveStatus(active=True, run_id="r1", detail="Run r1 is in progress.")
    monkeypatch.setattr(exp_module, "live_status", lambda: busy)
    monkeypatch.setattr(exp_module, "scan_experiment_processes", lambda: [])

    manager = ExperimentManager()
    with pytest.raises(ExperimentError) as excinfo:
        manager.start("A", "light", 1, 30)
    assert excinfo.value.status == 409
    assert "already running" in excinfo.value.message


def test_stop_refuses_when_nothing_was_launched_here(monkeypatch):
    """A matrix started in a terminal must not be killable from the browser."""
    _force_direct_path(monkeypatch)
    manager = ExperimentManager()
    with pytest.raises(ExperimentError) as excinfo:
        manager.stop()
    assert excinfo.value.status == 409
    assert "not stoppable" in excinfo.value.message


def test_unprivileged_without_launcher_refuses_and_explains(monkeypatch):
    """The original problem: no root, no launcher -> a clear 503, not a crash."""
    from dashboard.backend import experiments as exp_module

    _force_direct_path(monkeypatch, is_root=False)
    monkeypatch.setattr(exp_module, "live_status",
                        lambda: __import__("dashboard.backend.runs",
                                           fromlist=["LiveStatus"]).LiveStatus())
    monkeypatch.setattr(exp_module, "scan_experiment_processes", lambda: [])

    manager = ExperimentManager()
    with pytest.raises(ExperimentError) as excinfo:
        manager.start("A", "light", 1, 30)
    assert excinfo.value.status == 503
    # It must tell the user how to fix it, including the command to run.
    assert "run_experiment" in excinfo.value.message


# ------------------------------------------------- launcher-delegated path
class _FakeLauncher:
    """Stands in for the privileged daemon."""

    def __init__(self, current=None, raise_on_start=None):
        self.current = current
        self.raise_on_start = raise_on_start
        self.started = []
        self.stopped = []
        self.injected = []

    def status(self):
        return {"current": self.current, "history": []}

    def start(self, **kwargs):
        if self.raise_on_start:
            raise self.raise_on_start
        self.started.append(kwargs)
        self.current = {
            "experiment_id": "exp-20261023-125758-c3da5252",
            "running": True, "pid": 4242, "params": kwargs,
        }
        return {"experiment": self.current}

    def stop(self, experiment_id):
        self.stopped.append(experiment_id)
        payload = dict(self.current or {})
        self.current = None
        payload["running"] = False
        return {"experiment": payload}

    def inject_link(self, experiment_id, action, link):
        self.injected.append((experiment_id, action, link))
        return {"command": {"action": action, "link": link}}


def _force_launcher_path(monkeypatch, fake):
    from dashboard.backend import config as config_mod
    from dashboard.backend import experiments as exp_module

    caps = config_mod.Capabilities(
        mode="live", can_read_runs=True, can_start_experiments=True,
        is_root=False, start_mechanism="launcher", launcher_available=True,
        launcher_detail="launcher daemon reachable",
    )
    monkeypatch.setattr(exp_module, "probe_capabilities", lambda: caps)
    monkeypatch.setattr(exp_module, "LAUNCHER", fake)
    return caps


def test_start_delegates_to_the_launcher_when_available(monkeypatch):
    fake = _FakeLauncher()
    _force_launcher_path(monkeypatch, fake)
    manager = ExperimentManager()
    result = manager.start("B", "heavy", 3, 60, idle_timeout=15)
    assert result["started_via"] == "launcher"
    assert fake.started[0]["variant"] == "B"
    assert fake.started[0]["idle_timeout"] == 15


def test_start_drops_idle_timeout_for_variant_c(monkeypatch):
    """C selects its own; forwarding one would be a setting with no effect."""
    fake = _FakeLauncher()
    _force_launcher_path(monkeypatch, fake)
    ExperimentManager().start("C", "heavy", 1, 60, idle_timeout=15)
    assert fake.started[0]["idle_timeout"] is None


def test_launcher_refusal_becomes_a_conflict(monkeypatch):
    from dashboard.launcher.client import LauncherError

    fake = _FakeLauncher(
        raise_on_start=LauncherError("an experiment is already running (exp-x)")
    )
    _force_launcher_path(monkeypatch, fake)
    with pytest.raises(ExperimentError) as excinfo:
        ExperimentManager().start("A", "light", 1, 30)
    assert excinfo.value.status == 409


def test_launcher_unavailable_becomes_503(monkeypatch):
    from dashboard.launcher.client import LauncherUnavailable

    fake = _FakeLauncher(raise_on_start=LauncherUnavailable("daemon is gone"))
    _force_launcher_path(monkeypatch, fake)
    with pytest.raises(ExperimentError) as excinfo:
        ExperimentManager().start("A", "light", 1, 30)
    assert excinfo.value.status == 503


def test_stop_via_launcher_requires_a_matching_id(monkeypatch):
    """A stale browser tab must not stop an experiment it is not looking at."""
    fake = _FakeLauncher(current={"experiment_id": "exp-20261023-125758-c3da5252",
                                  "running": True})
    _force_launcher_path(monkeypatch, fake)
    manager = ExperimentManager()
    with pytest.raises(ExperimentError) as excinfo:
        manager.stop(experiment_id="exp-20200101-000000-deadbeef")
    assert excinfo.value.status == 409
    assert fake.stopped == []


def test_stop_via_launcher_succeeds_with_the_right_id(monkeypatch):
    fake = _FakeLauncher(current={"experiment_id": "exp-20261023-125758-c3da5252",
                                  "running": True})
    _force_launcher_path(monkeypatch, fake)
    result = ExperimentManager().stop(
        experiment_id="exp-20261023-125758-c3da5252"
    )
    assert fake.stopped == ["exp-20261023-125758-c3da5252"]
    assert "incomplete" in result["note"]


def test_stop_via_launcher_with_nothing_running(monkeypatch):
    fake = _FakeLauncher(current=None)
    _force_launcher_path(monkeypatch, fake)
    with pytest.raises(ExperimentError) as excinfo:
        ExperimentManager().stop()
    assert excinfo.value.status == 409


def test_inject_link_requires_the_launcher(monkeypatch):
    _force_direct_path(monkeypatch, is_root=True)
    with pytest.raises(ExperimentError) as excinfo:
        ExperimentManager().inject_link("exp-20261023-125758-c3da5252",
                                        "link_down", "s1-s2")
    assert excinfo.value.status == 503


def test_inject_link_delegates_to_the_launcher(monkeypatch):
    fake = _FakeLauncher(current={"experiment_id": "exp-20261023-125758-c3da5252",
                                  "running": True})
    _force_launcher_path(monkeypatch, fake)
    result = ExperimentManager().inject_link(
        "exp-20261023-125758-c3da5252", "link_down", "s1-s2"
    )
    assert fake.injected == [("exp-20261023-125758-c3da5252", "link_down", "s1-s2")]
    assert "switches confirm" in result["note"]


def test_inject_link_endpoint_validates_body(client):
    assert client.post("/api/experiments/inject-link",
                       json={"experiment_id": "x"}).status_code == 400
    assert client.post("/api/experiments/inject-link",
                       content="not json").status_code == 400


def test_links_endpoint_lists_only_core_links(client):
    body = client.get("/api/experiments/links").json()
    ids = {l["id"] for l in body["links"]}
    expected = {"-".join(sorted([l.a, l.b])) for l in spec.CORE_LINKS}
    assert ids == expected
    # Host access links must not be offered.
    assert not any("h" in i for i in ids)


def test_start_endpoint_rejects_non_json_body(client):
    response = client.post("/api/experiments/start", content="not json")
    assert response.status_code in (400, 422)


def test_start_endpoint_rejects_bad_types(client):
    response = client.post(
        "/api/experiments/start",
        json={"variant": "A", "workload": "light", "seed": "abc", "duration_s": 30},
    )
    assert response.status_code == 400


def test_experiment_options_only_offers_implemented_choices(client):
    body = client.get("/api/experiments/options").json()
    from experiments.run_experiment import DEFAULT_WORKLOADS, VARIANT_APPS
    assert {v["id"] for v in body["variants"]} == set(VARIANT_APPS)
    assert {w["id"] for w in body["workloads"]} == set(DEFAULT_WORKLOADS)
    assert any(v["selects_own_timeout"] for v in body["variants"])


# ------------------------------------------------------------ comparison
def _summary(n, median, lo, hi):
    return {"n": n, "mean": median, "median": median,
            "ci95_low": lo, "ci95_high": hi}


def test_delta_uses_correct_direction_for_lower_is_better():
    # Latency: 10 -> 5 is an improvement even though the number went down.
    delta = _delta(_summary(5, 10.0, 9.0, 11.0), _summary(5, 5.0, 4.0, 6.0), True)
    assert delta["improved"] is True
    assert delta["verdict"] == "improved"
    assert delta["relative_change"] == pytest.approx(-0.5)


def test_delta_uses_correct_direction_for_higher_is_better():
    # Throughput: 5 -> 10 is an improvement.
    delta = _delta(_summary(5, 5.0, 4.0, 6.0), _summary(5, 10.0, 9.0, 11.0), False)
    assert delta["improved"] is True
    assert delta["verdict"] == "improved"


def test_delta_reports_regression_when_worse():
    delta = _delta(_summary(5, 10.0, 9.0, 11.0), _summary(5, 5.0, 4.0, 6.0), False)
    assert delta["improved"] is False
    assert delta["verdict"] == "regressed"


def test_overlapping_intervals_are_not_called_a_difference():
    """The central statistical honesty rule of the comparison page."""
    delta = _delta(_summary(5, 10.0, 5.0, 15.0), _summary(5, 12.0, 6.0, 18.0), False)
    assert delta["verdict"] == "not_distinguishable"
    assert "overlap" in delta["detail"]


def test_delta_unavailable_when_a_metric_is_missing():
    delta = _delta(_summary(0, None, None, None), _summary(5, 1.0, 0.5, 1.5), False)
    assert delta["available"] is False
    assert "missing" in delta["reason"]


def test_comparison_excludes_adhoc_dashboard_runs(tmp_path, monkeypatch):
    """The comparison page must sample only the controlled matrix.

    Ad-hoc runs started from the dashboard carry a tag and are not extra
    samples of a matrix cell -- analysis.parsers.is_primary_run documents why:
    one was launched with a seed the matrix already used, so pooling them
    double-counts that configuration, inflates n and understates the spread.

    This is a regression test for a real divergence: the offline analysis
    filtered these runs but this endpoint did not, so the page could report
    n=8 for a cell the matrix defines as n=5 while claiming in its own
    docstring to agree with the generated report by construction.
    """
    from dashboard.backend import comparison as comp

    primary = "20260101-000000-A-heavy-seed1"
    adhoc = "20260101-010000-A-heavy-seed1-ui"
    demo = "20260101-020000-A-heavy-seed2-webdemo"
    for name in (primary, adhoc, demo):
        (tmp_path / name).mkdir()

    loaded: list[str] = []

    def fake_load_run(path):
        loaded.append(os.path.basename(path))
        return None  # the inclusion decision is all this test cares about

    monkeypatch.setattr(comp, "load_run", fake_load_run)
    # The cache is keyed on the directory and its mtime fingerprint, but clear
    # it anyway so the assertion cannot be satisfied by an earlier call.
    comp._CACHE.clear()

    comp.collect_run_metrics(runs_dir=str(tmp_path), window="full")

    assert loaded == [primary], (
        f"only the matrix run should be considered, got {loaded}"
    )


def test_delta_guards_zero_baseline():
    delta = _delta(_summary(5, 0.0, -1.0, 1.0), _summary(5, 2.0, 1.0, 3.0), False)
    assert delta["available"] is False


def test_metric_info_marks_directions_correctly():
    assert METRIC_INFO["throughput_of_bps"]["lower_is_better"] is False
    for key in ("rtt_median_ms", "ping_loss_fraction", "mean_rules",
                "reactive_msgs_total", "recovery_s"):
        assert METRIC_INFO[key]["lower_is_better"] is True, key


# ----------------------------------------------------------------- runs
def test_run_status_distinguishes_interrupted_from_complete(tmp_path):
    """A manifest-less directory with no running process was interrupted."""
    ref = RunRef(run_id="r1", path=str(tmp_path), config={"variant": "A"})
    assert ref.status(live_run_id=None) == "interrupted"
    assert ref.status(live_run_id="r1") == "running"

    ref.manifest = {"complete": False}
    assert ref.status(live_run_id=None) == "incomplete"
    ref.manifest = {"complete": True}
    assert ref.status(live_run_id=None) == "complete"


def test_find_run_rejects_path_traversal():
    from dashboard.backend.runs import find_run
    assert find_run("../../etc") is None
    assert find_run("..") is None
    assert find_run("") is None


def test_unknown_run_returns_404(client):
    assert client.get("/api/runs/does-not-exist").status_code == 404
    assert client.get("/api/runs/does-not-exist/export.csv").status_code == 404


def test_analysis_file_rejects_traversal(client):
    assert client.get("/api/analysis/file/..%2F..%2Fetc%2Fpasswd").status_code in (400, 404)


def test_comparison_rejects_invalid_window(client):
    assert client.get("/api/comparison?window=bogus").status_code == 400


def test_comparison_rejects_non_integer_seeds(client):
    assert client.get("/api/comparison?seeds=a,b").status_code == 400


# ------------------------------------------------------------- mock mode
def test_mock_mode_labels_every_payload(monkeypatch, client):
    monkeypatch.setenv("SDNRL_DASHBOARD_MOCK", "1")
    try:
        assert client.get("/api/health").json()["mock_mode"] is True
        assert client.get("/api/topology/state").json()["source"] == "mock"
        assert client.get("/api/flows").json()["source"] == "mock"
        assert client.get("/api/metrics/live").json()["source"] == "mock"
        assert client.get("/api/rl/decisions").json()["source"] == "mock"
        snapshot = client.get("/api/snapshot").json()
        assert snapshot["mock_mode"] is True
        assert snapshot["source"] == "mock"
    finally:
        monkeypatch.delenv("SDNRL_DASHBOARD_MOCK", raising=False)


def test_live_mode_never_claims_mock_source(client):
    assert client.get("/api/health").json()["mock_mode"] is False
    body = client.get("/api/topology").json()
    assert body["source"] != "mock"


# ------------------------------------------------------------- snapshot
def test_snapshot_without_a_run_declares_unavailability(client, monkeypatch):
    from dashboard.backend import app as app_module
    from dashboard.backend.runs import LiveStatus

    monkeypatch.setattr(app_module, "live_status", lambda: LiveStatus(active=False))
    body = client.get("/api/snapshot").json()
    assert body["source"] == "unavailable"
    assert body["metrics"]["available"] is False
    assert body["decisions"] == []
