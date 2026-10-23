"""Single source of truth for the experimental topology.

Both the Mininet build script (``topo/diamond.py``) and the OpenFlow controller
(``controller/base_app.py``) import this module, so there is exactly one place
where port numbers, addresses and candidate paths are defined.  Mininet is given
explicit port numbers when links are created, which keeps datapath port
numbering deterministic and lets the controller use a static port map instead of
running LLDP topology discovery.  Discovery is a source of flakiness we do not
need: the topology is fixed and known.

Topology -- 5 switches, 4 hosts, two link-disjoint s1<->s4 paths::

    h1 --.                                  .-- h3
          +-- s1 ------- s2 ------- s4 -----+
    h2 --'     |                    |        '-- h4
               '--- s3 ---- s5 -----'

  Path "A":  s1 -> s2 -> s4         2 hops, low delay,  LOW capacity  (5 Mbit)
  Path "B":  s1 -> s3 -> s5 -> s4   3 hops, high delay, HIGH capacity (10 Mbit)

The two paths share no link, so they are link-disjoint and a failure on one
leaves the other usable (used by the recovery experiment).

The asymmetry is deliberate and is the heart of the experiment.  Hop-count
shortest-path routing always prefers A, so under load it congests A while B
sits idle.  Under *light* load A is genuinely better (lower RTT).  So the
optimal choice is load-dependent, which is exactly what a state-conditioned
policy can capture and a static policy cannot.  See docs/ASSUMPTIONS.md.
"""

from __future__ import annotations

from typing import Dict, List, NamedTuple, Sequence, Tuple

# OpenFlow 1.3 controller endpoint, shared by Mininet and the launcher.
CONTROLLER_IP = "127.0.0.1"
CONTROLLER_PORT = 6653

SWITCHES: Tuple[str, ...] = ("s1", "s2", "s3", "s4", "s5")

# Mininet names switch sN with datapath id N, so this mapping is just an
# explicit statement of that convention rather than a new assumption.
DPID_BY_SWITCH: Dict[str, int] = {name: int(name[1:]) for name in SWITCHES}
SWITCH_BY_DPID: Dict[int, str] = {v: k for k, v in DPID_BY_SWITCH.items()}


class Host(NamedTuple):
    name: str
    ip: str
    mac: str
    switch: str
    port: int


class Link(NamedTuple):
    a: str
    a_port: int
    b: str
    b_port: int
    bw_mbit: float
    delay_ms: float


HOSTS: Tuple[Host, ...] = (
    Host("h1", "10.0.0.1", "00:00:00:00:00:01", "s1", 1),
    Host("h2", "10.0.0.2", "00:00:00:00:00:02", "s1", 2),
    Host("h3", "10.0.0.3", "00:00:00:00:00:03", "s4", 1),
    Host("h4", "10.0.0.4", "00:00:00:00:00:04", "s4", 2),
)

HOST_BY_NAME: Dict[str, Host] = {h.name: h for h in HOSTS}
HOST_BY_IP: Dict[str, Host] = {h.ip: h for h in HOSTS}
HOST_BY_MAC: Dict[str, Host] = {h.mac: h for h in HOSTS}

# Host access links are deliberately over-provisioned (100 Mbit, no added
# delay) so that the core links below are always the bottleneck.  If the access
# link were the bottleneck the routing decision would not matter.
HOST_LINK_BW_MBIT = 100.0
HOST_LINK_DELAY_MS = 0.0

# Core links.  Ports are explicit; see module docstring.
CORE_LINKS: Tuple[Link, ...] = (
    # ---- Path A: short, low capacity -------------------------------------
    Link("s1", 3, "s2", 1, bw_mbit=5.0, delay_ms=1.0),
    Link("s2", 2, "s4", 3, bw_mbit=5.0, delay_ms=1.0),
    # ---- Path B: long, high capacity -------------------------------------
    Link("s1", 4, "s3", 1, bw_mbit=10.0, delay_ms=2.0),
    Link("s3", 2, "s5", 1, bw_mbit=10.0, delay_ms=2.0),
    Link("s5", 2, "s4", 4, bw_mbit=10.0, delay_ms=2.0),
)

# Queue depth for TCLink.  Small enough that congestion actually produces loss
# and bufferbloat rather than unbounded queueing, which would turn every
# overload into pure latency and never show up as packet loss.
CORE_LINK_QUEUE_PKTS = 100

# --------------------------------------------------------------------------
# Derived lookups
# --------------------------------------------------------------------------

def _build_neighbour_ports() -> Dict[Tuple[str, str], int]:
    """Map (switch, neighbour_switch) -> local egress port."""
    ports: Dict[Tuple[str, str], int] = {}
    for link in CORE_LINKS:
        ports[(link.a, link.b)] = link.a_port
        ports[(link.b, link.a)] = link.b_port
    return ports


#: (switch, neighbour) -> egress port on ``switch`` facing ``neighbour``
NEIGHBOUR_PORT: Dict[Tuple[str, str], int] = _build_neighbour_ports()

#: Candidate paths as switch sequences, keyed by (ingress edge, egress edge).
#: Only s1<->s4 has a choice; that is where all inter-edge traffic flows.
PATH_SWITCHES: Dict[Tuple[str, str], Dict[str, Tuple[str, ...]]] = {
    ("s1", "s4"): {
        "A": ("s1", "s2", "s4"),
        "B": ("s1", "s3", "s5", "s4"),
    },
    ("s4", "s1"): {
        "A": ("s4", "s2", "s1"),
        "B": ("s4", "s5", "s3", "s1"),
    },
}

#: Stable ordering of path names, used as the agent's routing action space.
PATH_NAMES: Tuple[str, ...] = ("A", "B")

#: Path name -> the core links it traverses, as (switch, port) egress keys.
#: Used to compute per-path utilisation from port counters.
PATH_EGRESS_KEYS: Dict[str, Tuple[Tuple[str, int], ...]] = {
    "A": (("s1", 3), ("s2", 2)),
    "B": (("s1", 4), ("s3", 2), ("s5", 2)),
}

#: Path name -> the switch-pair links it traverses, direction-independent.
#: A link failure takes down both of its ports, so path availability is checked
#: against unordered switch pairs rather than egress ports.
PATH_LINKS: Dict[str, Tuple[frozenset, ...]] = {
    "A": (frozenset({"s1", "s2"}), frozenset({"s2", "s4"})),
    "B": (
        frozenset({"s1", "s3"}),
        frozenset({"s3", "s5"}),
        frozenset({"s5", "s4"}),
    ),
}

#: (switch, port) -> the link it belongs to, for mapping port-down to a link.
PORT_TO_LINK: Dict[Tuple[str, int], frozenset] = {}
for _l in CORE_LINKS:
    _pair = frozenset({_l.a, _l.b})
    PORT_TO_LINK[(_l.a, _l.a_port)] = _pair
    PORT_TO_LINK[(_l.b, _l.b_port)] = _pair
del _l, _pair


def paths_avoiding(failed_links) -> Tuple[str, ...]:
    """Path names that use none of ``failed_links`` (an iterable of frozensets)."""
    failed = set(failed_links)
    return tuple(
        name
        for name, links in PATH_LINKS.items()
        if not any(link in failed for link in links)
    )


def path_hop_count(path_name: str) -> int:
    """Hop count, used by the conventional shortest-path baseline."""
    return len(PATH_SWITCHES[("s1", "s4")][path_name]) - 1


#: Path name -> bottleneck capacity in bits/sec (the min over its links).
PATH_CAPACITY_BPS: Dict[str, float] = {
    "A": 5.0 * 1e6,
    "B": 10.0 * 1e6,
}

#: Egress (switch, port) -> link capacity bits/sec, for utilisation maths.
EGRESS_CAPACITY_BPS: Dict[Tuple[str, int], float] = {}
for _link in CORE_LINKS:
    EGRESS_CAPACITY_BPS[(_link.a, _link.a_port)] = _link.bw_mbit * 1e6
    EGRESS_CAPACITY_BPS[(_link.b, _link.b_port)] = _link.bw_mbit * 1e6
for _host in HOSTS:
    EGRESS_CAPACITY_BPS[(_host.switch, _host.port)] = HOST_LINK_BW_MBIT * 1e6
del _link, _host

#: The link cut by the recovery experiment.  It is on path A, which is the path
#: conventional routing always chooses, so cutting it forces a reroute onto B.
RECOVERY_LINK: Tuple[str, str] = ("s1", "s2")


def host_edge_switch(host_name: str) -> str:
    return HOST_BY_NAME[host_name].switch


def path_hops(src_host: str, dst_host: str, path_name: str) -> List[Tuple[str, int]]:
    """Return the (switch, egress_port) rules needed to carry src->dst.

    The final element egresses to the destination host's access port, so the
    returned list is the complete set of forwarding rules for one direction of
    one flow.  Raises KeyError if ``path_name`` is not valid for this pair.
    """
    src = HOST_BY_NAME[src_host]
    dst = HOST_BY_NAME[dst_host]

    if src.switch == dst.switch:
        # Same edge switch: one rule, straight out of the access port.
        return [(src.switch, dst.port)]

    chain = PATH_SWITCHES[(src.switch, dst.switch)][path_name]
    hops: List[Tuple[str, int]] = []
    for i, switch in enumerate(chain[:-1]):
        hops.append((switch, NEIGHBOUR_PORT[(switch, chain[i + 1])]))
    hops.append((chain[-1], dst.port))
    return hops


def available_paths(src_host: str, dst_host: str) -> Tuple[str, ...]:
    """Path names usable for this host pair (empty tuple means no choice)."""
    src = HOST_BY_NAME[src_host]
    dst = HOST_BY_NAME[dst_host]
    if src.switch == dst.switch:
        return ()
    return tuple(PATH_SWITCHES[(src.switch, dst.switch)].keys())


def inter_edge_host_pairs() -> List[Tuple[str, str]]:
    """Host pairs whose traffic crosses the core and therefore has a choice."""
    pairs = []
    for src in HOSTS:
        for dst in HOSTS:
            if src.name != dst.name and src.switch != dst.switch:
                pairs.append((src.name, dst.name))
    return pairs


def describe() -> str:
    """Human-readable summary, recorded into every run directory."""
    lines = [
        f"switches: {len(SWITCHES)} {SWITCHES}",
        f"hosts:    {len(HOSTS)} " + ", ".join(f"{h.name}={h.ip}@{h.switch}:{h.port}" for h in HOSTS),
        "core links:",
    ]
    for link in CORE_LINKS:
        lines.append(
            f"  {link.a}:{link.a_port} <-> {link.b}:{link.b_port}  "
            f"bw={link.bw_mbit}Mbit delay={link.delay_ms}ms"
        )
    lines.append("candidate paths (s1->s4):")
    for name, chain in PATH_SWITCHES[("s1", "s4")].items():
        cap = PATH_CAPACITY_BPS[name] / 1e6
        lines.append(f"  {name}: {' -> '.join(chain)}  bottleneck={cap}Mbit")
    return "\n".join(lines)


if __name__ == "__main__":  # pragma: no cover - manual inspection helper
    print(describe())
