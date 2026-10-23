"""Variant A -- conventional routing with fixed flow-rule timeouts.

This is the control condition, and it is written to be a *fair* one rather than
a straw man:

* It routes by fewest hops, which is what OSPF-style shortest-path routing does
  and what essentially every default SDN forwarding app does.
* It reacts to link failure by recomputing over the remaining paths, so it
  reconverges rather than black-holing.  Without that, variant A would lose the
  recovery experiment by construction and the comparison would prove nothing.
* It uses one fixed idle timeout for every rule for the whole run, which is the
  "static flow-table management" this project is testing against.

What it does not do is adapt to load.  Path A has fewer hops but less capacity,
so A keeps using it however congested it gets.  That is the specific weakness
variants B and C are given the chance to improve on.
"""

from __future__ import annotations

from typing import Dict, Optional, Sequence, Tuple

from agent.reward import RewardTerms
from agent.state import State
from controller.base_app import BaseSdnApp
from telemetry import metrics
from topo import spec


class BaselineApp(BaseSdnApp):
    VARIANT = "A"

    def policy_step(
        self,
        state: State,
        usable_paths: Sequence[str],
        snapshot: metrics.Snapshot,
        reward: Optional[RewardTerms],
    ) -> Tuple[str, int, Dict[str, object]]:
        # Shortest available path by hop count, ties broken by name so the
        # choice is deterministic across runs.
        path = min(sorted(usable_paths), key=spec.path_hop_count)
        return path, self.fixed_idle_timeout, {"action": f"path={path},idle={self.fixed_idle_timeout}"}
