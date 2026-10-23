"""Variant C -- joint RL routing and RL-selected flow-rule timeouts.

Identical to variant B except for the action space: C picks a (path, idle
timeout) pair, so it can trade flow-table occupancy and controller load against
throughput, which B's fixed timeout cannot express.
"""

from __future__ import annotations

from controller.base_app import BaseSdnApp
from controller.rl_mixin import RlControllerMixin


class RlJointApp(RlControllerMixin, BaseSdnApp):
    VARIANT = "C"
    ACTION_VARIANT = "C"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.init_agent()
