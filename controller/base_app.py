"""Shared OpenFlow 1.3 application logic for all three experiment variants.

Everything that is *not* the policy lives here: switch bring-up, ARP proxying,
reactive flow installation along a chosen path, statistics polling, link-failure
handling and telemetry.  The three variants differ only in
:meth:`BaseSdnApp.policy_step`, which returns the path and idle timeout to use.
Keeping it that way is what makes A/B/C a controlled comparison instead of three
different controllers that happen to share a topology.

Decision cadence
----------------
The agent does **not** choose per packet.  Once per polling interval the
controller reads the switch counters, derives a state and a reward, updates the
policy, and picks one (path, idle-timeout) action.  Every flow set up during the
next interval uses that action.  This gives a well-defined MDP -- state s_t,
action a_t applied over the whole interval, scalar reward r_t measured from that
interval, next state s_{t+1} -- instead of thousands of per-packet actions whose
individual contributions to a throughput measurement could never be separated.

Rule priorities
---------------
``0`` is the table-miss entry, ``1`` is infrastructure (IPv6 discard), and
``10`` and above are routed flows.  The telemetry counts only priority >= 10 as
occupancy and hits, so infrastructure rules never inflate the flow-table
numbers we report.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Dict, List, Optional, Sequence, Set, Tuple

from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import (
    CONFIG_DISPATCHER,
    DEAD_DISPATCHER,
    MAIN_DISPATCHER,
    set_ev_cls,
)
from os_ken.lib import hub
from os_ken.lib.packet import arp, ethernet, ether_types, ipv4, packet, tcp, udp
from os_ken.ofproto import ofproto_v1_3

from agent.reward import RewardNormalisers, RewardTerms, RewardWeights, compute_reward
from agent.state import State, discretise
from telemetry import metrics
from topo import spec

TABLE_MISS_PRIORITY = 0
INFRA_PRIORITY = 1
FLOW_PRIORITY = 10

#: Cookie stamped on routed (policy-installed) flows only.
#:
#: This exists because an OpenFlow 1.3 ``OFPFC_DELETE`` with a wildcard match
#: is *non-strict*: it ignores the priority field and deletes every matching
#: entry. Flushing "priority=10" that way also removed the table-miss and
#: infrastructure rules, after which no packet could reach the controller and
#: the network never recovered from a link failure. Deleting by cookie targets
#: exactly the rules the policy installed and leaves the plumbing intact.
ROUTED_COOKIE = 0x5D4E0001
COOKIE_MASK_EXACT = 0xFFFFFFFFFFFFFFFF

#: Protocols we install 5-tuple rules for. Others get a 3-tuple rule.
L4_TCP = 6
L4_UDP = 17
L4_ICMP = 1


def load_run_config() -> dict:
    """Read the run configuration written by the experiment runner.

    The path comes from ``SDNRL_CONFIG``.  Failing loudly when it is absent is
    deliberate: a controller started without a run config would silently write
    telemetry nowhere, and the run would look successful but produce no data.
    """
    path = os.environ.get("SDNRL_CONFIG")
    if not path:
        raise RuntimeError(
            "SDNRL_CONFIG is not set; start this app via experiments/run_experiment.py"
        )
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


class BaseSdnApp(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    #: Overridden by each variant; recorded in every telemetry row.
    VARIANT = "base"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cfg = load_run_config()

        self.run_dir: str = self.cfg["run_dir"]
        self.raw_dir: str = self.cfg.get("raw_dir", os.path.join(self.run_dir, "raw"))
        self.poll_interval: float = float(self.cfg.get("poll_interval_s", 1.0))
        self.fixed_idle_timeout: int = int(self.cfg.get("fixed_idle_timeout_s", 15))
        self.hard_timeout: int = int(self.cfg.get("hard_timeout_s", 120))
        self.occupancy_cap: float = float(self.cfg.get("occupancy_cap_rules", 100))

        self.weights = RewardWeights(**self.cfg.get("reward_weights", {}))
        self.normalisers = RewardNormalisers(**self.cfg.get("reward_normalisers", {}))

        self.writers = metrics.RunWriters(self.raw_dir)
        self.counters = metrics.ControllerCounters()

        self.datapaths: Dict[int, object] = {}
        self.failed_links: Set[frozenset] = set()

        # os-ken's hub defaults to HUB_TYPE "native", i.e. real OS threads
        # rather than eventlet greenthreads. Event handlers run on the app's
        # event-loop thread while the polling loop runs on its own thread, and
        # both touch the counters and accumulators below. Without this lock,
        # copying an accumulator could raise "dictionary changed size during
        # iteration" mid-run, and counter increments could be lost.
        self._lock = threading.Lock()

        # Accumulators filled by stats replies during each polling interval.
        self._acc_ports: Dict[Tuple[str, int], metrics.PortCounters] = {}
        self._acc_tables: Dict[str, metrics.TableCounters] = {}
        self._acc_rules: Dict[str, int] = {}
        self._acc_pkts_installed: Dict[str, int] = {}
        self._acc_pkts_miss: Dict[str, int] = {}

        self._prev_sample: Optional[metrics.RawSample] = None
        self._pending: Optional[Tuple[State, str, int, object]] = None
        self._running = True
        self._step = 0

        # Current action. Starts at the conventional choice (fewest hops) so
        # that every variant begins from the same behaviour and any divergence
        # is attributable to the policy rather than to a different start point.
        self.current_path: str = min(spec.PATH_NAMES, key=spec.path_hop_count)
        self.current_idle: int = self.fixed_idle_timeout

        self.logger.info(
            "variant=%s run_dir=%s poll=%.2fs idle=%ds",
            self.VARIANT, self.run_dir, self.poll_interval, self.fixed_idle_timeout,
        )
        self.writers.event("controller_start", f"variant={self.VARIANT}")

    def start(self):
        """Spawn the polling loop.

        This is deliberately not done in ``__init__``: subclasses build their
        agent after ``super().__init__()`` returns, and a monitor thread started
        in the constructor could call ``policy_step`` before that agent exists.
        ``start()`` is the documented post-initialisation hook, so by the time it
        runs the variant is fully constructed.
        """
        super().start()
        self._monitor_thread = hub.spawn(self._monitor_loop)
        self.threads.append(self._monitor_thread)
        return None

    def _bump(self, field: str, n: int = 1) -> None:
        """Increment a controller counter under the lock."""
        with self._lock:
            setattr(self.counters, field, getattr(self.counters, field) + n)

    # ------------------------------------------------------------------ hooks
    def policy_step(
        self,
        state: State,
        usable_paths: Sequence[str],
        snapshot: metrics.Snapshot,
        reward: Optional[RewardTerms],
    ) -> Tuple[str, int, Dict[str, object]]:
        """Return (path, idle_timeout, extra log fields) for the next interval.

        Called once per polling interval.  ``reward`` is None on the first
        interval, when no action has yet been applied to measure.
        """
        raise NotImplementedError

    def on_shutdown(self) -> None:
        """Variant hook for persisting learned state."""

    # ------------------------------------------------- switch lifecycle
    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def _state_change(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            if dp.id not in self.datapaths:
                self.datapaths[dp.id] = dp
                name = spec.SWITCH_BY_DPID.get(dp.id, f"dpid{dp.id}")
                self.logger.info("switch connected: %s (dpid=%s)", name, dp.id)
                self.writers.event("switch_connected", name)
        elif ev.state == DEAD_DISPATCHER and dp.id in self.datapaths:
            del self.datapaths[dp.id]
            self.writers.event(
                "switch_disconnected", spec.SWITCH_BY_DPID.get(dp.id, str(dp.id))
            )

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def _switch_features(self, ev):
        dp = ev.msg.datapath
        parser, ofp = dp.ofproto_parser, dp.ofproto

        # Table-miss: everything unmatched goes to the controller in full.
        # OFPCML_NO_BUFFER avoids relying on switch buffering, which OVS
        # implements inconsistently and which would make PacketOut unreliable.
        self._send_flow_mod(
            dp,
            priority=TABLE_MISS_PRIORITY,
            match=parser.OFPMatch(),
            actions=[parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)],
            idle_timeout=0,
            hard_timeout=0,
        )

        # Discard IPv6. Mininet hosts emit router solicitations and multicast
        # listener reports; without this they become PacketIns and pollute the
        # controller-load and table-miss measurements with traffic that has
        # nothing to do with the experiment.
        self._send_flow_mod(
            dp,
            priority=INFRA_PRIORITY,
            match=parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IPV6),
            actions=[],  # no actions == drop
            idle_timeout=0,
            hard_timeout=0,
        )

        self.logger.info("installed base rules on %s", spec.SWITCH_BY_DPID.get(dp.id))

    # ------------------------------------------------------- flow installation
    def _send_flow_mod(
        self,
        dp,
        priority: int,
        match,
        actions: List,
        idle_timeout: int,
        hard_timeout: int,
        send_flow_rem: bool = False,
        cookie: int = 0,
    ) -> None:
        parser, ofp = dp.ofproto_parser, dp.ofproto
        inst = (
            [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
            if actions
            else []
        )
        flags = ofp.OFPFF_SEND_FLOW_REM if send_flow_rem else 0
        mod = parser.OFPFlowMod(
            datapath=dp,
            priority=priority,
            match=match,
            instructions=inst,
            idle_timeout=idle_timeout,
            hard_timeout=hard_timeout,
            flags=flags,
            cookie=cookie,
        )
        dp.send_msg(mod)
        self._bump("flow_mod")

    def _flow_match(self, dp, src_ip: str, dst_ip: str, proto: int,
                    sport: Optional[int], dport: Optional[int]):
        parser = dp.ofproto_parser
        kw: Dict[str, object] = {
            "eth_type": ether_types.ETH_TYPE_IP,
            "ipv4_src": src_ip,
            "ipv4_dst": dst_ip,
            "ip_proto": proto,
        }
        if proto == L4_TCP and sport is not None and dport is not None:
            kw["tcp_src"] = sport
            kw["tcp_dst"] = dport
        elif proto == L4_UDP and sport is not None and dport is not None:
            kw["udp_src"] = sport
            kw["udp_dst"] = dport
        return parser.OFPMatch(**kw)

    def _install_path(
        self,
        src_host: str,
        dst_host: str,
        path_name: str,
        proto: int,
        sport: Optional[int],
        dport: Optional[int],
        idle_timeout: int,
    ) -> int:
        """Install forwarding rules for one direction. Returns rules installed.

        Rules go in from the far end backwards, so that by the time the ingress
        switch has a rule the rest of the path already does.  Installing ingress
        first would let packets race ahead of the downstream rules and generate
        extra table misses that we would then measure as controller load the
        policy did not actually cause.
        """
        src = spec.HOST_BY_NAME[src_host]
        dst = spec.HOST_BY_NAME[dst_host]
        hops = spec.path_hops(src_host, dst_host, path_name)

        installed = 0
        for switch_name, out_port in reversed(hops):
            dp = self.datapaths.get(spec.DPID_BY_SWITCH[switch_name])
            if dp is None:
                self.logger.warning("no datapath for %s; skipping rule", switch_name)
                continue
            match = self._flow_match(dp, src.ip, dst.ip, proto, sport, dport)
            self._send_flow_mod(
                dp,
                priority=FLOW_PRIORITY,
                match=match,
                actions=[dp.ofproto_parser.OFPActionOutput(out_port)],
                idle_timeout=idle_timeout,
                hard_timeout=self.hard_timeout,
                send_flow_rem=True,
                cookie=ROUTED_COOKIE,
            )
            installed += 1
        return installed

    # ------------------------------------------------------------- packet in
    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def _packet_in(self, ev):
        self._bump("packet_in")
        msg = ev.msg
        dp = msg.datapath
        in_port = msg.match.get("in_port")

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return

        if eth.ethertype == ether_types.ETH_TYPE_ARP:
            self._handle_arp(dp, in_port, pkt, eth)
            return
        if eth.ethertype == ether_types.ETH_TYPE_IP:
            self._handle_ip(dp, in_port, msg, pkt)
            return
        # Anything else is outside the experiment; dropping it silently keeps
        # the measurements clean.

    def _handle_arp(self, dp, in_port, pkt, eth) -> None:
        """Answer ARP from the topology spec instead of flooding.

        The topology contains a loop (two disjoint s1<->s4 paths), so flooding
        broadcast traffic would produce a storm.  Since every host's IP and MAC
        is known from ``topo.spec``, the controller can answer authoritatively.
        """
        req = pkt.get_protocol(arp.arp)
        if req is None or req.opcode != arp.ARP_REQUEST:
            return
        target = spec.HOST_BY_IP.get(req.dst_ip)
        if target is None:
            return

        reply = packet.Packet()
        reply.add_protocol(
            ethernet.ethernet(
                ethertype=ether_types.ETH_TYPE_ARP,
                dst=eth.src,
                src=target.mac,
            )
        )
        reply.add_protocol(
            arp.arp(
                opcode=arp.ARP_REPLY,
                src_mac=target.mac,
                src_ip=target.ip,
                dst_mac=req.src_mac,
                dst_ip=req.src_ip,
            )
        )
        reply.serialize()

        parser, ofp = dp.ofproto_parser, dp.ofproto
        dp.send_msg(
            parser.OFPPacketOut(
                datapath=dp,
                buffer_id=ofp.OFP_NO_BUFFER,
                in_port=ofp.OFPP_CONTROLLER,
                actions=[parser.OFPActionOutput(in_port)],
                data=reply.data,
            )
        )
        self._bump("packet_out")

    def _handle_ip(self, dp, in_port, msg, pkt) -> None:
        ip = pkt.get_protocol(ipv4.ipv4)
        if ip is None:
            return
        src = spec.HOST_BY_IP.get(ip.src)
        dst = spec.HOST_BY_IP.get(ip.dst)
        if src is None or dst is None:
            return

        proto = ip.proto
        sport = dport = None
        if proto == L4_TCP:
            seg = pkt.get_protocol(tcp.tcp)
            if seg is not None:
                sport, dport = seg.src_port, seg.dst_port
        elif proto == L4_UDP:
            seg = pkt.get_protocol(udp.udp)
            if seg is not None:
                sport, dport = seg.src_port, seg.dst_port

        usable = self.usable_paths()
        path = self.current_path if self.current_path in usable else (
            usable[0] if usable else self.current_path
        )
        idle = self.current_idle

        # Install both directions with the same path. A real controller sets up
        # the flow pair together; doing so also keeps forward and reverse
        # traffic symmetric, without which RTT measurements would mix two
        # different paths and be uninterpretable.
        self._install_path(src.name, dst.name, path, proto, sport, dport, idle)
        self._install_path(dst.name, src.name, path, proto, dport, sport, idle)

        # Forward the packet that triggered this, so the first packet of a flow
        # is not dropped (which TCP would see as loss and back off from).
        hops = spec.path_hops(src.name, dst.name, path)
        out_port = None
        this_switch = spec.SWITCH_BY_DPID.get(dp.id)
        for switch_name, port in hops:
            if switch_name == this_switch:
                out_port = port
                break
        if out_port is None:
            return

        parser, ofp = dp.ofproto_parser, dp.ofproto
        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        dp.send_msg(
            parser.OFPPacketOut(
                datapath=dp,
                buffer_id=msg.buffer_id,
                in_port=in_port,
                actions=[parser.OFPActionOutput(out_port)],
                data=data,
            )
        )
        self._bump("packet_out")

    @set_ev_cls(ofp_event.EventOFPFlowRemoved, MAIN_DISPATCHER)
    def _flow_removed(self, ev):
        """Record rule expiry.

        ``duration_sec`` on an idle-timeout removal is direct, switch-reported
        evidence of the idle timeout that was actually in force -- which is how
        we show variant C's timeout actions reached the dataplane rather than
        just the log.
        """
        self._bump("flow_removed")
        msg = ev.msg
        reason = {
            msg.datapath.ofproto.OFPRR_IDLE_TIMEOUT: "idle_timeout",
            msg.datapath.ofproto.OFPRR_HARD_TIMEOUT: "hard_timeout",
            msg.datapath.ofproto.OFPRR_DELETE: "delete",
            msg.datapath.ofproto.OFPRR_GROUP_DELETE: "group_delete",
        }.get(msg.reason, str(msg.reason))
        if msg.priority >= FLOW_PRIORITY:
            self.writers.event(
                "flow_removed",
                f"switch={spec.SWITCH_BY_DPID.get(msg.datapath.id)} reason={reason} "
                f"idle_timeout={msg.idle_timeout} duration_s={msg.duration_sec} "
                f"packets={msg.packet_count}",
            )

    # ------------------------------------------------------- link failure
    @set_ev_cls(ofp_event.EventOFPPortStatus, MAIN_DISPATCHER)
    def _port_status(self, ev):
        self._bump("port_status")
        msg = ev.msg
        dp = msg.datapath
        ofp = dp.ofproto
        switch_name = spec.SWITCH_BY_DPID.get(dp.id)
        port_no = msg.desc.port_no
        key = (switch_name, port_no)
        link = spec.PORT_TO_LINK.get(key)
        if link is None:
            return

        down = bool(msg.desc.state & ofp.OFPPS_LINK_DOWN)
        if msg.reason == ofp.OFPPR_DELETE:
            down = True

        before = set(self.failed_links)
        if down:
            self.failed_links.add(link)
        else:
            self.failed_links.discard(link)

        if self.failed_links != before:
            state = "down" if down else "up"
            usable = self.usable_paths()
            self.logger.info("link %s %s; usable paths now %s",
                             sorted(link), state, usable)
            self.writers.event(
                "link_state_change",
                f"link={'-'.join(sorted(link))} state={state} usable={','.join(usable)}",
            )
            if down:
                # Flush routed flows so traffic is re-established on a live
                # path. Without this, rules keep pointing at the dead link
                # until they idle out and "recovery time" would just be the
                # idle timeout rather than the controller's reaction.
                self._flush_routed_flows()

    def _flush_routed_flows(self) -> None:
        for dp in list(self.datapaths.values()):
            parser, ofp = dp.ofproto_parser, dp.ofproto
            dp.send_msg(
                parser.OFPFlowMod(
                    datapath=dp,
                    command=ofp.OFPFC_DELETE,
                    match=parser.OFPMatch(),
                    out_port=ofp.OFPP_ANY,
                    out_group=ofp.OFPG_ANY,
                    table_id=ofp.OFPTT_ALL,
                    # Cookie-scoped so the table-miss and IPv6 rules survive.
                    cookie=ROUTED_COOKIE,
                    cookie_mask=COOKIE_MASK_EXACT,
                )
            )
            self._bump("flow_mod")
        self.writers.event("routed_flows_flushed", "")

    def usable_paths(self) -> Tuple[str, ...]:
        paths = spec.paths_avoiding(self.failed_links)
        return paths if paths else spec.PATH_NAMES

    # ----------------------------------------------------------- stats polling
    def _monitor_loop(self) -> None:
        # Let switches finish connecting before the first request, so the first
        # sample covers all five rather than however many raced in.
        hub.sleep(1.0)
        while self._running:
            try:
                self._request_stats()
                hub.sleep(self.poll_interval)
                self._finalise_cycle()
            except Exception:  # keep the controller alive; a lost interval is
                # recoverable, a dead monitor thread silently ends the run.
                self.logger.exception("monitor cycle failed")
                hub.sleep(self.poll_interval)

    def _request_stats(self) -> None:
        for dp in list(self.datapaths.values()):
            parser, ofp = dp.ofproto_parser, dp.ofproto
            dp.send_msg(parser.OFPPortStatsRequest(dp, 0, ofp.OFPP_ANY))
            dp.send_msg(parser.OFPFlowStatsRequest(dp))
            dp.send_msg(parser.OFPTableStatsRequest(dp, 0))
            self._bump("stats_request", 3)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def _port_stats_reply(self, ev):
        self._bump("stats_reply")
        switch = spec.SWITCH_BY_DPID.get(ev.msg.datapath.id)
        if switch is None:
            return
        with self._lock:
            for stat in ev.msg.body:
                # OpenFlow reserves port numbers from 0xffffff00 up
                # (OFPP_LOCAL, OFPP_CONTROLLER, ...). Those are not links and
                # have no capacity, so including them would pollute the
                # utilisation maths.
                if stat.port_no >= 0xFFFFFF00:
                    continue
                self._acc_ports[(switch, stat.port_no)] = metrics.PortCounters(
                    tx_packets=stat.tx_packets,
                    rx_packets=stat.rx_packets,
                    tx_bytes=stat.tx_bytes,
                    rx_bytes=stat.rx_bytes,
                    tx_dropped=stat.tx_dropped,
                    rx_dropped=stat.rx_dropped,
                    tx_errors=stat.tx_errors,
                    rx_errors=stat.rx_errors,
                )

    @set_ev_cls(ofp_event.EventOFPTableStatsReply, MAIN_DISPATCHER)
    def _table_stats_reply(self, ev):
        self._bump("stats_reply")
        switch = spec.SWITCH_BY_DPID.get(ev.msg.datapath.id)
        if switch is None:
            return
        active = lookup = matched = 0
        for stat in ev.msg.body:
            active += stat.active_count
            lookup += stat.lookup_count
            matched += stat.matched_count
        with self._lock:
            self._acc_tables[switch] = metrics.TableCounters(active, lookup, matched)

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def _flow_stats_reply(self, ev):
        self._bump("stats_reply")
        switch = spec.SWITCH_BY_DPID.get(ev.msg.datapath.id)
        if switch is None:
            return
        rules = 0
        pkts_installed = 0
        pkts_miss = 0
        for stat in ev.msg.body:
            if stat.priority >= FLOW_PRIORITY:
                rules += 1
                pkts_installed += stat.packet_count
            elif stat.priority == TABLE_MISS_PRIORITY:
                pkts_miss += stat.packet_count
            # INFRA_PRIORITY rules are deliberately excluded from both.
        with self._lock:
            self._acc_rules[switch] = rules
            self._acc_pkts_installed[switch] = pkts_installed
            self._acc_pkts_miss[switch] = pkts_miss

    # ------------------------------------------------------- the control loop
    def _finalise_cycle(self) -> None:
        with self._lock:
            sample = metrics.RawSample(
                t=time.time(),
                ports=dict(self._acc_ports),
                tables=dict(self._acc_tables),
                rules_installed=dict(self._acc_rules),
                pkts_installed=dict(self._acc_pkts_installed),
                pkts_miss=dict(self._acc_pkts_miss),
                controller=self.counters.copy(),
            )
        self.writers.write_raw(sample)

        if self._prev_sample is None:
            self._prev_sample = sample
            return

        snap = metrics.derive(self._prev_sample, sample, self.occupancy_cap)
        self._prev_sample = sample
        self.writers.snapshots.write(snap.as_row())

        reward = compute_reward(
            delivered_bps=snap.delivered_bps,
            max_link_utilisation=snap.max_link_util,
            loss_fraction=snap.loss_fraction,
            occupancy_fraction=snap.occupancy_fraction,
            # Only *reactive* load counts against the policy: PacketIn plus
            # FlowMod. Our own periodic stats polling is fixed overhead that
            # the policy cannot influence, and at 15 requests/sec it would
            # dominate the total and flatten this term into a constant --
            # leaving variant C no gradient to learn timeout control from.
            controller_msg_rate=snap.packet_in_rate + snap.flow_mod_rate,
            weights=self.weights,
            normalisers=self.normalisers,
        )
        state = discretise(snap.path_util, snap.occupancy_fraction, snap.miss_rate_per_s)
        usable = self.usable_paths()

        path, idle, extra = self.policy_step(state, usable, snap, reward)

        if path not in spec.PATH_NAMES:
            self.logger.error("policy returned unknown path %r; keeping %s",
                              path, self.current_path)
            path = self.current_path
        self.current_path = path
        self.current_idle = int(idle)

        row: Dict[str, object] = {
            "t": round(snap.t, 4),
            "step": self._step,
            "variant": self.VARIANT,
            "state": state.encode(),
            "action": f"path={path},idle={idle}",
            "path": path,
            "idle_timeout": idle,
            "reward": round(reward.total, 6),
            "usable_paths": "|".join(usable),
            "r_throughput": round(reward.throughput, 6),
            "r_utilisation": round(reward.utilisation, 6),
            "r_loss": round(reward.loss, 6),
            "r_occupancy": round(reward.occupancy, 6),
            "r_controller_msgs": round(reward.controller_msgs, 6),
            "exploratory": "",
            "greedy_action": "",
            "epsilon": "",
            "q_before": "",
            "q_after": "",
            "td_error": "",
        }
        row.update(extra)
        self.writers.decisions.write(row)
        self._step += 1

    # ------------------------------------------------------------- shutdown
    def stop(self) -> None:
        """Signal the monitor loop before the base class joins it.

        ``AppManager.uninstantiate`` calls ``stop()`` (which joins
        ``self.threads``) and only then ``close()``.  If the stop flag were
        cleared in ``close()``, the join would wait forever on a monitor loop
        that has not been told to finish -- a shutdown deadlock.
        """
        self._running = False
        super().stop()

    def close(self) -> None:
        """Called by AppManager.close() on shutdown."""
        self._running = False
        try:
            self.on_shutdown()
        except Exception:
            self.logger.exception("variant shutdown hook failed")
        self.writers.event("controller_stop", f"steps={self._step}")
        self.writers.close()
