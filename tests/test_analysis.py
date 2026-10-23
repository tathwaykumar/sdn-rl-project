"""Tests for the analysis pipeline.

Every number in the report passes through this code, so the cases below focus
on the ways a parser can produce a confident wrong answer: a truncated
measurement read as zero, a failed iperf3 run counted as a result, a recovery
time invented when the network never recovered, or a statistic that hides how
few observations it rests on.
"""

from __future__ import annotations

import json
import os

import pytest

from analysis import stats
from analysis.parsers import (
    parse_flow_dump, parse_iperf3, parse_ping, recovery_window, to_float,
)


# ------------------------------------------------------------------ iperf3
def _write(tmp_path, name: str, text: str) -> str:
    path = os.path.join(tmp_path, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def _iperf_json(bps: float, bytes_: int = 1000) -> str:
    return json.dumps({
        "end": {
            "sum_received": {"bits_per_second": bps, "bytes": bytes_, "seconds": 5.0},
            "sum_sent": {"retransmits": 3},
        }
    })


def test_parse_iperf3_success(tmp_path):
    path = _write(tmp_path, "iperf3_a.json", _iperf_json(5_000_000.0))
    r = parse_iperf3(path)
    assert r.ok
    assert r.bits_per_second == pytest.approx(5e6)
    assert r.retransmits == 3


def test_parse_iperf3_empty_file_is_not_zero(tmp_path):
    """A killed client leaves an empty file; that is missing, not 0 bits/s."""
    path = _write(tmp_path, "iperf3_b.json", "")
    r = parse_iperf3(path)
    assert not r.ok
    assert r.bits_per_second is None
    assert "empty" in r.error


def test_parse_iperf3_error_object(tmp_path):
    """iperf3 reports failures as a JSON object with an 'error' key."""
    path = _write(tmp_path, "iperf3_c.json",
                  json.dumps({"error": "unable to connect to server"}))
    r = parse_iperf3(path)
    assert not r.ok
    assert "unable to connect" in r.error
    assert r.bits_per_second is None


def test_parse_iperf3_malformed(tmp_path):
    path = _write(tmp_path, "iperf3_d.json", "{not json")
    r = parse_iperf3(path)
    assert not r.ok
    assert "malformed" in r.error


def test_parse_iperf3_missing_file():
    r = parse_iperf3("/nonexistent/iperf3_x.json")
    assert not r.ok and r.error == "missing"


# -------------------------------------------------------------------- ping
PING_TEXT = """\
PING 10.0.0.3 (10.0.0.3) 56(84) bytes of data.
[1792742000.100000] 64 bytes from 10.0.0.3: icmp_seq=1 ttl=64 time=4.21 ms
[1792742000.300000] 64 bytes from 10.0.0.3: icmp_seq=2 ttl=64 time=5.00 ms
[1792742000.500000] no answer yet for icmp_seq=3
[1792742000.700000] no answer yet for icmp_seq=4
[1792742000.900000] 64 bytes from 10.0.0.3: icmp_seq=5 ttl=64 time=9.50 ms
"""


def test_parse_ping_reads_replies_and_losses(tmp_path):
    path = _write(tmp_path, "ping_h1.txt", PING_TEXT)
    p = parse_ping(path)
    assert p.ok
    assert len(p.replies) == 3
    assert len(p.losses) == 2
    assert p.rtts_ms == [4.21, 5.00, 9.50]
    assert p.sent == 5
    assert p.loss_fraction == pytest.approx(2 / 5)


def test_parse_ping_empty_is_not_zero_loss(tmp_path):
    path = _write(tmp_path, "ping_empty.txt", "")
    p = parse_ping(path)
    assert not p.ok
    assert p.loss_fraction is None


def test_recovery_window_measures_from_the_failure(tmp_path):
    path = _write(tmp_path, "ping_h1.txt", PING_TEXT)
    p = parse_ping(path)
    # Failure just after the second reply.
    out = recovery_window(p, failure_t=1792742000.35)
    assert out["first_reply_after_failure_s"] == pytest.approx(0.55, abs=1e-6)
    assert out["lost_probes_in_outage"] == 2


def test_recovery_window_reports_unknown_when_never_recovered(tmp_path):
    text = (
        "[1792742000.100000] 64 bytes from 10.0.0.3: icmp_seq=1 ttl=64 time=4.0 ms\n"
        "[1792742000.300000] no answer yet for icmp_seq=2\n"
        "[1792742000.500000] no answer yet for icmp_seq=3\n"
    )
    path = _write(tmp_path, "ping_dead.txt", text)
    p = parse_ping(path)
    out = recovery_window(p, failure_t=1792742000.2)
    # Never recovered: recovery time is unknown, NOT zero and NOT the run length.
    assert out["first_reply_after_failure_s"] is None
    assert out["lost_probes_in_outage"] == 2


# ------------------------------------------------------------- flow dumps
DUMP_TEXT = """\
# t=1792742100.0
===== s1 =====
OFPST_FLOW reply (OF1.3) (xid=0x2):
 cookie=0x5d4e0001, duration=9.0s, table=0, n_packets=18, n_bytes=1710, idle_timeout=15, hard_timeout=120, send_flow_rem priority=10,tcp,nw_src=10.0.0.1,nw_dst=10.0.0.3,tp_src=1,tp_dst=5201 actions=output:3
 cookie=0x5d4e0001, duration=2.0s, table=0, n_packets=4, n_bytes=400, idle_timeout=5, hard_timeout=120, send_flow_rem priority=10,icmp,nw_src=10.0.0.1,nw_dst=10.0.0.3 actions=output:4
 cookie=0x0, duration=20.0s, table=0, n_packets=22, n_bytes=1848, priority=1,ipv6 actions=drop
 cookie=0x0, duration=20.0s, table=0, n_packets=10, n_bytes=676, priority=0 actions=CONTROLLER:65535
===== s2 =====
OFPST_FLOW reply (OF1.3) (xid=0x2):
 cookie=0x0, duration=20.0s, table=0, n_packets=1, n_bytes=90, priority=0 actions=CONTROLLER:65535
"""


def test_parse_flow_dump_extracts_rules(tmp_path):
    path = _write(tmp_path, "flows_0000_1.txt", DUMP_TEXT)
    dump = parse_flow_dump(path)
    assert dump.t == pytest.approx(1792742100.0)
    assert len(dump.rules) == 5
    assert {r.switch for r in dump.rules} == {"s1", "s2"}


def test_routed_filter_excludes_infrastructure_rules(tmp_path):
    """Table-miss and IPv6 rules are plumbing and must not count as flows."""
    path = _write(tmp_path, "flows_0000_1.txt", DUMP_TEXT)
    routed = parse_flow_dump(path).routed()
    assert len(routed) == 2
    assert all(r.priority >= 10 for r in routed)
    assert all(r.cookie == "0x5d4e0001" for r in routed)


def test_parse_flow_dump_reads_timeouts_and_outputs(tmp_path):
    path = _write(tmp_path, "flows_0000_1.txt", DUMP_TEXT)
    routed = parse_flow_dump(path).routed()
    assert sorted(r.idle_timeout for r in routed) == [5, 15]
    assert sorted(r.output for r in routed) == ["3", "4"]


def test_rule_install_time_from_duration(tmp_path):
    """Install time is what lets a rule be matched to the decision in force."""
    path = _write(tmp_path, "flows_0000_1.txt", DUMP_TEXT)
    dump = parse_flow_dump(path)
    rule = next(r for r in dump.routed() if r.idle_timeout == 5)
    assert rule.installed_at(dump.t) == pytest.approx(1792742098.0)


def test_table_miss_rule_identified_by_priority_zero(tmp_path):
    path = _write(tmp_path, "flows_0000_1.txt", DUMP_TEXT)
    misses = [r for r in parse_flow_dump(path).rules if r.priority == 0]
    assert len(misses) == 2
    assert all(r.output == "CONTROLLER" for r in misses)


# ------------------------------------------------------------------ stats
def test_summarise_reports_n_and_central_values():
    s = stats.summarise([1.0, 2.0, 3.0, 4.0, 5.0])
    assert s.n == 5
    assert s.mean == pytest.approx(3.0)
    assert s.median == pytest.approx(3.0)
    assert s.iqr == pytest.approx(2.0)
    assert s.ci95_low < s.mean < s.ci95_high


def test_summarise_excludes_none_but_counts_what_remains():
    """None means 'not measured'; n must reflect the real sample size."""
    s = stats.summarise([1.0, None, 3.0, None])
    assert s.n == 2
    assert s.mean == pytest.approx(2.0)


def test_summarise_of_nothing_is_empty_not_zero():
    s = stats.summarise([None, None])
    assert s.n == 0
    assert s.mean is None and s.median is None
    assert s.format_median_iqr() == "n/a"


def test_summarise_single_value_has_no_interval():
    s = stats.summarise([7.0])
    assert s.n == 1
    assert s.mean == pytest.approx(7.0)
    assert s.ci95_low is None  # one observation supports no interval
    assert "n=1" in s.format_mean_ci()


def test_t_critical_values_widen_for_small_samples():
    assert stats.t95(4) > stats.t95(20) > stats.t95(1000)
    assert stats.t95(4) == pytest.approx(2.776)


def test_ci_overlap_detects_separation():
    low = stats.summarise([1.0, 1.1, 0.9, 1.05, 0.95])
    high = stats.summarise([10.0, 10.1, 9.9, 10.05, 9.95])
    assert stats.ci_overlap(low, high) is False
    assert stats.ci_overlap(low, low) is True


def test_ci_overlap_unknown_without_intervals():
    assert stats.ci_overlap(stats.summarise([1.0]), stats.summarise([2.0])) is None


def test_percentile_interpolates():
    assert stats.percentile([1.0, 2.0, 3.0, 4.0], 0.5) == pytest.approx(2.5)
    assert stats.percentile([], 0.5) is None


def test_relative_change_guards_zero_baseline():
    assert stats.relative_change(0.0, 5.0) is None
    assert stats.relative_change(None, 5.0) is None
    assert stats.relative_change(10.0, 15.0) == pytest.approx(0.5)


# ----------------------------------------------------------------- helpers
def test_to_float_keeps_missing_distinct_from_zero():
    assert to_float("") is None
    assert to_float("abc") is None
    assert to_float("0") == 0.0
    assert to_float("1.5") == pytest.approx(1.5)


# ------------------------------------------------- diagnostic-run exclusion
def test_diagnostic_runs_are_identified():
    """Control runs are tagged in their directory name by the runner."""
    from analysis.parsers import is_diagnostic_run
    assert is_diagnostic_run("20261023-100228-C-heavy-seed1-diag-frozen")
    assert is_diagnostic_run("20261023-100411-C-heavy-seed1-diag-shuffled")
    assert not is_diagnostic_run("20261023-081048-C-heavy-seed1")
    assert not is_diagnostic_run("20261023-093239-C-heavy-seed5")


def test_discover_runs_excludes_diagnostics_by_default(tmp_path):
    """Frozen and shuffled runs must never enter the A/B/C comparison.

    They are complete, valid runs, but the agent in them is deliberately
    crippled or scrambled, so they are not samples of a variant's normal
    behaviour. When this was first run, three frozen runs pulled variant C's
    measured heavy-workload throughput from 13.68 down to 9.32 Mbit/s and
    inflated its sample count from 5 to 14.
    """
    import json as _json
    from analysis.parsers import discover_runs

    for name in ("20261023-081048-C-heavy-seed1",
                 "20261023-100228-C-heavy-seed1-diag-frozen",
                 "20261023-100411-C-heavy-seed1-diag-shuffled"):
        run_dir = tmp_path / name
        (run_dir / "raw").mkdir(parents=True)
        (run_dir / "config.json").write_text(
            _json.dumps({"variant": "C", "workload": "heavy", "seed": 1})
        )
        (run_dir / "manifest.json").write_text(_json.dumps({"complete": True}))

    normal = discover_runs(str(tmp_path))
    assert len(normal) == 1
    assert os.path.basename(normal[0].path).endswith("seed1")

    everything = discover_runs(str(tmp_path), include_diagnostics=True)
    assert len(everything) == 3


# --------------------------------------------------- duration comparability
def test_modal_duration_picks_the_dominant_length():
    from analysis.aggregate import modal_duration
    rows = [{"duration_s": 90}] * 5 + [{"duration_s": 30}]
    assert modal_duration(rows) == 90.0
    assert modal_duration([]) is None
    assert modal_duration([{"duration_s": None}]) is None


def test_off_duration_runs_are_separated_not_averaged_in():
    """A 30s dashboard run must not be averaged with the 90s matrix.

    Cumulative metrics (table misses, controller messages) scale with run
    length, and the RL variants get proportionally more or fewer learning
    steps, so mixing lengths compares different experiments.
    """
    from analysis.aggregate import modal_duration, split_by_duration
    rows = [
        {"run": "matrix-1", "duration_s": 90},
        {"run": "matrix-2", "duration_s": 90},
        {"run": "matrix-3", "duration_s": 90},
        {"run": "adhoc-ui", "duration_s": 30},
        {"run": "demo", "duration_s": 240},
    ]
    duration = modal_duration(rows)
    keep, drop = split_by_duration(rows, duration)
    assert duration == 90.0
    assert [r["run"] for r in keep] == ["matrix-1", "matrix-2", "matrix-3"]
    assert {r["run"] for r in drop} == {"adhoc-ui", "demo"}


def test_split_by_duration_is_a_noop_without_a_modal_duration():
    from analysis.aggregate import split_by_duration
    rows = [{"run": "a"}, {"run": "b"}]
    keep, drop = split_by_duration(rows, None)
    assert keep == rows and drop == []
