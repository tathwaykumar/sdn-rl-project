"""Tests for the Q-learning agent, action spaces, state discretisation and reward.

The emphasis is on the properties the experiment's validity rests on:
determinism given a seed, the frozen agent genuinely not learning, refusal to
load a mismatched Q-table, and the update rule computing what it claims to.
"""

from __future__ import annotations

import json
import math
import os

import pytest

from agent import state as state_mod
from agent.actions import (
    HARD_TIMEOUT_S,
    TIMEOUT_CLASSES_S,
    JointActionSpace,
    RoutingActionSpace,
    build_action_space,
)
from agent.qlearning import QLearningAgent, QLearningConfig
from agent.reward import RewardNormalisers, RewardWeights, compute_reward
from agent.state import State, discretise, state_space_size


# ----------------------------------------------------------------- state
def test_bin_edges_are_half_open():
    assert state_mod._bin_index(0.0, (0.25, 0.5, 0.75)) == 0
    assert state_mod._bin_index(0.25, (0.25, 0.5, 0.75)) == 1  # edge goes up
    assert state_mod._bin_index(0.99, (0.25, 0.5, 0.75)) == 3
    assert state_mod._bin_index(99.0, (0.25, 0.5, 0.75)) == 3  # saturates


def test_state_space_size_matches_enumeration():
    assert state_space_size() == len(state_mod.all_states())
    assert state_space_size() == 144


def test_state_encode_decode_roundtrip():
    for st in state_mod.all_states():
        assert State.decode(st.encode()) == st


def test_discretise_handles_missing_and_negative():
    """A path carrying no traffic is idle, and negatives must not wrap."""
    st = discretise({}, 0.0, 0.0)
    assert st == State(0, 0, 0, 0)
    st = discretise({"A": -1.0, "B": 2.0}, -5.0, -1.0)
    assert st.util_a == 0 and st.util_b == 3
    assert st.occupancy == 0 and st.miss_rate == 0


def test_discretise_distinguishes_load():
    idle = discretise({"A": 0.0, "B": 0.0}, 0.0, 0.0)
    busy = discretise({"A": 0.9, "B": 0.1}, 0.5, 30.0)
    assert idle != busy
    assert busy.util_a == 3 and busy.occupancy == 1 and busy.miss_rate == 2


# ---------------------------------------------------------------- actions
def test_routing_action_space_is_paths_only():
    sp = RoutingActionSpace(["A", "B"], fixed_idle_timeout=15)
    assert len(sp) == 2
    assert sp.path_of("A") == "A"
    # The whole point of variant B: timeout never varies.
    assert {sp.idle_timeout_of(a) for a in sp.actions} == {15}


def test_joint_action_space_is_the_cross_product():
    sp = JointActionSpace(["A", "B"])
    assert len(sp) == 2 * len(TIMEOUT_CLASSES_S)
    assert {sp.path_of(a) for a in sp.actions} == {"A", "B"}
    assert {sp.idle_timeout_of(a) for a in sp.actions} == set(TIMEOUT_CLASSES_S)


def test_joint_space_strictly_larger_than_routing_space():
    """C must be able to express everything B can, and more."""
    b = RoutingActionSpace(["A", "B"], 15)
    c = JointActionSpace(["A", "B"])
    assert len(c) > len(b)


def test_action_encoding_is_stable_and_distinct():
    sp = JointActionSpace(["A", "B"])
    encoded = [sp.encode(a) for a in sp.actions]
    assert len(set(encoded)) == len(encoded)
    assert sp.encode(("A", 5)) == "path=A,idle=5"


def test_restrict_filters_dead_paths():
    sp = JointActionSpace(["A", "B"])
    allowed = sp.restrict(list(sp.actions), ["B"])
    assert allowed and all(sp.path_of(a) == "B" for a in allowed)


def test_restrict_never_returns_empty():
    """Returning nothing would crash action selection mid-run."""
    sp = JointActionSpace(["A", "B"])
    assert sp.restrict(list(sp.actions), ["nonexistent"]) == list(sp.actions)


def test_build_action_space_rejects_variant_a():
    with pytest.raises(ValueError, match="variant"):
        build_action_space("A", ["A", "B"], 15)


def test_hard_timeout_exceeds_every_idle_class():
    assert all(HARD_TIMEOUT_S > t for t in TIMEOUT_CLASSES_S)


# --------------------------------------------------------------- q-learning
def _agent(**kw):
    sp = RoutingActionSpace(["A", "B"], 15)
    return QLearningAgent(sp, QLearningConfig(**kw), seed=42)


def test_update_implements_the_bellman_step():
    ag = _agent(alpha=0.5, gamma=0.9)
    s, s2 = State(0, 0, 0, 0), State(1, 1, 0, 0)
    # Seed a known next-state value so the target is predictable.
    ag.q[s2.encode()] = {"path=A": 2.0, "path=B": 0.0}
    rec = ag.update(s, "A", reward=1.0, next_state=s2)
    # target = 1.0 + 0.9 * 2.0 = 2.8 ; Q <- 0 + 0.5*(2.8-0) = 1.4
    assert math.isclose(rec.q_after, 1.4, rel_tol=1e-9)
    assert math.isclose(rec.td_error, 2.8, rel_tol=1e-9)
    assert math.isclose(ag.q_value(s, "A"), 1.4, rel_tol=1e-9)


def test_epsilon_decays_monotonically_to_floor():
    ag = _agent(epsilon_start=1.0, epsilon_end=0.1, epsilon_decay_steps=10)
    values = []
    s = State(0, 0, 0, 0)
    for _ in range(15):
        values.append(ag.epsilon)
        ag.update(s, "A", 0.0, s)
    assert values[0] == pytest.approx(1.0)
    assert all(b <= a + 1e-12 for a, b in zip(values, values[1:]))
    assert ag.epsilon == pytest.approx(0.1)


def test_same_seed_reproduces_action_sequence():
    """Required for the experiment to be reproducible at all."""
    s = State(0, 0, 0, 0)
    seqs = []
    for _ in range(2):
        sp = RoutingActionSpace(["A", "B"], 15)
        ag = QLearningAgent(sp, QLearningConfig(epsilon_start=1.0), seed=7)
        seq = []
        for _ in range(20):
            a, _, _ = ag.select_action(s)
            seq.append(a)
            ag.update(s, a, 0.1, s)
        seqs.append(seq)
    assert seqs[0] == seqs[1]


def test_different_seeds_diverge():
    s = State(0, 0, 0, 0)
    def run(seed):
        sp = RoutingActionSpace(["A", "B"], 15)
        ag = QLearningAgent(sp, QLearningConfig(epsilon_start=1.0), seed=seed)
        out = []
        for _ in range(40):
            a, _, _ = ag.select_action(s)
            out.append(a)
            ag.update(s, a, 0.1, s)
        return out
    assert run(1) != run(2)


def test_greedy_tie_break_is_deterministic_not_random():
    """The null-control experiment relies on an all-zero table being stable."""
    ag = _agent()
    s = State(0, 0, 0, 0)
    picks = {ag.greedy_action(s, list(ag.action_space.actions)) for _ in range(50)}
    assert picks == {"A"}  # first in action-space order


def test_greedy_follows_learned_values():
    ag = _agent(alpha=1.0, gamma=0.0)
    s = State(2, 0, 0, 0)
    ag.update(s, "B", reward=5.0, next_state=s)
    assert ag.greedy_action(s, list(ag.action_space.actions)) == "B"


def test_frozen_agent_never_learns_or_explores():
    sp = RoutingActionSpace(["A", "B"], 15)
    ag = QLearningAgent(sp, QLearningConfig(epsilon_start=1.0), seed=3, frozen=True)
    s = State(0, 0, 0, 0)
    for _ in range(30):
        a, exploratory, _ = ag.select_action(s)
        assert a == "A"           # deterministic tie-break
        assert exploratory is False
        ag.update(s, a, reward=9.0, next_state=s)
    assert ag.epsilon == 0.0
    assert ag.updates == 0
    assert ag.nonzero_entries() == 0


def test_exploration_flag_marks_overridden_greedy_choice():
    sp = RoutingActionSpace(["A", "B"], 15)
    ag = QLearningAgent(sp, QLearningConfig(epsilon_start=1.0), seed=11)
    s = State(0, 0, 0, 0)
    ag.update(s, "A", reward=10.0, next_state=s)  # make A clearly greedy
    saw_exploration = False
    for _ in range(60):
        action, exploratory, greedy = ag.select_action(s)
        assert greedy == "A"
        if exploratory:
            assert action != greedy
            saw_exploration = True
    assert saw_exploration, "epsilon=1.0 should have explored at least once"


def test_save_load_roundtrip(tmp_path):
    ag = _agent()
    s = State(1, 2, 0, 1)
    ag.update(s, "B", reward=0.75, next_state=s)
    path = os.path.join(tmp_path, "q.json")
    ag.save(path)

    sp2 = RoutingActionSpace(["A", "B"], 15)
    ag2 = QLearningAgent(sp2, QLearningConfig(), seed=0)
    ag2.load(path)
    assert ag2.q_value(s, "B") == pytest.approx(ag.q_value(s, "B"))
    assert ag2.steps == ag.steps


def test_load_rejects_mismatched_action_space(tmp_path):
    """Loading a B table into C would corrupt the experiment silently."""
    b = QLearningAgent(RoutingActionSpace(["A", "B"], 15), seed=0)
    path = os.path.join(tmp_path, "b.json")
    b.save(path)
    c = QLearningAgent(JointActionSpace(["A", "B"]), seed=0)
    with pytest.raises(ValueError, match="action space mismatch"):
        c.load(path)


def test_save_is_atomic_leaving_no_temp_file(tmp_path):
    ag = _agent()
    path = os.path.join(tmp_path, "q.json")
    ag.save(path)
    assert os.path.exists(path)
    assert not os.path.exists(path + ".tmp")
    json.load(open(path))  # valid JSON


def test_config_validation_rejects_nonsense():
    with pytest.raises(ValueError):
        QLearningConfig(alpha=0.0).validate()
    with pytest.raises(ValueError):
        QLearningConfig(gamma=1.0).validate()
    with pytest.raises(ValueError):
        QLearningConfig(epsilon_start=0.1, epsilon_end=0.9).validate()


# ------------------------------------------------------------------ reward
def _reward(**kw):
    base = dict(
        delivered_bps=0.0, max_link_utilisation=0.0, loss_fraction=0.0,
        occupancy_fraction=0.0, controller_msg_rate=0.0,
        weights=RewardWeights(), normalisers=RewardNormalisers(),
    )
    base.update(kw)
    return compute_reward(**base)


def test_throughput_increases_reward_and_penalties_decrease_it():
    baseline = _reward().total
    assert _reward(delivered_bps=15e6).total > baseline
    assert _reward(loss_fraction=1.0).total < baseline
    assert _reward(max_link_utilisation=1.0).total < baseline
    assert _reward(occupancy_fraction=1.0).total < baseline
    assert _reward(controller_msg_rate=100.0).total < baseline


def test_reward_terms_are_normalised_and_total_clipped():
    r = _reward(delivered_bps=1e12, max_link_utilisation=50.0,
                loss_fraction=5.0, occupancy_fraction=5.0,
                controller_msg_rate=1e6)
    for value in (r.throughput, r.utilisation, r.loss, r.occupancy, r.controller_msgs):
        assert 0.0 <= value <= 1.0
    assert -1.0 <= r.total <= 1.0


def test_occupancy_and_message_weights_are_nonzero():
    """If these were zero, variant C could not possibly beat variant B."""
    w = RewardWeights()
    assert w.occupancy > 0
    assert w.controller_msgs > 0


def test_reward_is_deterministic():
    a = _reward(delivered_bps=5e6, max_link_utilisation=0.5)
    b = _reward(delivered_bps=5e6, max_link_utilisation=0.5)
    assert a.total == b.total
