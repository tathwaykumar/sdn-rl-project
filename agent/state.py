"""State discretisation for the tabular Q-learning agent.

The agent only ever sees quantities a real OpenFlow controller can obtain from
the switches themselves -- port counters, flow counters and table counters.  It
never sees the ping/iperf3 numbers used to evaluate the experiment.  That
separation is deliberate: if the agent optimised the same measurements we score
it with, a good score would prove nothing about whether the policy is useful.

The state space is kept small on purpose.  Tabular Q-learning needs each state
to be visited many times before its row means anything, and our episodes are
seconds long, not hours.  With the bins below the space is::

    4 (util A) x 4 (util B) x 3 (occupancy) x 3 (miss rate) = 144 states

which a few hundred seconds of interaction can actually cover.  Widening any
bin set is the first thing to reconsider if the learning curves stay flat.
"""

from __future__ import annotations

from typing import Dict, NamedTuple, Sequence, Tuple

#: Upper edges for link-utilisation bins (fraction of link capacity).
#: Four bins: idle, light, busy, saturated.
UTIL_EDGES: Tuple[float, ...] = (0.25, 0.50, 0.75)

#: Upper edges for flow-table occupancy bins (fraction of the soft cap).
OCCUPANCY_EDGES: Tuple[float, ...] = (0.33, 0.66)

#: Upper edges for table-miss-rate bins (misses per second, absolute).
#: Chosen from observed baseline behaviour: a handful of misses per second is
#: normal flow setup, tens per second means rules are expiring too fast.
MISS_RATE_EDGES: Tuple[float, ...] = (5.0, 20.0)


def _bin_index(value: float, edges: Sequence[float]) -> int:
    """Index of the bin ``value`` falls into; len(edges) is the top bin."""
    for i, edge in enumerate(edges):
        if value < edge:
            return i
    return len(edges)


class State(NamedTuple):
    """Discretised controller view. Hashable, so it keys the Q-table directly."""

    util_a: int
    util_b: int
    occupancy: int
    miss_rate: int

    def encode(self) -> str:
        """Stable string form for Q-table persistence and logging."""
        return f"{self.util_a}-{self.util_b}-{self.occupancy}-{self.miss_rate}"

    @classmethod
    def decode(cls, text: str) -> "State":
        parts = text.split("-")
        if len(parts) != 4:
            raise ValueError(f"malformed state key: {text!r}")
        return cls(*(int(p) for p in parts))


def discretise(
    path_util: Dict[str, float],
    occupancy_fraction: float,
    miss_rate_per_s: float,
) -> State:
    """Map raw controller observations onto a :class:`State`.

    ``path_util`` maps path name -> utilisation fraction of that path's
    bottleneck link. Missing paths are treated as idle, which is the correct
    reading when a path carries no traffic.
    """
    return State(
        util_a=_bin_index(max(0.0, path_util.get("A", 0.0)), UTIL_EDGES),
        util_b=_bin_index(max(0.0, path_util.get("B", 0.0)), UTIL_EDGES),
        occupancy=_bin_index(max(0.0, occupancy_fraction), OCCUPANCY_EDGES),
        miss_rate=_bin_index(max(0.0, miss_rate_per_s), MISS_RATE_EDGES),
    )


def state_space_size() -> int:
    """Total number of reachable discrete states (for reporting coverage)."""
    return (
        (len(UTIL_EDGES) + 1) ** 2
        * (len(OCCUPANCY_EDGES) + 1)
        * (len(MISS_RATE_EDGES) + 1)
    )


def all_states() -> Tuple[State, ...]:
    """Every state in the space, used by tests and coverage reporting."""
    out = []
    for ua in range(len(UTIL_EDGES) + 1):
        for ub in range(len(UTIL_EDGES) + 1):
            for oc in range(len(OCCUPANCY_EDGES) + 1):
                for mr in range(len(MISS_RATE_EDGES) + 1):
                    out.append(State(ua, ub, oc, mr))
    return tuple(out)
