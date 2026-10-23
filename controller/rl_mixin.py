"""Shared reinforcement-learning plumbing for variants B and C.

This module defines no OpenFlow application, only a mixin.  That matters
mechanically: ``os_ken``'s ``AppManager.load_app`` picks the first class in the
loaded module that subclasses ``OSKenApp`` *and* is defined in that module, so
the variant modules must each contain exactly one app class.  Keeping the shared
code in a non-app mixin here means B and C are a handful of lines each and
cannot drift apart.

The one-step cycle, run once per polling interval:

1. A reward is measured for the interval the previous action was applied to.
2. ``Q(s_prev, a_prev)`` is updated towards ``r + gamma * max_a Q(s_now, a)``.
3. A new action is selected for ``s_now`` and returned for the next interval.

On the first interval there is no previous action, so step 2 is skipped -- the
agent cannot be rewarded for something it did not do.
"""

from __future__ import annotations

import os
from typing import Dict, Optional, Sequence, Tuple

from agent.actions import Action, ActionSpace, build_action_space
from agent.qlearning import QLearningAgent, QLearningConfig
from agent.reward import RewardTerms
from agent.state import State, state_space_size
from telemetry import metrics
from topo import spec


class RlControllerMixin:
    """Adds a Q-learning policy to :class:`controller.base_app.BaseSdnApp`."""

    #: "B" (routing only) or "C" (joint routing + timeout).
    ACTION_VARIANT = "B"

    def init_agent(self) -> None:
        """Build the agent. Call after ``BaseSdnApp.__init__`` has run."""
        cfg = self.cfg
        self.action_space: ActionSpace = build_action_space(
            self.ACTION_VARIANT,
            paths=list(spec.PATH_NAMES),
            fixed_idle_timeout=self.fixed_idle_timeout,
        )

        q_cfg = QLearningConfig(**cfg.get("agent", {}))
        self.agent = QLearningAgent(
            action_space=self.action_space,
            config=q_cfg,
            seed=int(cfg.get("seed", 0)),
            frozen=bool(cfg.get("frozen_agent", False)),
        )

        # Null control: a frozen all-zero table makes the policy collapse to the
        # deterministic tie-break, which is how we demonstrate that the learned
        # values -- not merely the code path -- change behaviour.
        qtable_in = cfg.get("qtable_in")
        if qtable_in:
            self.agent.load(qtable_in)
            self.logger.info(
                "loaded Q-table from %s (%d states)", qtable_in, self.agent.visited_states()
            )

        # Action shuffle: permutes the action->(path, timeout) mapping. If
        # metrics are unchanged under shuffling, the actions are not reaching
        # the dataplane and any apparent learning is an artefact.
        self._shuffle = bool(cfg.get("shuffle_actions", False))
        self._action_permutation: Dict[str, Action] = {}
        if self._shuffle:
            import random as _random

            actions = list(self.action_space.actions)
            shuffled = list(actions)
            _random.Random(int(cfg.get("seed", 0)) + 9901).shuffle(shuffled)
            self._action_permutation = {
                self.action_space.encode(a): b for a, b in zip(actions, shuffled)
            }
            self.logger.warning("ACTION SHUFFLE ACTIVE (diagnostic run)")

        self._pending: Optional[Tuple[State, Action, bool, Action]] = None
        self.logger.info(
            "variant=%s actions=%d state_space=%d frozen=%s",
            self.ACTION_VARIANT,
            len(self.action_space),
            state_space_size(),
            self.agent.frozen,
        )

    def _apply_permutation(self, action: Action) -> Action:
        if not self._shuffle:
            return action
        return self._action_permutation.get(self.action_space.encode(action), action)

    def policy_step(
        self,
        state: State,
        usable_paths: Sequence[str],
        snapshot: metrics.Snapshot,
        reward: Optional[RewardTerms],
    ) -> Tuple[str, int, Dict[str, object]]:
        extra: Dict[str, object] = {}

        # ---- 1 & 2: learn from the interval just measured -----------------
        if self._pending is not None and reward is not None:
            prev_state, prev_action, was_exploratory, greedy = self._pending
            record = self.agent.update(
                state=prev_state,
                action=prev_action,
                reward=reward.total,
                next_state=state,
                exploratory=was_exploratory,
                greedy_action=greedy,
            )
            extra.update({
                "q_before": round(record.q_before, 6),
                "q_after": round(record.q_after, 6),
                "td_error": round(record.td_error, 6),
                "exploratory": int(was_exploratory),
                "greedy_action": record.greedy_action,
            })

        # ---- 3: choose the action for the coming interval ------------------
        action, exploratory, greedy = self.agent.select_action(state, usable_paths)
        self._pending = (state, action, exploratory, greedy)

        effective = self._apply_permutation(action)
        path = self.action_space.path_of(effective)
        idle = self.action_space.idle_timeout_of(effective)

        extra.setdefault("exploratory", int(exploratory))
        extra.setdefault("greedy_action", self.action_space.encode(greedy))
        extra["epsilon"] = round(self.agent.epsilon, 6)
        extra["action"] = self.action_space.encode(action)
        return path, idle, extra

    def on_shutdown(self) -> None:
        """Persist the Q-table, policy and learning history."""
        out = self.cfg.get("qtable_out")
        if out:
            self.agent.save(out)
            self.logger.info("saved Q-table to %s", out)

        policy_path = os.path.join(self.run_dir, "policy.json")
        try:
            import json

            with open(policy_path, "w", encoding="utf-8") as fh:
                json.dump(
                    {
                        "variant": self.ACTION_VARIANT,
                        "steps": self.agent.steps,
                        "updates": self.agent.updates,
                        "visited_states": self.agent.visited_states(),
                        "state_space_size": state_space_size(),
                        "coverage": round(self.agent.coverage(state_space_size()), 4),
                        "nonzero_q_entries": self.agent.nonzero_entries(),
                        "frozen": self.agent.frozen,
                        "shuffled": self._shuffle,
                        "greedy_policy": self.agent.policy_table(),
                    },
                    fh,
                    indent=2,
                    sort_keys=True,
                )
        except Exception:
            self.logger.exception("failed to write policy.json")
