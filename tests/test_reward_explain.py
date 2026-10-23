"""Tests for the reward explanation surfaced in the dashboard.

The explanation must describe the reward the agent actually received. These
tests check that it is derived from the real implementation and that the
component breakdown reconciles with the recorded total -- including the two
cases where exact equality legitimately does not hold (recorded rounding, and
the +/-1 clip).

Nothing here changes the reward function or the agent.
"""

from __future__ import annotations

import csv
import json
import os
import warnings

import pytest

warnings.filterwarnings("ignore", category=SyntaxWarning)

from fastapi.testclient import TestClient  # noqa: E402

from agent.reward import (  # noqa: E402
    RewardNormalisers, RewardWeights, compute_reward,
)
from dashboard.backend import reward as reward_mod  # noqa: E402
from dashboard.backend.app import app  # noqa: E402

DECISION_HEADER = (
    "t,step,variant,state,action,path,idle_timeout,exploratory,greedy_action,"
    "epsilon,reward,q_before,q_after,td_error,usable_paths,"
    "r_throughput,r_utilisation,r_loss,r_occupancy,r_controller_msgs\n"
)


@pytest.fixture()
def client():
    return TestClient(app)


def _write_run(tmp_path, rows):
    raw = tmp_path / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    with open(raw / "decisions.csv", "w", encoding="utf-8") as fh:
        fh.write(DECISION_HEADER)
        for r in rows:
            fh.write(r + "\n")
    return str(raw)


def _row(step, reward, tp, ut, ls, oc, ms, variant="C"):
    return (
        f"1792740000.0,{step},{variant},1-1-0-0,\"path=B,idle=15\",B,15,0,"
        f"\"path=B,idle=15\",0.3,{reward},0.1,0.2,0.1,A|B,"
        f"{tp},{ut},{ls},{oc},{ms}"
    )


# ------------------------------------------------------- model fidelity
def test_model_reports_the_real_weights_and_signs():
    """Weights and signs must come from agent/reward.py, not be restated."""
    model = reward_mod.reward_model(None)
    defaults = RewardWeights()
    for term in model["terms"]:
        assert term["weight"] == getattr(defaults, term["weight_key"])
    signs = {t["key"]: t["sign"] for t in model["terms"]}
    # Throughput is the only term that can raise the score.
    assert signs["throughput"] == 1
    assert all(v == -1 for k, v in signs.items() if k != "throughput")


def test_model_reports_the_real_clip_range():
    model = reward_mod.reward_model(None)
    assert model["clip_range"] == [-1.0, 1.0]
    # And that range is what the implementation actually enforces.
    extreme = compute_reward(
        delivered_bps=1e15, max_link_utilisation=0.0, loss_fraction=0.0,
        occupancy_fraction=0.0, controller_msg_rate=0.0,
        weights=RewardWeights(), normalisers=RewardNormalisers(),
    )
    assert -1.0 <= extreme.total <= 1.0


def test_model_lists_every_term_the_formula_uses():
    model = reward_mod.reward_model(None)
    keys = {t["key"] for t in model["terms"]}
    assert keys == {"throughput", "utilisation", "loss", "occupancy",
                    "controller_msgs"}


def test_model_states_its_limitations():
    """A page that explains the reward must also say what it cannot claim."""
    model = reward_mod.reward_model(None)
    joined = " ".join(model["caveats"]).lower()
    assert "proxy" in joined           # utilisation stands in for latency
    assert "not tuned" in joined       # weights were hand-chosen
    assert "variant a" in joined       # has no agent


# --------------------------------------------------- contribution maths
def test_contributions_are_term_times_weight_times_sign(tmp_path):
    raw = _write_run(tmp_path, [_row(0, 0.253807, 0.665729, 0.979805, 0.0, 0.08, 0.0)])
    out = reward_mod.explain_decisions(None, raw, limit=10)
    step = out["steps"][0]
    by_key = {c["key"]: c for c in step["contributions"]}

    assert by_key["throughput"]["contribution"] == pytest.approx(0.665729 * 1.0)
    assert by_key["utilisation"]["contribution"] == pytest.approx(-0.979805 * 0.4)
    assert by_key["occupancy"]["contribution"] == pytest.approx(-0.08 * 0.25)
    assert by_key["loss"]["contribution"] == pytest.approx(0.0)


def test_breakdown_reconciles_with_the_recorded_reward(tmp_path):
    raw = _write_run(tmp_path, [_row(0, 0.253807, 0.665729, 0.979805, 0.0, 0.08, 0.0)])
    out = reward_mod.explain_decisions(None, raw, limit=10)
    step = out["steps"][0]
    assert step["reconciles"] is True
    assert step["sum_of_contributions"] == pytest.approx(step["reward"], abs=5e-6)
    assert out["reconciliation"]["ok"] is True
    assert out["reconciliation"]["unreconciled"] == 0


def test_reconciliation_tolerates_recorded_rounding_only(tmp_path):
    """Six-decimal rounding is fine; a real disagreement is not."""
    # Deliberately wrong total, far beyond rounding.
    raw = _write_run(tmp_path, [_row(0, 0.900000, 0.665729, 0.979805, 0.0, 0.08, 0.0)])
    out = reward_mod.explain_decisions(None, raw, limit=10)
    assert out["steps"][0]["reconciles"] is False
    assert out["reconciliation"]["ok"] is False
    assert out["reconciliation"]["unreconciled"] == 1


def test_clipping_is_reported_rather_than_hidden(tmp_path):
    """When the total is clipped the parts genuinely do not sum to it."""
    # Raw sum would be 1.0 * 1.0 = 1.0 exactly; push it past the clip.
    raw = _write_run(tmp_path, [_row(0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0)])
    out = reward_mod.explain_decisions(None, raw, limit=10)
    step = out["steps"][0]
    # Not clipped here: 1.0 is exactly the bound.
    assert step["clipped"] is False
    assert step["reconciles"] is True

    # Now a case that really exceeds it is impossible from a single term with
    # weight 1.0, so verify the clip logic directly against the implementation.
    terms = compute_reward(
        delivered_bps=1e12, max_link_utilisation=0.0, loss_fraction=0.0,
        occupancy_fraction=0.0, controller_msg_rate=0.0,
        weights=RewardWeights(throughput=5.0), normalisers=RewardNormalisers(),
    )
    assert terms.total == 1.0  # clipped from 5.0


def test_bands_classify_the_score(tmp_path):
    raw = _write_run(tmp_path, [
        _row(0, 0.400000, 0.5, 0.1, 0.0, 0.0, 0.0),
        _row(1, 0.000000, 0.0, 0.0, 0.0, 0.0, 0.0),
        _row(2, -0.300000, 0.0, 0.5, 0.2, 0.0, 0.0),
    ])
    out = reward_mod.explain_decisions(None, raw, limit=10)
    assert [s["band"] for s in out["steps"]] == ["positive", "near_zero", "negative"]


def test_missing_component_is_null_not_zero(tmp_path):
    """An unrecorded term must not be shown as a zero contribution."""
    row = (
        "1792740000.0,0,A,0-0-0-0,\"path=A,idle=15\",A,15,,,,0.25,,,,A|B,"
        ",,,,"  # every component blank, as variant A rows can be
    )
    raw = _write_run(tmp_path, [row])
    out = reward_mod.explain_decisions(None, raw, limit=10)
    step = out["steps"][0]
    assert all(c["contribution"] is None for c in step["contributions"])
    assert step["sum_of_contributions"] is None
    # With nothing to compare, the row is not counted as reconciled either way.
    assert out["reconciliation"]["checked"] == 0


# ------------------------------------------------------- missing inputs
def test_no_run_selected_is_unavailable_not_empty():
    out = reward_mod.explain_decisions(None, None, limit=10)
    assert out["available"] is False
    assert out["source"] == "unavailable"
    assert out["reason"]
    # The model is still returned so the page can explain the formula.
    assert out["model"]["terms"]


def test_run_without_decisions_is_reported(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    out = reward_mod.explain_decisions("run-x", str(raw), limit=10)
    assert out["available"] is False
    assert "decisions" in out["reason"]


# ------------------------------------------------------------- endpoints
def test_reward_model_endpoint(client):
    body = client.get("/api/reward/model").json()
    assert body["clip_range"] == [-1.0, 1.0]
    assert len(body["terms"]) == 5
    assert "throughput" in body["expression"]


def test_reward_model_rejects_unknown_run(client):
    assert client.get("/api/reward/model?run_id=nope").status_code == 404


def test_reward_explain_endpoint_without_a_run(client):
    body = client.get("/api/reward/explain").json()
    assert "model" in body
    assert body["available"] in (True, False)


def test_metric_catalog_defines_every_metric(client):
    body = client.get("/api/metrics/catalog").json()
    keys = {m["key"] for m in body["metrics"]}
    for expected in ("throughput", "latency_median", "latency_p95",
                     "packet_loss", "max_link_util", "flow_occupancy",
                     "rule_hit_ratio", "table_misses", "controller_load",
                     "recovery_time", "reward"):
        assert expected in keys, expected


def test_every_catalog_metric_has_unit_direction_and_plain_english(client):
    body = client.get("/api/metrics/catalog").json()
    for metric in body["metrics"]:
        assert metric["unit"], metric["key"]
        assert metric["direction"] in (
            "higher_is_better", "lower_is_better", "context"
        ), metric["key"]
        assert len(metric["plain"]) > 20, metric["key"]
        assert metric["kind"] in body["kinds"], metric["key"]


def test_loss_metric_carries_the_survivorship_caveat(client):
    """Latency and loss must not be read independently of each other."""
    body = client.get("/api/metrics/catalog").json()
    loss = next(m for m in body["metrics"] if m["key"] == "packet_loss")
    assert "caveat" in loss
    assert "latency" in loss["caveat"].lower()


# --------------------------------------------- against a real recorded run
def test_reconciles_against_a_real_recorded_run():
    """The strongest check: real data written by the real controller."""
    import glob
    runs = sorted(glob.glob(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "results", "runs", "2026*-C-*", "raw", "decisions.csv",
    )))
    if not runs:
        pytest.skip("no recorded runs available in this checkout")

    raw_dir = os.path.dirname(runs[0])
    run_id = os.path.basename(os.path.dirname(raw_dir))
    out = reward_mod.explain_decisions(run_id, raw_dir, limit=500)
    assert out["available"] is True
    rec = out["reconciliation"]
    assert rec["checked"] > 0
    assert rec["unreconciled"] == 0, (
        f"{rec['unreconciled']} rows did not reconcile; worst residual "
        f"{rec['worst_residual']}"
    )
    assert rec["worst_residual"] <= rec["tolerance"]
