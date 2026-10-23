"""Tabular Q-learning agent.

Deliberately plain: a dict-of-dicts Q-table, epsilon-greedy exploration with
exponential decay, and the standard one-step update

    Q(s,a) <- Q(s,a) + alpha * ( r + gamma * max_a' Q(s',a') - Q(s,a) )

There is no neural network and no replay buffer.  With 144 states and 2-6
actions a table is the right tool, it trains inside a short episode, and -- the
reason that matters for this project -- every value it learns can be printed and
checked by hand, so "the agent changed its mind" is a verifiable claim rather
than a plausible story.

Determinism: all exploration draws come from a :class:`random.Random` seeded per
run, so a given (seed, workload, variant) replays identically.  The agent never
touches the global ``random`` module.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from random import Random
from typing import Dict, List, Optional, Sequence, Tuple

from agent.actions import Action, ActionSpace
from agent.state import State


@dataclass
class QLearningConfig:
    alpha: float = 0.2          # learning rate
    gamma: float = 0.8          # discount; episodes are short, so < 0.9
    epsilon_start: float = 0.9
    epsilon_end: float = 0.05
    epsilon_decay_steps: int = 300   # ~ one episode at a 1s step interval
    optimistic_init: float = 0.0     # initial Q value for unseen (s,a)

    def validate(self) -> None:
        if not 0.0 < self.alpha <= 1.0:
            raise ValueError("alpha must be in (0, 1]")
        if not 0.0 <= self.gamma < 1.0:
            raise ValueError("gamma must be in [0, 1)")
        if not 0.0 <= self.epsilon_end <= self.epsilon_start <= 1.0:
            raise ValueError("require 0 <= epsilon_end <= epsilon_start <= 1")
        if self.epsilon_decay_steps < 1:
            raise ValueError("epsilon_decay_steps must be >= 1")


@dataclass
class StepRecord:
    """One learning step, appended to the run's decision log."""

    step: int
    state: str
    action: str
    reward: float
    next_state: str
    q_before: float
    q_after: float
    td_error: float
    epsilon: float
    exploratory: bool
    greedy_action: str


class QLearningAgent:
    """Tabular Q-learning over a fixed :class:`ActionSpace`."""

    def __init__(
        self,
        action_space: ActionSpace,
        config: Optional[QLearningConfig] = None,
        seed: int = 0,
        frozen: bool = False,
    ):
        self.action_space = action_space
        self.config = config or QLearningConfig()
        self.config.validate()
        self.seed = seed
        self.rng = Random(seed)
        #: When frozen the agent still selects actions but performs no updates
        #: and never explores.  Used by the null-control experiment, which is
        #: how we show the *learned* values (not merely the plumbing) matter.
        self.frozen = frozen

        self.q: Dict[str, Dict[str, float]] = {}
        self.steps = 0
        self.updates = 0
        self.history: List[StepRecord] = []

    # ---------------------------------------------------------------- epsilon
    @property
    def epsilon(self) -> float:
        if self.frozen:
            return 0.0
        c = self.config
        if self.steps >= c.epsilon_decay_steps:
            return c.epsilon_end
        frac = self.steps / c.epsilon_decay_steps
        return c.epsilon_start + (c.epsilon_end - c.epsilon_start) * frac

    # ---------------------------------------------------------------- Q access
    def _row(self, state: State) -> Dict[str, float]:
        key = state.encode()
        row = self.q.get(key)
        if row is None:
            row = {
                self.action_space.encode(a): self.config.optimistic_init
                for a in self.action_space.actions
            }
            self.q[key] = row
        return row

    def q_value(self, state: State, action: Action) -> float:
        return self._row(state)[self.action_space.encode(action)]

    def q_row(self, state: State) -> Dict[str, float]:
        """Copy of the Q values for ``state`` (for logging and inspection)."""
        return dict(self._row(state))

    # ---------------------------------------------------------------- policy
    def greedy_action(self, state: State, allowed: Sequence[Action]) -> Action:
        """Highest-Q action among ``allowed``, ties broken deterministically.

        Ties are broken by action-space order rather than randomly so that an
        untrained (all-zero) table yields a fixed, predictable policy.  The
        null-control test depends on that predictability.
        """
        row = self._row(state)
        best: Optional[Action] = None
        best_q = float("-inf")
        for action in allowed:
            value = row[self.action_space.encode(action)]
            if value > best_q:
                best_q, best = value, action
        assert best is not None, "allowed action list was empty"
        return best

    def select_action(
        self, state: State, usable_paths: Optional[Sequence[str]] = None
    ) -> Tuple[Action, bool, Action]:
        """Pick an action for ``state``.

        Returns ``(action, was_exploratory, greedy_action)``.  The greedy action
        is returned alongside so the decision log can show what exploration
        overrode -- without it, an exploratory step is indistinguishable from a
        policy change.
        """
        allowed = list(self.action_space.actions)
        if usable_paths is not None:
            allowed = self.action_space.restrict(allowed, usable_paths)

        greedy = self.greedy_action(state, allowed)
        if not self.frozen and self.rng.random() < self.epsilon:
            choice = self.rng.choice(allowed)
            return choice, choice != greedy, greedy
        return greedy, False, greedy

    # ---------------------------------------------------------------- learning
    def update(
        self,
        state: State,
        action: Action,
        reward: float,
        next_state: State,
        epsilon: Optional[float] = None,
        exploratory: bool = False,
        greedy_action: Optional[Action] = None,
    ) -> StepRecord:
        """Apply one Q-learning update and record it."""
        a_key = self.action_space.encode(action)
        row = self._row(state)
        q_before = row[a_key]

        if self.frozen:
            q_after = q_before
            td_error = 0.0
        else:
            next_row = self._row(next_state)
            best_next = max(next_row.values()) if next_row else 0.0
            target = reward + self.config.gamma * best_next
            td_error = target - q_before
            q_after = q_before + self.config.alpha * td_error
            row[a_key] = q_after
            self.updates += 1

        record = StepRecord(
            step=self.steps,
            state=state.encode(),
            action=a_key,
            reward=reward,
            next_state=next_state.encode(),
            q_before=q_before,
            q_after=q_after,
            td_error=td_error,
            epsilon=self.epsilon if epsilon is None else epsilon,
            exploratory=exploratory,
            greedy_action=(
                self.action_space.encode(greedy_action)
                if greedy_action is not None
                else ""
            ),
        )
        self.history.append(record)
        self.steps += 1
        return record

    # ---------------------------------------------------------------- reporting
    def visited_states(self) -> int:
        return len(self.q)

    def coverage(self, total_states: int) -> float:
        return self.visited_states() / total_states if total_states else 0.0

    def nonzero_entries(self) -> int:
        return sum(1 for row in self.q.values() for v in row.values() if v != 0.0)

    def policy_table(self) -> Dict[str, str]:
        """Greedy action per visited state, for diffing policies between runs."""
        out = {}
        for key in sorted(self.q):
            state = State.decode(key)
            out[key] = self.action_space.encode(
                self.greedy_action(state, list(self.action_space.actions))
            )
        return out

    # ---------------------------------------------------------------- persistence
    def to_dict(self) -> dict:
        return {
            "action_space": self.action_space.name,
            "actions": [self.action_space.encode(a) for a in self.action_space.actions],
            "seed": self.seed,
            "frozen": self.frozen,
            "steps": self.steps,
            "updates": self.updates,
            "config": {
                "alpha": self.config.alpha,
                "gamma": self.config.gamma,
                "epsilon_start": self.config.epsilon_start,
                "epsilon_end": self.config.epsilon_end,
                "epsilon_decay_steps": self.config.epsilon_decay_steps,
                "optimistic_init": self.config.optimistic_init,
            },
            "q": self.q,
        }

    def save(self, path: str) -> None:
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, indent=2, sort_keys=True)
        os.replace(tmp, path)  # atomic, so a crash cannot leave a half table

    def load(self, path: str) -> None:
        """Load a Q-table, keeping only actions this action space still has.

        Refusing silently-mismatched tables matters: loading a variant-B table
        into variant C would look like it worked and quietly destroy the
        experiment.
        """
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)

        expected = [self.action_space.encode(a) for a in self.action_space.actions]
        found = data.get("actions", [])
        if found != expected:
            raise ValueError(
                "Q-table action space mismatch: "
                f"file has {found}, agent expects {expected}"
            )

        self.q = {str(k): {str(ak): float(av) for ak, av in v.items()}
                  for k, v in data.get("q", {}).items()}
        self.steps = int(data.get("steps", 0))
        self.updates = int(data.get("updates", 0))
