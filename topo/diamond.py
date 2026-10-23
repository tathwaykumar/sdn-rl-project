"""Mininet build script for the 5-switch / 4-host topology in ``topo.spec``.

Run directly for an interactive CLI session (requires root)::

    sudo python3 -m topo.diamond --cli

Normally the experiment runner imports :func:`build_network` instead.

Port numbers are passed explicitly to ``addLink`` so that the datapath port
numbering matches ``topo.spec`` exactly.  Without this, Mininet would number
ports in link-creation order and the controller's static port map would be
silently wrong -- the kind of bug that produces a working ping and a nonsense
routing experiment.
"""

from __future__ import annotations

import argparse
import sys

from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSKernelSwitch, RemoteController

from topo import spec


def build_network(
    controller_ip: str = spec.CONTROLLER_IP,
    controller_port: int = spec.CONTROLLER_PORT,
    autostart: bool = True,
) -> Mininet:
    """Construct the topology wired to a remote OpenFlow 1.3 controller."""
    net = Mininet(
        controller=None,
        switch=OVSKernelSwitch,
        link=TCLink,
        autoSetMacs=False,  # we set MACs explicitly, to match topo.spec
        autoStaticArp=False,  # ARP must traverse the controller; see base_app
        build=False,
        cleanup=True,
    )

    controller = net.addController(
        "c0",
        controller=RemoteController,
        ip=controller_ip,
        port=controller_port,
    )

    switches = {}
    for name in spec.SWITCHES:
        # protocols= pins the switch to OpenFlow 1.3.  Without it OVS 3.7
        # negotiates OF1.0 by default and every OF1.3-only message we send
        # (table-miss with an explicit instruction set, meters, etc.) fails.
        switches[name] = net.addSwitch(
            name,
            cls=OVSKernelSwitch,
            dpid=f"{spec.DPID_BY_SWITCH[name]:016x}",
            protocols="OpenFlow13",
        )

    hosts = {}
    for h in spec.HOSTS:
        hosts[h.name] = net.addHost(h.name, ip=f"{h.ip}/24", mac=h.mac)

    # Host access links: over-provisioned so the core is always the bottleneck.
    for h in spec.HOSTS:
        net.addLink(
            hosts[h.name],
            switches[h.switch],
            port1=0,
            port2=h.port,
            bw=spec.HOST_LINK_BW_MBIT,
            delay=f"{spec.HOST_LINK_DELAY_MS}ms",
        )

    # Core links with the asymmetric capacity/delay that makes the routing
    # decision non-trivial.
    for link in spec.CORE_LINKS:
        net.addLink(
            switches[link.a],
            switches[link.b],
            port1=link.a_port,
            port2=link.b_port,
            bw=link.bw_mbit,
            delay=f"{link.delay_ms}ms",
            max_queue_size=spec.CORE_LINK_QUEUE_PKTS,
            use_htb=True,
        )

    net.build()
    if autostart:
        controller.start()
        for name in spec.SWITCHES:
            switches[name].start([controller])
    return net


def verify_port_mapping(net: Mininet) -> list[str]:
    """Check live Mininet port numbers against ``topo.spec``.

    Returns a list of human-readable mismatches; empty means the running
    topology matches the spec the controller is using.  The experiment runner
    calls this before every run and aborts on mismatch, because a silent port
    mismatch invalidates all routing results.
    """
    problems: list[str] = []

    for h in spec.HOSTS:
        host = net.get(h.name)
        switch = net.get(h.switch)
        intf = host.defaultIntf()
        peer = intf.link.intf1 if intf.link.intf2 == intf else intf.link.intf2
        actual = switch.ports.get(peer)
        if actual != h.port:
            problems.append(
                f"host {h.name}: expected {h.switch} port {h.port}, got {actual}"
            )

    for link in spec.CORE_LINKS:
        sw_a, sw_b = net.get(link.a), net.get(link.b)
        found = None
        for intf in sw_a.intfList():
            if intf.link is None:
                continue
            other = intf.link.intf1 if intf.link.intf2 == intf else intf.link.intf2
            if other.node == sw_b:
                found = (sw_a.ports.get(intf), sw_b.ports.get(other))
                break
        if found is None:
            problems.append(f"core link {link.a}<->{link.b}: not present")
        elif found != (link.a_port, link.b_port):
            problems.append(
                f"core link {link.a}<->{link.b}: expected ports "
                f"{(link.a_port, link.b_port)}, got {found}"
            )

    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cli", action="store_true", help="drop into Mininet CLI")
    parser.add_argument("--controller-ip", default=spec.CONTROLLER_IP)
    parser.add_argument("--controller-port", type=int, default=spec.CONTROLLER_PORT)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="build, check port mapping, print result, tear down",
    )
    args = parser.parse_args()

    setLogLevel("info")
    net = build_network(args.controller_ip, args.controller_port)
    try:
        problems = verify_port_mapping(net)
        if problems:
            print("PORT MAPPING MISMATCH:")
            for p in problems:
                print("  " + p)
        else:
            print("port mapping matches topo.spec")

        if args.verify_only:
            return 1 if problems else 0

        if args.cli:
            from mininet.cli import CLI

            CLI(net)
        return 1 if problems else 0
    finally:
        net.stop()


if __name__ == "__main__":
    sys.exit(main())
