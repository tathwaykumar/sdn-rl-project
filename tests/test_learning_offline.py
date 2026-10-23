"""Does the learning loop actually learn? (SIMULATED environment only.)

These tests use ``sim.offline_env``, an analytic toy model -- NOT Mininet.
They prove the agent, reward and update cycle are wired together correctly and
that the loop can improve a policy at all.  They are emphatically **not**
experimental results and no number from here appears in the report.

The real behavioural evidence comes from switch flow-table dumps in
``analysis/verify_behaviour.py``.
"""

from __future__ import annotations

import pytest

from agent.actions import JointActionSpace, RoutingActionSpace
from agent.qlearning import QLearningAgent, QLearningConfig
from sim.offline_env import OfflineEnv, SimConfig, mean, train


def _agent(space, seed=0, **cfg):
    defaults = dict(alpha=0.3, gamma=0.8, epsilon_start=1.0,
                    epsilon_end=0.05, epsilon_decay_steps=200)
    defaults.update(cfg)
    return QLearningAgent(space, QLearningConfig(**defaults), seed=seed)


def test_reward_improves_over_training():
    """Late-training reward must beat early-training reward. SIMULATED."""
    env = OfflineEnv(SimConfig(steps=800), seed=1)
    agent = _agent(JointActionSpace(["A", "B"]), seed=1)
    rewards = train(agent, env, steps=800)
    early = mean(rewards[:200])
    late = mean(rewards[-200:])
    assert late > early, f"no improvement: early={early:.4f} late={late:.4f}"


def test_agent_visits_and_fills_the_table():
    env = OfflineEnv(SimConfig(steps=500), seed=2)
    agent = _agent(JointActionSpace(["A", "B"]), seed=2)
    train(agent, env, steps=500)
    assert agent.updates > 400
    assert agent.visited_states() > 1
    assert agent.nonzero_entries() > 0


def test_learned_policy_is_state_dependent():
    """The whole premise: the best action differs by state. SIMULATED.

    If the learned greedy policy were the same action everywhere, a static
    configuration could match it and the RL variants would have no reason to
    exist.
    """
    env = OfflineEnv(SimConfig(steps=1500), seed=3)
    agent = _agent(JointActionSpace(["A", "B"]), seed=3,
                   epsilon_decay_steps=600)
    train(agent, env, steps=1500)
    policy = agent.policy_table()
    distinct = set(policy.values())
    assert len(distinct) > 1, f"policy collapsed to a single action: {distinct}"


def test_frozen_agent_does_not_improve():
    """Null control: with learning off, reward must not trend upward."""
    env = OfflineEnv(SimConfig(steps=600), seed=4)
    space = JointActionSpace(["A", "B"])
    agent = QLearningAgent(space, QLearningConfig(), seed=4, frozen=True)
    rewards = train(agent, env, steps=600)
    early = mean(rewards[:200])
    late = mean(rewards[-200:])
    assert agent.updates == 0
    # Allow a little slack for the environment's own regime cycling and noise,
    # but it must not show the clear gain the learning agent shows.
    assert late - early < 0.05, f"frozen agent improved: {early:.4f} -> {late:.4f}"


def test_joint_space_reaches_at_least_routing_space_performance():
    """C's action space contains B's, so C should not do worse. SIMULATED."""
    results = {}
    for name, space in (
        ("B", RoutingActionSpace(["A", "B"], fixed_idle_timeout=15)),
        ("C", JointActionSpace(["A", "B"])),
    ):
        env = OfflineEnv(SimConfig(steps=1200), seed=5)
        agent = _agent(space, seed=5, epsilon_decay_steps=500)
        rewards = train(agent, env, steps=1200)
        results[name] = mean(rewards[-300:])
    # Not a strict inequality: C explores a larger space, so with equal budget
    # it can land slightly behind. A large shortfall would mean the extra
    # actions actively hurt, which would be worth knowing.
    assert results["C"] > results["B"] - 0.10, results


def test_timeout_choice_changes_outcome_in_the_model():
    """Confirms the model actually rewards timeout selection at all.

    Without this, test_joint_* above could pass vacuously on a model where the
    timeout action had no effect.
    """
    env = OfflineEnv(seed=6)
    _, r_short, d_short = env.observe("B", 5)
    env2 = OfflineEnv(seed=6)
    _, r_long, d_long = env2.observe("B", 60)
    assert d_short["occupancy"] != d_long["occupancy"]
    assert d_short["miss_rate"] != d_long["miss_rate"]
    assert r_short != r_long


def test_training_is_reproducible_under_a_seed():
    def run():
        env = OfflineEnv(SimConfig(steps=300), seed=7)
        agent = _agent(JointActionSpace(["A", "B"]), seed=7)
        return train(agent, env, steps=300)
    assert run() == run()
