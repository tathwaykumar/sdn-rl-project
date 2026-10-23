"""Tests for the topology spec.

These guard the invariants that silently invalidate experiments if broken: a
duplicated port number, a path that is not actually link-disjoint, or a reverse
path that does not retrace the forward one.
"""

from __future__ import annotations

import pytest

from topo import spec


def test_five_switches_four_hosts():
    assert len(spec.SWITCHES) == 5
    assert len(spec.HOSTS) == 4


def test_port_numbers_unique_per_switch():
    """A duplicated port would make two different links look like one."""
    seen = {}
    for h in spec.HOSTS:
        key = (h.switch, h.port)
        assert key not in seen, f"port clash at {key}: host {h.name} vs {seen.get(key)}"
        seen[key] = f"host {h.name}"
    for link in spec.CORE_LINKS:
        for key, who in (
            ((link.a, link.a_port), f"link to {link.b}"),
            ((link.b, link.b_port), f"link to {link.a}"),
        ):
            assert key not in seen, f"port clash at {key}: {who} vs {seen.get(key)}"
            seen[key] = who


def test_paths_are_link_disjoint():
    """The central topology claim: A and B share no link."""
    a = set(spec.PATH_LINKS["A"])
    b = set(spec.PATH_LINKS["B"])
    assert a & b == set(), f"paths share links: {a & b}"


def test_path_capacities_differ():
    """The experiment is only interesting if the paths are asymmetric."""
    assert spec.PATH_CAPACITY_BPS["A"] != spec.PATH_CAPACITY_BPS["B"]
    # A is the shorter path but the smaller pipe; that tension is the point.
    assert spec.path_hop_count("A") < spec.path_hop_count("B")
    assert spec.PATH_CAPACITY_BPS["A"] < spec.PATH_CAPACITY_BPS["B"]


@pytest.mark.parametrize("path", ["A", "B"])
def test_path_hops_reach_destination(path):
    hops = spec.path_hops("h1", "h3", path)
    # Last hop must egress on h3's access port at h3's edge switch.
    last_switch, last_port = hops[-1]
    assert last_switch == spec.HOST_BY_NAME["h3"].switch
    assert last_port == spec.HOST_BY_NAME["h3"].port
    # Hop count equals path length (intermediate switches + destination edge).
    assert len(hops) == spec.path_hop_count(path) + 1


@pytest.mark.parametrize("path", ["A", "B"])
def test_forward_and_reverse_use_same_links(path):
    """Reverse path must retrace the forward one, or RTT mixes two paths."""
    fwd = {sw for sw, _ in spec.path_hops("h1", "h3", path)}
    rev = {sw for sw, _ in spec.path_hops("h3", "h1", path)}
    assert fwd == rev


def test_path_hops_consistent_with_neighbour_ports():
    """Each intermediate hop's egress port must face the next switch."""
    for path, chain in spec.PATH_SWITCHES[("s1", "s4")].items():
        hops = spec.path_hops("h1", "h3", path)
        for i, (switch, port) in enumerate(hops[:-1]):
            expected = spec.NEIGHBOUR_PORT[(switch, chain[i + 1])]
            assert port == expected, f"{path} hop {switch}: {port} != {expected}"


def test_same_switch_pair_has_no_path_choice():
    assert spec.available_paths("h1", "h2") == ()
    assert spec.path_hops("h1", "h2", "A") == [("s1", spec.HOST_BY_NAME["h2"].port)]


def test_paths_avoiding_failed_link():
    failed = {frozenset({"s1", "s2"})}  # a link on path A
    assert spec.paths_avoiding(failed) == ("B",)
    failed_b = {frozenset({"s3", "s5"})}  # a link on path B
    assert spec.paths_avoiding(failed_b) == ("A",)
    assert set(spec.paths_avoiding(set())) == set(spec.PATH_NAMES)


def test_recovery_link_is_on_the_conventional_path():
    """Cutting it must force the baseline to reroute, or the test is vacuous."""
    link = frozenset(spec.RECOVERY_LINK)
    conventional = min(spec.PATH_NAMES, key=spec.path_hop_count)
    assert link in spec.PATH_LINKS[conventional]


def test_egress_capacity_covers_every_path_link():
    for path, keys in spec.PATH_EGRESS_KEYS.items():
        for key in keys:
            assert key in spec.EGRESS_CAPACITY_BPS, f"{path}: no capacity for {key}"


def test_path_egress_keys_match_path_switches():
    """PATH_EGRESS_KEYS is used for utilisation and must track the real path."""
    for path, chain in spec.PATH_SWITCHES[("s1", "s4")].items():
        derived = tuple(
            (sw, spec.NEIGHBOUR_PORT[(sw, chain[i + 1])])
            for i, sw in enumerate(chain[:-1])
        )
        assert spec.PATH_EGRESS_KEYS[path] == derived
