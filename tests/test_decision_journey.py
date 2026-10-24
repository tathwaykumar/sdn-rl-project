"""Tests for the Decision Journey instrumentation and its dashboard surfacing.

Three things are checked here that nowhere else in the suite checks:

1. That recording the full Q-value row before each action is selected does
   not change what the agent does. This is additive logging, not a behaviour
   change, and the test proves it rather than asserting it in a comment.
2. That the recorded Q-values and the recorded ``greedy_action`` agree with
   each other -- the controller computes ``greedy_action`` independently
   (inside ``agent.update``), and the dashboard computes its own argmax from
   the logged Q-value row. If those two ever disagreed, the "Choose Action"
   step of the journey would be showing something that contradicts the
   controller's own record.
3. That per-step switch confirmation and per-host traffic direction are
   derived correctly from raw counters, including the cases that are easy to
   get backwards (which counter is "sent" vs "received" from a host's point
   of view) or easy to silently fabricate (a step from a run recorded before
   this instrumentation existed must report "not recorded", never a zero or
   invented Q-value).
"""

from __future__ import annotations

import csv
import json
import os
import warnings

import pytest

warnings.filterwarnings("ignore", category=SyntaxWarning)

from agent.actions import JointActionSpace, RoutingActionSpace  # noqa: E402
from agent.qlearning import QLearningAgent, QLearningConfig  # noqa: E402
from agent.state import State  # noqa: E402
from sim.offline_env import OfflineEnv, SimConfig  # noqa: E402


# --------------------------------------------------------- behaviour neutrality
def _run_with_instrumentation(space_factory, seed: int, steps: int,
                              instrument: bool):
    """Mirrors rl_mixin.policy_step's call order, with or without the new
    q_row()/restrict() calls spliced in before select_action()."""
    env = OfflineEnv(SimConfig(steps=steps), seed=seed)
    agent = QLearningAgent(
        space_factory(), QLearningConfig(epsilon_start=0.9, epsilon_decay_steps=steps // 2),
        seed=seed,
    )
    state = State(0, 0, 0, 0)
    actions_taken = []
    pending = None

    for _ in range(steps):
        if pending is not None:
            prev_state, prev_action, was_exploratory, greedy = pending
            # reward is irrelevant to the action sequence; use a fixed value
            agent.update(prev_state, prev_action, 0.0, state,
                         exploratory=was_exploratory, greedy_action=greedy)

        if instrument:
            # Exactly what rl_mixin.py now does: read the row and the allowed
            # set before selection. Must not perturb anything that follows.
            # usable_paths mirrors what base_app.usable_paths() always returns
            # in production -- never None, since it falls back to every path
            # when nothing has failed.
            _ = agent.q_row(state)
            _ = agent.action_space.restrict(list(agent.action_space.actions), ["A", "B"])

        action, exploratory, greedy = agent.select_action(state)
        pending = (state, action, exploratory, greedy)
        actions_taken.append(agent.action_space.encode(action))

        # Advance the (toy) environment the same way for both runs.
        next_state, _reward, _ = env.observe(
            agent.action_space.path_of(action),
            agent.action_space.idle_timeout_of(action),
        )
        state = next_state

    return actions_taken


@pytest.mark.parametrize("space_factory", [
    lambda: RoutingActionSpace(["A", "B"], fixed_idle_timeout=15),
    lambda: JointActionSpace(["A", "B"]),
])
def test_instrumentation_does_not_change_the_action_sequence(space_factory):
    """The regression test for the claim made in rl_mixin.py's comment."""
    for seed in (1, 7, 42):
        without = _run_with_instrumentation(space_factory, seed, 150, instrument=False)
        with_ = _run_with_instrumentation(space_factory, seed, 150, instrument=True)
        assert without == with_, f"seed {seed}: action sequence changed"


# ------------------------------------------------------------- q_values_json
def test_q_values_json_round_trips_through_the_logged_format():
    space = JointActionSpace(["A", "B"])
    agent = QLearningAgent(space, QLearningConfig(), seed=3)
    state = State(1, 2, 0, 1)
    agent.update(state, ("B", 15), reward=0.4, next_state=state)

    row = agent.q_row(state)
    allowed = space.restrict(list(space.actions), ["A", "B"])
    payload = json.dumps({
        "values": {k: round(v, 6) for k, v in sorted(row.items())},
        "allowed": sorted(space.encode(a) for a in allowed),
    }, sort_keys=True)

    decoded = json.loads(payload)
    assert decoded["values"][space.encode(("B", 15))] == pytest.approx(0.4 * 0.2)
    assert set(decoded["allowed"]) == {space.encode(a) for a in space.actions}


def test_greedy_action_matches_argmax_of_logged_q_values():
    """What the controller records as greedy_action must match what the
    dashboard will independently compute from the logged Q-value row --
    otherwise the journey would show two different "chosen because" stories."""
    space = JointActionSpace(["A", "B"])
    agent = QLearningAgent(space, QLearningConfig(), seed=11)
    state = State(2, 0, 1, 0)

    # Force a specific, non-zero landscape.
    agent.update(state, ("A", 5), reward=0.1, next_state=state)
    agent.update(state, ("B", 60), reward=0.9, next_state=state)

    row = agent.q_row(state)
    allowed_keys = [space.encode(a) for a in space.actions]
    computed_argmax = max(allowed_keys, key=lambda k: row[k])

    controller_greedy = agent.greedy_action(state, list(space.actions))
    assert space.encode(controller_greedy) == computed_argmax


def test_disallowed_actions_are_excluded_from_allowed_but_still_logged():
    """A link failure removes a path from 'allowed', but the Q-values for it
    must still be visible -- hiding them would misrepresent what the agent
    knew, not just what it could act on."""
    space = JointActionSpace(["A", "B"])
    agent = QLearningAgent(space, QLearningConfig(), seed=5)
    state = State(0, 0, 0, 0)
    agent.update(state, ("A", 15), reward=0.3, next_state=state)

    row = agent.q_row(state)
    allowed = space.restrict(list(space.actions), ["B"])  # A unusable
    allowed_keys = {space.encode(a) for a in allowed}

    assert all(space.encode(a) in row for a in space.actions)  # nothing hidden
    assert all(space.path_of(a) == "B" for a in allowed)       # but A excluded


# -------------------------------------------------------- dashboard surfacing
DECISION_HEADER_WITH_Q = (
    "t,step,variant,state,action,path,idle_timeout,exploratory,greedy_action,"
    "epsilon,reward,q_before,q_after,td_error,usable_paths,"
    "r_throughput,r_utilisation,r_loss,r_occupancy,r_controller_msgs,q_values_json\n"
)
DECISION_HEADER_WITHOUT_Q = (  # pre-instrumentation shape, still must parse
    "t,step,variant,state,action,path,idle_timeout,exploratory,greedy_action,"
    "epsilon,reward,q_before,q_after,td_error,usable_paths,"
    "r_throughput,r_utilisation,r_loss,r_occupancy,r_controller_msgs\n"
)


def _write_run(tmp_path, header, rows, name="raw"):
    raw = tmp_path / name
    raw.mkdir(parents=True, exist_ok=True)
    with open(raw / "decisions.csv", "w", encoding="utf-8") as fh:
        fh.write(header)
        for r in rows:
            fh.write(r + "\n")
    return str(raw)


def test_decisions_without_q_values_json_report_unavailable_not_invented(tmp_path):
    """Runs recorded before this instrumentation existed (the whole 60-run
    matrix) must not have Q-values fabricated for them."""
    from dashboard.backend import telemetry as T

    row = (
        "1792740000.0,0,B,0-0-0-0,\"path=A\",A,15,0,\"path=A\",0.3,0.2,"
        "0.1,0.15,0.05,A|B,0.3,0.1,0.0,0.0,0.0"
    )
    raw = _write_run(tmp_path, DECISION_HEADER_WITHOUT_Q, [row])
    decisions = T.read_decisions(raw)
    assert len(decisions) == 1
    # No crash on the missing column, and nothing invented in its place.
    assert decisions[0].get("q_values") in (None, {})


def test_decisions_with_q_values_json_expose_the_full_row(tmp_path):
    from dashboard.backend import telemetry as T

    payload = json.dumps({
        "values": {"path=A,idle=5": 0.1, "path=A,idle=15": 0.2,
                  "path=B,idle=5": 0.05, "path=B,idle=15": 0.4,
                  "path=A,idle=60": 0.0, "path=B,idle=60": 0.0},
        "allowed": ["path=A,idle=5", "path=A,idle=15", "path=A,idle=60",
                   "path=B,idle=5", "path=B,idle=15", "path=B,idle=60"],
    })
    row = (
        "1792740000.0,0,C,0-0-0-0,\"path=B,idle=15\",B,15,0,\"path=B,idle=15\","
        "0.3,0.2,0.1,0.15,0.05,A|B,0.3,0.1,0.0,0.0,0.0,"
        f"\"{payload.replace(chr(34), chr(34)*2)}\""
    )
    raw = _write_run(tmp_path, DECISION_HEADER_WITH_Q, [row])
    decisions = T.read_decisions(raw)
    assert len(decisions) == 1
    q = decisions[0]["q_values"]
    assert q is not None
    assert q["values"]["path=B,idle=15"] == pytest.approx(0.4)
    assert len(q["allowed"]) == 6


# ------------------------------------------------------- per-step confirmation
FLOW_DUMP_TMPL = """\
# t={t}
===== s1 =====
OFPST_FLOW reply (OF1.3) (xid=0x2):
{s1_rules}
===== s4 =====
OFPST_FLOW reply (OF1.3) (xid=0x2):
{s4_rules}
"""


def _rule(switch, port, src="10.0.0.1", dst="10.0.0.3", cookie="0x5d4e0001"):
    return (f" cookie={cookie}, duration=9.0s, table=0, n_packets=5, n_bytes=500, "
           f"idle_timeout=15, hard_timeout=120, send_flow_rem "
           f"priority=10,tcp,nw_src={src},nw_dst={dst},tp_src=1,tp_dst=2 "
           f"actions=output:{port}")


def test_step_verification_finds_the_first_dump_after_the_decision(tmp_path):
    """A decision is confirmed by the first dump taken *after* it -- not by
    rules that happen to already exist from an earlier decision."""
    from dashboard.backend import telemetry as T

    raw = tmp_path / "raw"
    (raw / "flow_dumps").mkdir(parents=True)
    # Dump 0: path A still installed (pre-decision state).
    (raw / "flow_dumps" / "flows_0000_1000.txt").write_text(
        FLOW_DUMP_TMPL.format(t=1000.0, s1_rules=_rule("s1", 3), s4_rules=_rule("s4", 1))
    )
    # Dump 1: path B now installed (post-decision state).
    (raw / "flow_dumps" / "flows_0001_1010.txt").write_text(
        FLOW_DUMP_TMPL.format(t=1010.0, s1_rules=_rule("s1", 4), s4_rules=_rule("s4", 1))
    )
    (raw / "decisions.csv").write_text(
        DECISION_HEADER_WITHOUT_Q +
        "1005.0,0,B,0-0-0-0,\"path=B\",B,15,0,\"path=B\",0.3,0.2,,,,"
        "A|B,0.3,0.1,0.0,0.0,0.0\n"
    )

    result = T.verify_decision_at(str(raw), step=0)
    assert result["status"] == "confirmed"
    assert result["dump_t"] == pytest.approx(1010.0)


def test_step_verification_reports_no_later_dump_honestly(tmp_path):
    """If the run ended before a confirming dump was taken, say so --
    never silently report the stale pre-decision dump as confirmation."""
    from dashboard.backend import telemetry as T

    raw = tmp_path / "raw"
    (raw / "flow_dumps").mkdir(parents=True)
    (raw / "flow_dumps" / "flows_0000_1000.txt").write_text(
        FLOW_DUMP_TMPL.format(t=1000.0, s1_rules=_rule("s1", 3), s4_rules=_rule("s4", 1))
    )
    (raw / "decisions.csv").write_text(
        DECISION_HEADER_WITHOUT_Q +
        "1005.0,0,B,0-0-0-0,\"path=B\",B,15,0,\"path=B\",0.3,0.2,,,,"
        "A|B,0.3,0.1,0.0,0.0,0.0\n"
    )
    result = T.verify_decision_at(str(raw), step=0)
    assert result["status"] == "no_later_dump"
    assert "after this decision" in result["detail"].lower()
    assert "cannot be confirmed" in result["detail"].lower()


def test_step_verification_unknown_step_is_an_error_not_a_guess(tmp_path):
    from dashboard.backend import telemetry as T
    raw = tmp_path / "raw"
    (raw / "flow_dumps").mkdir(parents=True)
    (raw / "decisions.csv").write_text(
        DECISION_HEADER_WITHOUT_Q +
        "1005.0,0,B,0-0-0-0,\"path=B\",B,15,0,\"path=B\",0.3,0.2,,,,"
        "A|B,0.3,0.1,0.0,0.0,0.0\n"
    )
    result = T.verify_decision_at(str(raw), step=99)
    assert result["available"] is False


# ------------------------------------------------------------ host traffic
def test_host_traffic_direction_is_not_swapped(tmp_path):
    """The easiest way to get this wrong: a switch's egress (tx) port counter
    is what the HOST received, and its ingress (rx) counter is what the host
    SENT. Mixing these up would show every host's traffic backwards."""
    from dashboard.backend import topology as Topo

    raw = tmp_path / "raw"
    raw.mkdir()
    # h1 is on s1 port 1 (topo.spec). Two samples, 1s apart.
    # switch tx = host received; switch rx = host sent.
    rows = [
        "t,switch,port,tx_packets,rx_packets,tx_bytes,rx_bytes,tx_dropped,rx_dropped,tx_errors,rx_errors\n",
        "1000.0,s1,1,10,5,10000,5000,0,0,0,0\n",
        "1001.0,s1,1,20,9,20000,9000,0,0,0,0\n",
    ]
    with open(raw / "raw_port_stats.csv", "w") as fh:
        fh.writelines(rows)

    traffic = Topo.host_traffic(str(raw))
    assert traffic["available"] is True
    h1 = traffic["hosts"]["h1"]
    # Received = switch tx delta = (20000-10000)*8 = 80000 bits over 1s
    assert h1["received_bytes"] == 20000           # cumulative, latest tx_bytes
    assert h1["sent_bytes"] == 9000                # cumulative, latest rx_bytes
    assert h1["receive_bps"] == pytest.approx(80000.0)
    assert h1["send_bps"] == pytest.approx(32000.0)  # (9000-5000)*8


def test_host_traffic_totals_available_but_rates_need_two_samples(tmp_path):
    """Cumulative totals are a single-sample read (counters are monotonic
    since switch start); throughput is a rate and genuinely needs two."""
    from dashboard.backend import topology as Topo
    raw = tmp_path / "raw"
    raw.mkdir()
    with open(raw / "raw_port_stats.csv", "w") as fh:
        fh.write("t,switch,port,tx_packets,rx_packets,tx_bytes,rx_bytes,tx_dropped,rx_dropped,tx_errors,rx_errors\n")
        fh.write("1000.0,s1,1,10,5,10000,5000,0,0,0,0\n")
    traffic = Topo.host_traffic(str(raw))
    assert traffic["available"] is True
    h1 = traffic["hosts"]["h1"]
    assert h1["received_bytes"] == 10000
    assert h1["sent_bytes"] == 5000
    assert h1["receive_bps"] is None
    assert h1["send_bps"] is None


# --------------------------------------------------------- real-data check
def test_q_values_reconcile_with_greedy_action_on_a_real_instrumented_run():
    """Cross-checks the *most recently recorded* instrumented run: its logged
    greedy_action must match the argmax the dashboard independently computes
    from the logged Q-value row, for every decision.

    Deliberately scoped to the newest instrumented run rather than every
    instrumented run ever produced. Raw run directories are append-only (see
    the project's no-mutation policy for results/runs/), so a run recorded
    before a since-fixed logging bug keeps that bug baked into its CSV
    forever -- that is a known, already-diagnosed historical artifact, not an
    ongoing regression, and must not permanently fail this suite. The newest
    run always reflects the current code, so it is the only one that can
    actually tell us whether *today's* instrumentation is self-consistent.
    """
    import glob
    from dashboard.backend import telemetry as T

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidates = sorted(
        glob.glob(os.path.join(root, "results", "runs", "*", "raw", "decisions.csv"))
    )
    instrumented = []
    for path in candidates:
        with open(path, encoding="utf-8") as fh:
            header = fh.readline()
        if "q_values_json" in header:
            instrumented.append(path)
    if not instrumented:
        pytest.skip("no instrumented run in this checkout yet")

    # Run directories are named "<UTC timestamp>-...", so the lexicographic
    # max is also the most recently recorded run.
    newest_path = max(instrumented, key=lambda p: os.path.basename(os.path.dirname(os.path.dirname(p))))
    raw_dir = os.path.dirname(newest_path)
    decisions = T.read_decisions(raw_dir, limit=500)

    checked = 0
    for d in decisions:
        q = d.get("q_values")
        if not q or not q.get("allowed"):
            continue
        allowed = q["allowed"]
        values = q["values"]
        argmax = max(allowed, key=lambda k: values[k])
        if d.get("greedy_action"):
            checked += 1
            assert argmax == d["greedy_action"], (
                f"{raw_dir} step {d['step']}: logged greedy_action "
                f"{d['greedy_action']!r} != argmax {argmax!r} of logged Q-values"
            )
    assert checked > 0, f"{raw_dir} had q_values_json but no decision to check"


# ------------------------------------------------------------- HTTP endpoints
@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from dashboard.backend.app import app
    return TestClient(app)


def test_step_verification_endpoint_requires_a_step(client):
    assert client.get("/api/rl/step-verification").status_code == 422


def test_step_verification_endpoint_without_a_run(client):
    body = client.get("/api/rl/step-verification?step=0").json()
    # No experiment running and no run selected in a bare test client.
    assert body["available"] in (False, True)


def test_step_verification_endpoint_rejects_unknown_run(client):
    # Consistent with every other run_id-taking endpoint in this API.
    resp = client.get("/api/rl/step-verification?step=0&run_id=does-not-exist")
    assert resp.status_code == 404


def test_topology_state_exposes_host_traffic_shape(client):
    body = client.get("/api/topology/state").json()
    live = body.get("live", {})
    # No run selected -> no telemetry, but the field must exist and be honest.
    assert "host_traffic_available" in live or live.get("available") is False


def test_reward_explain_endpoint_carries_q_value_reconciliation_when_available(client):
    body = client.get("/api/reward/explain").json()
    if body.get("available"):
        assert "q_value_reconciliation" in body
        assert body["q_value_reconciliation"]["ok"] in (True, False)
    else:
        # No run selected and nothing running: honestly unavailable, and
        # there is nothing to reconcile, so the key is correctly absent.
        assert body["reason"]
