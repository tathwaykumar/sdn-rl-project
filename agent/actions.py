"""Action spaces for the three experiment variants.

The distinction between variants B and C lives here and nowhere else:

* Variant B (``RoutingActionSpace``) chooses a **path only**.  Flow-rule idle
  timeouts stay at the fixed value configured for the run, exactly as in the
  conventional baseline A.
* Variant C (``JointActionSpace``) chooses a **(path, idle timeout) pair**.  Its
  action space is the cartesian product, so it is strictly larger and can trade
  flow-table occupancy against controller load in a way B structurally cannot.

Keeping both behind one interface means the controller code path is identical
for B and C; only the action space object differs.  That removes a whole class
of confounds from the comparison.
"""

from __future__ import annotations

from typing import Sequence, Tuple, Union

#: Idle-timeout classes in seconds, offered to variant C.
#:
#: Short timeouts keep the flow table small but force more table misses and so
#: more controller work; long timeouts do the opposite.  5s is below the gap
#: between our workload's flow arrivals, 60s outlives most of our flows, and
#: 15s sits between -- so the three classes genuinely differ in behaviour
#: rather than being three cosmetic variations.
TIMEOUT_CLASSES_S: Tuple[int, ...] = (5, 15, 60)

#: Hard timeout applied to every installed rule regardless of variant.  It is a
#: safety ceiling so a rule can never be immortal if idle traffic keeps it warm.
HARD_TIMEOUT_S: int = 120

RoutingAction = str                      # a path name, e.g. "A"
JointAction = Tuple[str, int]            # (path name, idle timeout seconds)
Action = Union[RoutingAction, JointAction]


class ActionSpace:
    """Common interface over the variant action spaces."""

    #: Stable, ordered tuple of every action. Index order is the agent's
    #: canonical action ordering and must not change between runs of the same
    #: variant, or saved Q-tables become meaningless.
    actions: Tuple[Action, ...] = ()

    name = "abstract"

    def __len__(self) -> int:
        return len(self.actions)

    def path_of(self, action: Action) -> str:
        raise NotImplementedError

    def idle_timeout_of(self, action: Action) -> int:
        raise NotImplementedError

    def encode(self, action: Action) -> str:
        """Stable string form, used as a Q-table key and in logs."""
        raise NotImplementedError

    def restrict(self, actions: Sequence[Action], usable_paths: Sequence[str]):
        """Subset of ``actions`` whose path is currently usable.

        Used when a link failure takes a path out of service: the agent must
        not be allowed to pick a dead path, and we must not pretend it did.
        """
        allowed = [a for a in actions if self.path_of(a) in usable_paths]
        return allowed or list(actions)


class RoutingActionSpace(ActionSpace):
    """Variant B: choose the path; idle timeout is fixed for the whole run."""

    name = "routing"

    def __init__(self, paths: Sequence[str], fixed_idle_timeout: int):
        if not paths:
            raise ValueError("at least one path is required")
        self.actions = tuple(paths)
        self.fixed_idle_timeout = int(fixed_idle_timeout)

    def path_of(self, action: Action) -> str:
        return str(action)

    def idle_timeout_of(self, action: Action) -> int:
        return self.fixed_idle_timeout

    def encode(self, action: Action) -> str:
        return f"path={action}"


class JointActionSpace(ActionSpace):
    """Variant C: choose the path *and* the idle-timeout class."""

    name = "joint"

    def __init__(
        self,
        paths: Sequence[str],
        timeout_classes: Sequence[int] = TIMEOUT_CLASSES_S,
    ):
        if not paths:
            raise ValueError("at least one path is required")
        if not timeout_classes:
            raise ValueError("at least one timeout class is required")
        self.actions = tuple(
            (p, int(t)) for p in paths for t in timeout_classes
        )

    def path_of(self, action: Action) -> str:
        return action[0]

    def idle_timeout_of(self, action: Action) -> int:
        return int(action[1])

    def encode(self, action: Action) -> str:
        return f"path={action[0]},idle={action[1]}"


def build_action_space(variant: str, paths: Sequence[str], fixed_idle_timeout: int) -> ActionSpace:
    """Factory keyed by variant letter, so the runner can stay generic."""
    variant = variant.upper()
    if variant == "B":
        return RoutingActionSpace(paths, fixed_idle_timeout)
    if variant == "C":
        return JointActionSpace(paths)
    raise ValueError(
        f"variant {variant!r} has no learned action space "
        "(variant A is the non-learning baseline)"
    )
