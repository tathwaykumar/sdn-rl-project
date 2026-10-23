"""Tests for telemetry derivation.

These matter because every reported number flows through ``derive()``.  The
cases below are the ones that would otherwise produce plausible-looking but
wrong results: counter resets, rule churn, a zero-length interval, and the
OpenFlow 1.3 subtlety that the table-miss entry is itself a matching rule.
"""

from __future__ import annotations

import os

import pytest

from telemetry import metrics
from topo import spec

CAP = 100.0


def _port(tx_bytes=0, tx_packets=0, tx_dropped=0, rx_bytes=0, rx_packets=0):
    return metrics.PortCounters(
        tx_packets=tx_packets, rx_packets=rx_packets,
        tx_bytes=tx_bytes, rx_bytes=rx_bytes,
        tx_dropped=tx_dropped, rx_dropped=0, tx_errors=0, rx_errors=0,
    )


def _sample(t, ports=None, tables=None, rules=None, installed=None, miss=None,
            controller=None):
    return metrics.RawSample(
        t=t,
        ports=ports or {},
        tables=tables or {},
        rules_installed=rules or {},
        pkts_installed=installed or {},
        pkts_miss=miss or {},
        controller=controller or metrics.ControllerCounters(),
    )


def test_non_positive_dt_is_invalid():
    """Two samples with the same timestamp must not divide by zero."""
    s = _sample(100.0)
    snap = metrics.derive(s, _sample(100.0), CAP)
    assert not snap.valid
    snap = metrics.derive(_sample(101.0), _sample(100.0), CAP)
    assert not snap.valid


def test_utilisation_is_bytes_over_capacity():
    key = ("s1", 3)  # path A first hop, 5 Mbit
    cap = spec.EGRESS_CAPACITY_BPS[key]
    # Push exactly half the link's capacity for one second.
    half_bytes = int(cap / 2 / 8)
    prev = _sample(0.0, ports={key: _port(tx_bytes=0)})
    cur = _sample(1.0, ports={key: _port(tx_bytes=half_bytes)})
    snap = metrics.derive(prev, cur, CAP)
    assert snap.path_util["A"] == pytest.approx(0.5, rel=1e-3)
    assert snap.max_link_util == pytest.approx(0.5, rel=1e-3)
    assert snap.valid


def test_path_utilisation_uses_worst_link_not_mean():
    """The bottleneck limits the path, so a mean would hide congestion."""
    k1, k2 = spec.PATH_EGRESS_KEYS["A"]
    cap1 = spec.EGRESS_CAPACITY_BPS[k1]
    prev = _sample(0.0, ports={k1: _port(), k2: _port()})
    cur = _sample(1.0, ports={
        k1: _port(tx_bytes=int(cap1 / 8)),  # saturated
        k2: _port(tx_bytes=0),              # idle
    })
    snap = metrics.derive(prev, cur, CAP)
    assert snap.path_util["A"] == pytest.approx(1.0, rel=1e-3)


def test_delivered_throughput_counts_only_host_facing_ports():
    """Core-link bytes are in transit, not delivered; counting both double-counts."""
    host_key = (spec.HOSTS[2].switch, spec.HOSTS[2].port)  # h3 access port
    core_key = ("s1", 3)
    prev = _sample(0.0, ports={host_key: _port(), core_key: _port()})
    cur = _sample(1.0, ports={
        host_key: _port(tx_bytes=125000),   # 1 Mbit delivered
        core_key: _port(tx_bytes=999999),
    })
    snap = metrics.derive(prev, cur, CAP)
    assert snap.delivered_bps == pytest.approx(1e6, rel=1e-6)


def test_loss_fraction_from_core_drops():
    key = ("s1", 3)
    prev = _sample(0.0, ports={key: _port(tx_packets=0, tx_dropped=0)})
    cur = _sample(1.0, ports={key: _port(tx_packets=90, tx_dropped=10)})
    snap = metrics.derive(prev, cur, CAP)
    assert snap.loss_fraction == pytest.approx(0.1)


def test_no_traffic_means_zero_loss_not_division_by_zero():
    key = ("s1", 3)
    prev = _sample(0.0, ports={key: _port()})
    cur = _sample(1.0, ports={key: _port()})
    snap = metrics.derive(prev, cur, CAP)
    assert snap.loss_fraction == 0.0
    assert snap.valid


def test_port_counter_reset_invalidates_interval():
    """A backwards counter must not become a huge negative rate."""
    key = ("s1", 3)
    prev = _sample(0.0, ports={key: _port(tx_bytes=10_000)})
    cur = _sample(1.0, ports={key: _port(tx_bytes=5_000)})
    snap = metrics.derive(prev, cur, CAP)
    assert not snap.valid
    assert "reset" in snap.note


def test_misses_come_from_priority_zero_counter():
    prev = _sample(0.0, tables={"s1": metrics.TableCounters(1, 100, 100)},
                   miss={"s1": 10})
    cur = _sample(1.0, tables={"s1": metrics.TableCounters(1, 200, 200)},
                  miss={"s1": 35})
    snap = metrics.derive(prev, cur, CAP)
    assert snap.table_misses == 25
    assert snap.miss_rate_per_s == pytest.approx(25.0)
    # hits = lookups - misses = 100 - 25
    assert snap.rule_hit_packets == 75
    assert snap.rule_hit_ratio == pytest.approx(0.75)


def test_hit_ratio_survives_rule_churn():
    """Installed-rule counters go backwards when rules expire; hits must not."""
    prev = _sample(0.0, tables={"s1": metrics.TableCounters(5, 1000, 1000)},
                   miss={"s1": 0}, installed={"s1": 900})
    cur = _sample(1.0, tables={"s1": metrics.TableCounters(3, 1500, 1500)},
                  miss={"s1": 20}, installed={"s1": 100})  # rules aged out
    snap = metrics.derive(prev, cur, CAP)
    assert snap.rule_hit_packets == 480      # 500 lookups - 20 misses
    assert snap.rule_hit_ratio == pytest.approx(480 / 500)
    assert snap.valid, "churn must not invalidate an otherwise good interval"
    assert "churned" in snap.note


def test_hit_ratio_is_none_when_nothing_was_looked_up():
    """Reporting 0.0 would claim a measured miss rate we do not have."""
    prev = _sample(0.0, tables={"s1": metrics.TableCounters(1, 50, 50)})
    cur = _sample(1.0, tables={"s1": metrics.TableCounters(1, 50, 50)})
    snap = metrics.derive(prev, cur, CAP)
    assert snap.rule_hit_ratio is None
    assert snap.as_row()["rule_hit_ratio"] == ""


def test_occupancy_excludes_nothing_and_is_capped():
    prev = _sample(0.0, rules={"s1": 0, "s2": 0})
    cur = _sample(1.0, rules={"s1": 50, "s2": 200})
    snap = metrics.derive(prev, cur, occupancy_cap_rules=100.0)
    assert snap.total_rules == 250
    # s1 = 0.5, s2 clipped to 1.0 -> mean 0.75
    assert snap.occupancy_fraction == pytest.approx(0.75)


def test_controller_rates_are_per_second():
    prev = _sample(0.0, controller=metrics.ControllerCounters(packet_in=0, flow_mod=0))
    cur = _sample(2.0, controller=metrics.ControllerCounters(packet_in=10, flow_mod=30))
    snap = metrics.derive(prev, cur, CAP)
    assert snap.packet_in_rate == pytest.approx(5.0)
    assert snap.flow_mod_rate == pytest.approx(15.0)
    assert snap.controller_msg_rate == pytest.approx(20.0)


def test_controller_counters_total_sums_all_fields():
    c = metrics.ControllerCounters(packet_in=1, flow_mod=2, packet_out=3,
                                   stats_request=4, stats_reply=5,
                                   flow_removed=6, port_status=7)
    assert c.total() == 28
    assert c.copy().total() == 28
    assert c.as_dict()["total"] == 28


def test_snapshot_row_has_every_declared_field():
    """A row missing a field would silently write a blank column."""
    snap = metrics.derive(_sample(0.0), _sample(1.0), CAP)
    row = snap.as_row()
    for field in metrics.SNAPSHOT_FIELDS:
        assert field in row, f"SNAPSHOT_FIELDS declares {field} but as_row() omits it"


def test_host_facing_and_core_egress_do_not_overlap():
    assert set(metrics.HOST_FACING_EGRESS) & set(metrics.CORE_EGRESS) == set()
    assert len(metrics.HOST_FACING_EGRESS) == len(spec.HOSTS)


def test_csv_writer_appends_header_once_and_flushes(tmp_path):
    path = os.path.join(tmp_path, "x.csv")
    w = metrics.CsvWriter(path, ("a", "b"))
    w.write({"a": 1, "b": 2})
    # Readable before close: the writer flushes every row so a killed run
    # still leaves usable data.
    assert open(path).read().splitlines() == ["a,b", "1,2"]
    w.close()

    w2 = metrics.CsvWriter(path, ("a", "b"))
    w2.write({"a": 3, "b": 4})
    w2.close()
    lines = open(path).read().splitlines()
    assert lines == ["a,b", "1,2", "3,4"], "header must not repeat on append"


def test_csv_writer_ignores_extra_keys(tmp_path):
    path = os.path.join(tmp_path, "y.csv")
    w = metrics.CsvWriter(path, ("a",))
    w.write({"a": 1, "unexpected": 2})
    w.close()
    assert open(path).read().splitlines() == ["a", "1"]
