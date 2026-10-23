"""SIMULATED environment for offline agent development.

    ==================================================================
    WARNING: NOTHING IN THIS MODULE IS AN EXPERIMENTAL RESULT.
    This is an analytic toy model, not Mininet, not Open vSwitch, and
    not a network. Numbers produced here must never appear in the
    results tables or the report as measurements. They exist only to
    (a) exercise the learning loop in unit tests, and (b) sanity-check
    hyperparameters before spending hours of real emulation.
    Anything derived from this is labelled "simulated" everywhere.
    ==================================================================

The model deliberately reproduces only the structure the agent must discover:

* Path A has less capacity than B but fewer hops, so it is better when demand
  is low and worse when demand is high. An optimal policy is therefore
  *state-dependent*, which is what distinguishes a learned policy from any
  fixed choice.
* A short idle timeout keeps the flow table small but causes more table misses
  and so more controller work; a long timeout does the reverse. This is the
  trade-off variant C's extra actions exist to exploit.

If a policy cannot be learned here, it certainly will not be learned in 90
seconds of real traffic -- so this is a cheap filter, not evidence.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from agent.actions import ActionSpace
from agent.qlearning import QLearningAgent
from agent.reward import RewardNormalisers, RewardWeights, compute_reward
from agent.state import State, discretise
from topo import spec


@dataclass
class SimConfig:
    steps: int = 200
    #: Flow arrivals per second, driving table occupancy and miss rate.
    flow_arrival_rate: float = 3.0
    #: Demand alternates between these two levels in blocks, so the agent sees
    #: both regimes repeatedly and can learn a different action for each.
    low_demand_bps: float = 2.0e6
    high_demand_bps: float = 12.0e6
    regime_block_steps: int = 20
    occupancy_cap_rules: float = 100.0
    noise: float = 0.05


class OfflineEnv:
    """Analytic stand-in for the controller's measurement loop. SIMULATED."""

    def __init__(self, config: Optional[SimConfig] = None, seed: int = 0):
        self.cfg = config or SimConfig()
        self.rng = random.Random(seed)
        self.weights = RewardWeights()
        self.normalisers = RewardNormalisers(
            occupancy_cap_rules=self.cfg.occupancy_cap_rules
        )
        self.step_index = 0

    def demand_bps(self) -> float:
        block = (self.step_index // self.cfg.regime_block_steps) % 2
        base = self.cfg.high_demand_bps if block else self.cfg.low_demand_bps
        return base * (1.0 + self.rng.uniform(-self.cfg.noise, self.cfg.noise))

    def observe(self, path: str, idle_timeout: int) -> Tuple[State, float, Dict[str, float]]:
        """Apply one (path, timeout) action and return (state, reward, detail)."""
        demand = self.demand_bps()
        capacity = spec.PATH_CAPACITY_BPS[path]
        hops = spec.path_hop_count(path)

        delivered = min(demand, capacity)
        util = min(2.0, demand / capacity)
        loss = max(0.0, (demand - capacity) / demand) if demand > 0 else 0.0

        # Occupancy grows with arrival rate x timeout x path length: a longer
        # timeout holds each rule for longer, and a longer path installs the
        # rule on more switches.
        rules_per_switch = self.cfg.flow_arrival_rate * idle_timeout
        occupancy = min(1.0, rules_per_switch / self.cfg.occupancy_cap_rules)

        # A rule that has expired costs a fresh table miss. Short timeouts mean
        # a flow's later packets miss more often.
        miss_rate = self.cfg.flow_arrival_rate * (1.0 + 10.0 / max(1, idle_timeout))
        reactive_msgs = miss_rate * (1 + hops)

        state = discretise(
            {path: util, ("B" if path == "A" else "A"): 0.0},
            occupancy,
            miss_rate,
        )
        terms = compute_reward(
            delivered_bps=delivered,
            max_link_utilisation=min(1.0, util),
            loss_fraction=loss,
            occupancy_fraction=occupancy,
            controller_msg_rate=reactive_msgs,
            weights=self.weights,
            normalisers=self.normalisers,
        )
        self.step_index += 1
        return state, terms.total, {
            "demand_bps": demand,
            "delivered_bps": delivered,
            "util": util,
            "loss": loss,
            "occupancy": occupancy,
            "miss_rate": miss_rate,
            "reactive_msgs": reactive_msgs,
        }


def train(
    agent: QLearningAgent,
    env: OfflineEnv,
    steps: Optional[int] = None,
) -> List[float]:
    """Run the same state/action/reward/update cycle the controller uses.

    Returns the per-step reward sequence. SIMULATED -- see module warning.
    """
    steps = steps or env.cfg.steps
    space: ActionSpace = agent.action_space

    state = State(0, 0, 0, 0)
    rewards: List[float] = []
    pending: Optional[Tuple[State, object]] = None

    for _ in range(steps):
        action, exploratory, greedy = agent.select_action(state)
        path = space.path_of(action)
        idle = space.idle_timeout_of(action)
        next_state, reward, _ = env.observe(path, idle)

        if pending is not None:
            prev_state, prev_action = pending
            agent.update(prev_state, prev_action, reward, next_state,
                         exploratory=exploratory, greedy_action=greedy)
        pending = (state, action)
        state = next_state
        rewards.append(reward)

    return rewards


def mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0
