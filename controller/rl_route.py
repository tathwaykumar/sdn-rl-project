"""Variant B -- RL-based routing with fixed flow-rule timeouts.

The agent chooses the path; the idle timeout is the same fixed value variant A
uses.  So B differs from A in exactly one respect, and differs from C in exactly
one respect.  Any measured difference between B and C is therefore attributable
to timeout control and nothing else.
"""

from __future__ import annotations

from controller.base_app import BaseSdnApp
from controller.rl_mixin import RlControllerMixin


class RlRouteApp(RlControllerMixin, BaseSdnApp):
    VARIANT = "B"
    ACTION_VARIANT = "B"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.init_agent()
