"""Tests for how the controller variants are wired together.

No Mininet and no root: these import the app modules and inspect them. They
guard the structural assumptions that would otherwise fail silently at runtime,
in the middle of an experiment, as wrong results rather than as an error.
"""

from __future__ import annotations

import inspect
import json
import os
import warnings

import pytest

# os-ken 4.1.1 emits a SyntaxWarning from vrrp.py on Python 3.14
# ('break' in a 'finally' block). It is harmless and unrelated to this project.
warnings.filterwarnings("ignore", category=SyntaxWarning)

from os_ken.base.app_manager import OSKenApp  # noqa: E402

from agent.actions import JointActionSpace, RoutingActionSpace  # noqa: E402

VARIANT_MODULES = {
    "A": "controller.baseline",
    "B": "controller.rl_route",
    "C": "controller.rl_joint",
}


def _app_classes_defined_in(module):
    """App classes defined *in* this module, which is what os-ken picks up."""
    return [
        cls for _, cls in inspect.getmembers(module, inspect.isclass)
        if issubclass(cls, OSKenApp) and cls.__module__ == module.__name__
    ]


@pytest.fixture(scope="module")
def modules():
    import importlib
    return {v: importlib.import_module(m) for v, m in VARIANT_MODULES.items()}


@pytest.mark.parametrize("variant", sorted(VARIANT_MODULES))
def test_each_variant_module_defines_exactly_one_app(variant, modules):
    """os-ken's load_app picks the FIRST app class it finds in the module.

    inspect.getmembers returns members alphabetically, so a second app class in
    one of these modules would not be an error -- it would quietly change which
    controller gets loaded, and the run would measure the wrong variant while
    looking completely healthy.
    """
    classes = _app_classes_defined_in(modules[variant])
    assert len(classes) == 1, (
        f"{VARIANT_MODULES[variant]} defines {len(classes)} app classes "
        f"({[c.__name__ for c in classes]}); os-ken would load only the first"
    )


@pytest.mark.parametrize("variant", sorted(VARIANT_MODULES))
def test_variant_label_matches_module(variant, modules):
    cls = _app_classes_defined_in(modules[variant])[0]
    assert cls.VARIANT == variant


@pytest.mark.parametrize("variant", sorted(VARIANT_MODULES))
def test_every_variant_speaks_openflow_13(variant, modules):
    from os_ken.ofproto import ofproto_v1_3
    cls = _app_classes_defined_in(modules[variant])[0]
    assert cls.OFP_VERSIONS == [ofproto_v1_3.OFP_VERSION]


def test_rl_mixin_defines_no_app_class():
    """The shared mixin must not be loadable as an app in its own right."""
    import controller.rl_mixin as mixin
    assert _app_classes_defined_in(mixin) == []


@pytest.mark.parametrize("variant", sorted(VARIANT_MODULES))
def test_every_variant_implements_the_policy_hook(variant, modules):
    cls = _app_classes_defined_in(modules[variant])[0]
    from controller.base_app import BaseSdnApp
    assert cls.policy_step is not BaseSdnApp.policy_step, (
        f"variant {variant} does not override policy_step; it would raise "
        "NotImplementedError on the first polling interval"
    )


def test_variants_b_and_c_have_different_action_spaces():
    """If B and C shared an action space, the comparison would be vacuous."""
    b = RoutingActionSpace(["A", "B"], fixed_idle_timeout=15)
    c = JointActionSpace(["A", "B"])
    assert len(c) > len(b)
    assert {b.idle_timeout_of(a) for a in b.actions} == {15}
    assert len({c.idle_timeout_of(a) for a in c.actions}) > 1


def test_routed_cookie_is_distinct_from_infrastructure_cookie():
    """The flow flush is cookie-scoped; a shared cookie would wipe the
    table-miss rule and break recovery, as it once did."""
    from controller.base_app import (
        FLOW_PRIORITY, INFRA_PRIORITY, ROUTED_COOKIE, TABLE_MISS_PRIORITY,
    )
    assert ROUTED_COOKIE != 0
    assert TABLE_MISS_PRIORITY < INFRA_PRIORITY < FLOW_PRIORITY


def test_base_app_requires_a_run_config(monkeypatch):
    """Starting without SDNRL_CONFIG must fail loudly, not write nowhere."""
    from controller.base_app import load_run_config
    monkeypatch.delenv("SDNRL_CONFIG", raising=False)
    with pytest.raises(RuntimeError, match="SDNRL_CONFIG"):
        load_run_config()


def test_run_config_round_trips(tmp_path, monkeypatch):
    from controller.base_app import load_run_config
    path = os.path.join(tmp_path, "cfg.json")
    payload = {"variant": "C", "run_dir": str(tmp_path), "seed": 3}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)
    monkeypatch.setenv("SDNRL_CONFIG", path)
    assert load_run_config() == payload


def test_experiment_runner_maps_every_variant_to_an_app():
    from experiments.run_experiment import VARIANT_APPS
    assert set(VARIANT_APPS) == set(VARIANT_MODULES)
    for variant, module in VARIANT_MODULES.items():
        assert VARIANT_APPS[variant] == module


def test_all_workloads_are_registered():
    from experiments.run_experiment import DEFAULT_WORKLOADS
    from experiments.workloads import WORKLOADS
    for name in DEFAULT_WORKLOADS:
        assert name in WORKLOADS, f"workload {name!r} is not registered"


def test_failure_workload_timings_scale_with_duration():
    """Absolute offsets would make a short run skip the failure silently."""
    from experiments.workloads import build_workload
    short = build_workload("failure", seed=1, raw_dir="/tmp", duration_s=30)
    long = build_workload("failure", seed=1, raw_dir="/tmp", duration_s=90)
    assert short.fail_at_s < short.repair_at_s < short.duration_s
    assert long.fail_at_s > short.fail_at_s
