"""Reward function, computed only from OpenFlow statistics.

Every term here comes from counters the controller reads off the switches
(port stats, flow stats, table stats).  None of it comes from ping or iperf3.

That restriction is the point.  The evaluation metrics in the results tables are
measured independently, by the hosts, with tools the controller cannot see.  If
the reward were built from those same measurements, a good final score would
only show the optimiser had optimised its own objective -- it would say nothing
about whether the policy improved the network.  Here, improvement in the
reported metrics is a genuine out-of-sample consequence of the learned policy.

Each term is normalised to roughly [0, 1] before weighting so that the weights
mean what they look like, and the total is clipped to [-1, 1] to keep TD errors
in a range a 0.2 learning rate can handle.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict


@dataclass
class RewardWeights:
    """Weights for the reward terms.

    Defaults favour throughput, with congestion (the latency proxy) and loss
    close behind, and smaller pressure on flow-table occupancy and controller
    load.  The occupancy and message terms are what give variant C something to
    gain over B; if they were zero, C's extra timeout actions could not pay off
    and the comparison would be rigged against it.

    Weights are recorded into every run directory, so any result can be traced
    back to the objective that produced it.
    """

    throughput: float = 1.0
    utilisation: float = 0.4
    loss: float = 0.6
    occupancy: float = 0.25
    controller_msgs: float = 0.15

    def as_dict(self) -> Dict[str, float]:
        return asdict(self)


@dataclass
class RewardNormalisers:
    """Reference values that map raw measurements onto [0, 1].

    ``throughput_ref_bps`` is the combined capacity of both paths, so delivering
    everything both paths can carry scores 1.0.  ``msg_rate_ref`` is a
    controller message rate we consider high for this topology.
    """

    throughput_ref_bps: float = 15.0e6   # 5 Mbit path A + 10 Mbit path B
    msg_rate_ref: float = 50.0           # controller messages per second
    occupancy_cap_rules: float = 100.0   # soft flow-table cap per switch


@dataclass
class RewardTerms:
    """Breakdown of one reward, logged so the total is always explainable."""

    throughput: float
    utilisation: float
    loss: float
    occupancy: float
    controller_msgs: float
    total: float

    def as_dict(self) -> Dict[str, float]:
        return asdict(self)


def clip01(x: float) -> float:
    """Clamp to [0, 1]."""
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return x


def compute_reward(
    delivered_bps: float,
    max_link_utilisation: float,
    loss_fraction: float,
    occupancy_fraction: float,
    controller_msg_rate: float,
    weights: RewardWeights,
    normalisers: RewardNormalisers,
) -> RewardTerms:
    """Combine one interval's OpenFlow-derived measurements into a reward.

    Args:
        delivered_bps: bits/sec actually delivered to destination hosts, from
            edge-switch egress port counters.
        max_link_utilisation: highest core-link utilisation this interval,
            as a fraction of that link's capacity. Stands in for congestion
            and queueing delay, which OF stats cannot measure directly.
        loss_fraction: dropped packets / offered packets on core links.
        occupancy_fraction: mean flow-table occupancy against the soft cap.
        controller_msg_rate: *reactive* controller messages per second
            (PacketIn + FlowMod). Periodic stats polling is excluded: it is
            fixed monitoring overhead the policy cannot change, and including
            it would swamp the term and remove the incentive to manage
            timeouts at all.
    """
    tp = clip01(delivered_bps / normalisers.throughput_ref_bps)
    util = clip01(max_link_utilisation)
    loss = clip01(loss_fraction)
    occ = clip01(occupancy_fraction)
    msgs = clip01(controller_msg_rate / normalisers.msg_rate_ref)

    total = (
        weights.throughput * tp
        - weights.utilisation * util
        - weights.loss * loss
        - weights.occupancy * occ
        - weights.controller_msgs * msgs
    )
    total = max(-1.0, min(1.0, total))

    return RewardTerms(
        throughput=tp,
        utilisation=util,
        loss=loss,
        occupancy=occ,
        controller_msgs=msgs,
        total=total,
    )
