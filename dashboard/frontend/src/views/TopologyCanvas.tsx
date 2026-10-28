/** The network graph, shared by the Overview and the Live Topology page.
 *
 * One implementation, two hosts — the Overview embeds it compactly, the
 * Topology page gives it the full canvas and adds inspector panels. Keeping it
 * in one place means the two can never disagree about what the network looks
 * like.
 *
 * Honesty rules encoded here:
 *   - An edge is styled as carrying traffic only when installed rules point
 *     down its path. The agent selecting a path does not colour it.
 *   - Flow animation is driven by measured utilisation. With no telemetry,
 *     nothing animates.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ReactFlow, {
  Background, BackgroundVariant, Controls, Handle, Position,
  type Edge, type Node, type NodeProps,
} from "reactflow";
import "reactflow/dist/style.css";

import { api } from "../api/client";
import { useAsync } from "../hooks/useLive";
import type {
  FlowTablePayload, LiveSnapshot, TopoLink, TopoNode, TopologyPayload,
} from "../api/types";
import {
  Empty, ErrorBox, Skeleton, cx, fmtBits, fmtBytes, fmtPercent,
} from "../components/ui";

/** Fixed layout for the diamond topology.
 *
 * Hand-placed rather than force-directed: the shape of this network is the
 * point — two link-disjoint paths between the edge switches — and a physics
 * layout would redraw that shape differently on every mount.
 *
 * The proportions are chosen so the graph's bounding box is close to the shape
 * of the panels that host it (roughly 1.75:1). `fitView` scales to whichever
 * axis binds first, so a graph much wider than its panel gets scaled down to
 * fit the width and leaves a dead band above and below it. */
const POSITIONS: Record<string, { x: number; y: number }> = {
  h1: { x: 0, y: 30 }, h2: { x: 0, y: 390 },
  s1: { x: 178, y: 208 },
  s2: { x: 368, y: 8 },
  s3: { x: 298, y: 408 }, s5: { x: 470, y: 408 },
  s4: { x: 600, y: 208 },
  h3: { x: 772, y: 30 }, h4: { x: 772, y: 390 },
};

function SwitchNode({ data }: NodeProps) {
  const { label, dpid, isEdge, ruleCount, selected, utilisation } = data as {
    label: string; dpid?: number; isEdge?: boolean;
    ruleCount?: number | null; selected?: boolean; utilisation?: number | null;
  };
  const util = utilisation ?? null;
  const busy = (util ?? 0) > 0.75;
  const tint = busy ? "var(--warn)" : isEdge ? "var(--accent)" : "var(--accent-3)";
  return (
    <div
      className={cx(
        "rounded-[10px] px-3.5 py-3 min-w-[138px] cursor-pointer relative overflow-hidden",
        "transition-all duration-200",
      )}
      style={{
        background: isEdge
          ? "linear-gradient(165deg, var(--panel-raised), var(--surface-3))"
          : "var(--panel-raised)",
        /* Selection and congestion are stated with a border and a ring, not a
           halo: a glowing node implied activity the telemetry had not
           reported. An edge switch keeps a quiet, permanent hint of the
           accent so the two role-defining nodes read as the network's
           endpoints even before anything is selected. */
        border: `1px solid ${
          selected
            ? "var(--accent)"
            : busy
              ? "color-mix(in srgb, var(--warn) 55%, transparent)"
              : isEdge
                ? "color-mix(in srgb, var(--accent) 30%, var(--border-2))"
                : "var(--border-2)"
        }`,
        boxShadow: selected
          ? "0 0 0 1px var(--accent), 0 0 24px -6px var(--accent-ring), var(--e3)"
          : isEdge
            ? "var(--e2), inset 0 1px 0 color-mix(in srgb, var(--accent) 10%, transparent)"
            : "var(--e2)",
      }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
      <Handle type="target" position={Position.Top} id="t" style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Bottom} id="b" style={{ opacity: 0 }} />

      <div className="flex items-center gap-2.5 relative">
        <span
          className="w-7 h-7 rounded-[7px] grid place-items-center shrink-0"
          style={{
            background: "var(--surface-4)",
            border: `1px solid color-mix(in srgb, ${tint} 25%, transparent)`,
          }}
          aria-hidden="true"
        >
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none"
               stroke={tint} strokeWidth="2" strokeLinecap="round">
            <rect x="3" y="7" width="18" height="10" rx="2" />
            <path d="M7 11h2M12 11h2M17 11h0" />
          </svg>
        </span>
        <div className="min-w-0">
          <div
            className="mono leading-none font-semibold"
            style={{ fontSize: 16, letterSpacing: "0.02em" }}
          >
            {label}
          </div>
          <div className="t-micro mt-1" style={{ color: "var(--text-4)" }}>
            dpid {dpid}{isEdge ? " · EDGE" : ""}
          </div>
        </div>
      </div>

      {ruleCount != null && (
        <div
          className="mt-2.5 pt-2.5 flex items-baseline gap-1.5 relative"
          style={{ borderTop: "1px solid var(--hairline)" }}
        >
          <span className="readout text-[15px]" style={{ color: "var(--text-1)" }}>
            {ruleCount}
          </span>
          <span className="t-micro" style={{ color: "var(--text-4)" }}>rules</span>
        </div>
      )}
      {/* Utilisation bar: the switch's hottest attached link. Measured, so it
          is absent rather than zero when there is no telemetry. */}
      {util != null && (
        <div
          className="mt-2 h-[4px] rounded-full overflow-hidden relative"
          style={{ background: "var(--track-bg)" }}
          title={`Peak utilisation on an attached link: ${fmtPercent(util, 0)}`}
        >
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{
              width: `${Math.min(100, util * 100)}%`,
              background: tint,
            }}
          />
        </div>
      )}
    </div>
  );
}

function HostNode({ data }: NodeProps) {
  const { label, ip, selected, traffic } = data as {
    label: string; ip?: string; selected?: boolean;
    traffic?: {
      send_bps: number | null; receive_bps: number | null;
      sent_bytes: number; received_bytes: number;
    } | null;
  };
  // A rate needs two counter samples that straddle live traffic; a completed
  // run's last two samples usually straddle its teardown, so the rate is a
  // true zero there while the byte totals remain meaningful. Show whichever
  // is real, labelled for which it is — never a total dressed up as a rate.
  const sending = (traffic?.send_bps ?? 0) > 0;
  const receiving = (traffic?.receive_bps ?? 0) > 0;
  const sentTotal = (traffic?.sent_bytes ?? 0) > 0;
  const recvTotal = (traffic?.received_bytes ?? 0) > 0;
  const activeHost = sending || receiving;

  return (
    <div
      className="rounded-[10px] px-3.5 py-3 cursor-pointer transition-all duration-200 relative overflow-hidden"
      style={{
        background: "var(--panel)",
        border: `1px solid ${
          selected
            ? "var(--accent)"
            : activeHost ? "color-mix(in srgb, var(--ok) 50%, transparent)" : "var(--border-1)"
        }`,
        boxShadow: selected ? "0 0 0 1px var(--accent), var(--e3)" : "var(--e2)",
      }}
    >
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div className="flex items-center gap-2.5 relative">
        <span
          className="w-6 h-6 rounded-[6px] grid place-items-center shrink-0"
          style={{
            background: activeHost
              ? "color-mix(in srgb, var(--ok) 16%, transparent)" : "var(--surface-3)",
            border: `1px solid ${
              activeHost ? "color-mix(in srgb, var(--ok) 42%, transparent)" : "var(--border-1)"
            }`,
          }}
          aria-hidden="true"
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none"
               stroke={activeHost ? "var(--ok)" : "var(--text-3)"} strokeWidth="2.2"
               strokeLinecap="round"
>
            <rect x="4" y="4" width="16" height="12" rx="2" />
            <path d="M9 20h6" />
          </svg>
        </span>
        <div className="min-w-0">
          <div className="mono leading-none font-semibold" style={{ fontSize: 14 }}>{label}</div>
          <div className="t-micro mono mt-1" style={{ color: "var(--text-4)" }}>{ip}</div>
        </div>
      </div>

      {/* Per-host traffic, from this host's edge-switch port counters. Shown
          only when two samples existed to derive a rate; totals alone never
          get rendered as a rate. */}
      {traffic && (sending || receiving || sentTotal || recvTotal) && (
        <div
          className="mt-2.5 pt-2.5 flex flex-col gap-1 items-stretch relative"
          style={{ borderTop: "1px solid var(--hairline)" }}
        >
          {(sending || sentTotal) && (
            <span
              className="readout text-[11px] flex items-center justify-between gap-2.5"
              style={{ color: sending ? "var(--accent)" : "var(--text-3)" }}
              title={
                sending
                  ? `${label} is sending ${fmtBits(traffic.send_bps)} right now `
                    + `(${fmtBytes(traffic.sent_bytes)} so far)`
                  : `${label} sent ${fmtBytes(traffic.sent_bytes)} in total. `
                    + "No current rate: the last two counter samples show no change."
              }
            >
              <span style={{ opacity: 0.7 }}>TX (sent)</span>
              <span>
                {sending ? fmtBits(traffic.send_bps) : fmtBytes(traffic.sent_bytes)}
              </span>
            </span>
          )}
          {(receiving || recvTotal) && (
            <span
              className="readout text-[11px] flex items-center justify-between gap-2.5"
              style={{ color: receiving ? "var(--ok)" : "var(--text-3)" }}
              title={
                receiving
                  ? `${label} is receiving ${fmtBits(traffic.receive_bps)} right now `
                    + `(${fmtBytes(traffic.received_bytes)} so far)`
                  : `${label} received ${fmtBytes(traffic.received_bytes)} in total. `
                    + "No current rate: the last two counter samples show no change."
              }
            >
              <span style={{ opacity: 0.7 }}>RX (received)</span>
              <span>
                {receiving
                  ? fmtBits(traffic.receive_bps)
                  : fmtBytes(traffic.received_bytes)}
              </span>
            </span>
          )}
        </div>
      )}
    </div>
  );
}

const NODE_TYPES = { switchNode: SwitchNode, hostNode: HostNode };

export type Selection =
  | { kind: "node"; id: string }
  | { kind: "link"; id: string }
  | null;

export function TopologyCanvas({
  snapshot,
  compact,
  inset,
  selection,
  onSelect,
  runId,
}: {
  snapshot: LiveSnapshot | null;
  compact?: boolean;
  /** Pull the graph further from the canvas edges, for hosts that float panels
      over the corners. Done through `fitView` rather than CSS padding, because
      resizing the container leaves React Flow's nodes unmeasured and hidden. */
  inset?: boolean;
  selection?: Selection;
  onSelect?: (s: Selection) => void;
  runId?: string;
}) {
  // React Flow measures its nodes with a ResizeObserver and keeps them
  // `visibility: hidden` until that reports back. Mounted into a box whose size
  // is not settled yet — which is what happens on a page whose layout above the
  // canvas is still resolving — that first report can be zero and no second one
  // ever arrives, leaving a canvas that holds nine nodes and draws none of
  // them. Waiting for a non-zero box before mounting removes most of the race,
  // but a box boxed inside a card (rather than a bare full-bleed section) can
  // still report a size one paint before its surroundings (header, sibling
  // columns) finish settling — and React Flow's own per-node observer loses
  // that second, later layout pass. Two animation frames of slack after the
  // first non-zero report is cheap and makes mounting wait for that settle.
  const [sized, setSized] = useState(false);
  const observer = useRef<ResizeObserver | null>(null);
  const raf1 = useRef<number | null>(null);
  const raf2 = useRef<number | null>(null);
  const clearRafs = () => {
    if (raf1.current != null) cancelAnimationFrame(raf1.current);
    if (raf2.current != null) cancelAnimationFrame(raf2.current);
    raf1.current = null;
    raf2.current = null;
  };
  const settle = useCallback(() => {
    clearRafs();
    raf1.current = requestAnimationFrame(() => {
      raf2.current = requestAnimationFrame(() => setSized(true));
    });
  }, []);
  const hostRef = useCallback((el: HTMLDivElement | null) => {
    observer.current?.disconnect();
    clearRafs();
    if (!el) return;
    if (el.clientWidth > 0 && el.clientHeight > 0) settle();
    observer.current = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width > 0 && height > 0) settle();
    });
    observer.current.observe(el);
  }, [settle]);
  useEffect(() => () => {
    observer.current?.disconnect();
    clearRafs();
  }, []);

  const { data: topo, loading, error, refresh } = useAsync<TopologyPayload>(
    () => api.topologyState(runId),
    [runId],
    10000,
  );

  const live = snapshot?.topology ?? topo?.live;
  const linkState = live?.link_state ?? {};
  const activePaths = live?.active_paths;
  // The live websocket snapshot carries flow_counts only for a run actually
  // in progress (forced null for a selected run, same as everywhere else on
  // this page) -- fetched directly here too, so a recorded run's switch
  // tiles show real rule counts instead of silently going blank.
  const flowsQuery = useAsync<FlowTablePayload>(
    () => api.flows(undefined, runId), [runId], runId ? undefined : 8000,
  );
  const flowCounts = flowsQuery.data?.counts_by_switch ?? snapshot?.flow_counts ?? {};
  // Per-host counters, when the run recorded two samples to derive rates from.
  const hostTraffic = live?.host_traffic_available ? (live.hosts ?? {}) : {};

  const { nodes, edges } = useMemo(() => {
    if (!topo) return { nodes: [] as Node[], edges: [] as Edge[] };

    const ruleCounts = (activePaths?.rule_counts ?? {}) as Record<string, number>;
    const dominant = activePaths?.available ? activePaths.dominant_path : null;

    // Per-switch peak utilisation, used only to tint a node when it is hot.
    // Links without a telemetry entry are skipped rather than counted as zero,
    // so a switch with no measurement at all keeps `undefined` here and its
    // node renders no meter — an empty meter track would read as a measured
    // zero, which is a different claim from "not measured".
    const switchUtil: Record<string, number> = {};
    for (const link of topo.links) {
      const u = linkState[link.id]?.utilisation;
      if (u == null) continue;
      for (const end of [link.source, link.target]) {
        switchUtil[end] = Math.max(switchUtil[end] ?? 0, u);
      }
    }

    const nodeList: Node[] = topo.nodes.map((n: TopoNode) => ({
      id: n.id,
      type: n.kind === "switch" ? "switchNode" : "hostNode",
      position: POSITIONS[n.id] ?? { x: 0, y: 0 },
      draggable: false,
      data: {
        label: n.label, dpid: n.dpid, isEdge: n.is_edge, ip: n.ip,
        ruleCount: n.kind === "switch" ? (flowCounts[n.id]?.routed ?? null) : null,
        utilisation: n.kind === "switch" ? (switchUtil[n.id] ?? null) : null,
        traffic: n.kind === "host" ? (hostTraffic[n.id] ?? null) : null,
        selected: selection?.kind === "node" && selection.id === n.id,
      },
    }));

    const edgeList: Edge[] = topo.links.map((l: TopoLink) => {
      const state = linkState[l.id];
      const failed = state?.state === "down";
      const util = state?.utilisation ?? null;
      const carrying =
        l.kind === "core" && l.paths.some((p) => (ruleCounts[p] ?? 0) > 0);
      const isDominant =
        l.kind === "core" && dominant != null && l.paths.includes(dominant);
      const isSelected = selection?.kind === "link" && selection.id === l.id;

      // Colour is driven by measured utilisation, not by intent.
      let stroke = "var(--idle)";
      let width = 2;
      if (failed) {
        stroke = "var(--bad)";
        width = 2.6;
      } else if (carrying) {
        if ((util ?? 0) > 0.85) stroke = "var(--warn)";
        else stroke = isDominant ? "var(--accent)" : "var(--accent-3)";
        width = isDominant ? 4 : 3;
      }

      // Animate only where telemetry reports real traffic.
      const flowing = carrying && !failed && (util ?? 0) > 0.02;
      const fast = (util ?? 0) > 0.6;
      const glowColor = isSelected ? "var(--accent-2)" : stroke;

      return {
        id: l.id,
        source: l.source,
        target: l.target,
        type: "smoothstep",
        style: {
          stroke: isSelected ? "var(--accent-2)" : stroke,
          strokeWidth: isSelected ? width + 1.6 : width,
          strokeDasharray: failed ? "6 5" : undefined,
          // Glow strength tracks what the link is actually doing: a soft bloom
          // on a link genuinely carrying traffic (brighter on the dominant
          // path), a harder red glow on a reported failure, none at all on an
          // idle or merely-installed link — the glow is read from the same
          // measurement the colour is, never added for effect.
          filter: flowing
            ? `drop-shadow(0 0 ${fast ? 7 : 4}px ${glowColor})`
            : failed
              ? "drop-shadow(0 0 5px var(--bad))"
              : undefined,
        },
        className: flowing ? (fast ? "edge-flowing-fast" : "edge-flowing") : undefined,
        label: l.kind === "core"
          ? failed ? "DOWN" : util != null ? fmtPercent(util, 0) : undefined
          : undefined,
        labelStyle: {
          fill: failed ? "var(--bad)" : (util ?? 0) > 0.85
            ? "var(--warn)" : "var(--text-2)",
          fontSize: 10, fontWeight: 700, letterSpacing: "0.06em",
        },
        labelBgStyle: { fill: "var(--surface-0)", fillOpacity: 0.92 },
        labelBgPadding: [6, 3] as [number, number],
        labelBgBorderRadius: 5,
      };
    });

    return { nodes: nodeList, edges: edgeList };
  }, [topo, linkState, activePaths, flowCounts, selection]);

  if (loading && !topo) return <div className="p-4"><Skeleton rows={6} /></div>;
  if (error && !topo) {
    return <div className="p-4"><ErrorBox message={error} onRetry={refresh} /></div>;
  }
  if (!topo) {
    return <div className="p-4"><Empty title="Topology unavailable" /></div>;
  }

  return (
    <div ref={hostRef} className="h-full w-full relative" style={{ background: "var(--surface-0)" }}>
      {/* A bloom under the graph: pushes the canvas edges back so the nodes
          read as lit from within the panel rather than sitting on a flat
          page. Decorative, and unrelated to any telemetry — it is the same
          shape and size regardless of what the network is doing. */}
      <span
        className="absolute inset-0 pointer-events-none z-0"
        style={{
          background:
            "radial-gradient(65% 65% at 50% 42%, color-mix(in srgb, var(--accent) 10%, transparent), transparent 70%),"
            + "radial-gradient(40% 40% at 85% 85%, color-mix(in srgb, var(--rl) 6%, transparent), transparent 70%)",
        }}
        aria-hidden="true"
      />
      {sized && <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: inset ? 0.34 : compact ? 0.1 : 0.14 }}
        proOptions={{ hideAttribution: true }}
        onNodeClick={(_, node) => onSelect?.({ kind: "node", id: node.id })}
        onEdgeClick={(_, edge) => onSelect?.({ kind: "link", id: edge.id })}
        onPaneClick={() => onSelect?.(null)}
        nodesDraggable={false}
        nodesConnectable={false}
        elementsSelectable
        minZoom={0.4}
        maxZoom={1.6}
      >
        <Background variant={BackgroundVariant.Dots} gap={22} size={1}
                    color="var(--border-3)" />
        {!compact && <Controls showInteractive={false} />}
      </ReactFlow>}
    </div>
  );
}

/** Legend shared by both hosts of the canvas. Always rendered, not behind a
    click: a map with no key is not finished. */
export function TopologyLegend() {
  const links = [
    { color: "var(--accent)", label: "Dominant confirmed path", dash: false },
    { color: "var(--accent-3)", label: "Carrying installed rules (confirmed)", dash: false },
    { color: "var(--warn)", label: "Above 85% utilisation", dash: false },
    { color: "var(--idle)", label: "Idle — no rules installed", dash: false },
    { color: "var(--bad)", label: "Link down (controller-reported)", dash: true },
  ];
  return (
    <div className="space-y-3.5">
      <div>
        <p className="t-label mb-1.5" style={{ color: "var(--text-4)" }}>
          Links &amp; forwarding state
        </p>
        <div className="space-y-1.5">
          {links.map((i) => (
            <div key={i.label} className="flex items-center gap-2 t-small">
              <span
                className="w-6 h-0.5 rounded shrink-0"
                style={{
                  background: i.dash
                    ? `repeating-linear-gradient(90deg, ${i.color} 0 4px, transparent 4px 7px)`
                    : i.color,
                }}
                aria-hidden="true"
              />
              <span style={{ color: "var(--text-2)" }}>{i.label}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="pt-0.5">
        <p className="t-label mb-1.5" style={{ color: "var(--text-4)" }}>
          Switches &amp; hosts
        </p>
        <div className="space-y-1.5 t-small">
          <div className="flex items-center gap-2">
            <span
              className="w-3.5 h-3.5 rounded-[4px] shrink-0"
              style={{ border: "1px solid var(--accent)", background: "var(--panel-raised)" }}
              aria-hidden="true"
            />
            <span style={{ color: "var(--text-2)" }}>
              Edge switch (connects directly to a host) — tagged{" "}
              <span className="readout" style={{ color: "var(--accent)" }}>EDGE</span>
            </span>
          </div>
          <div className="flex items-center gap-2">
            <span
              className="w-3.5 h-3.5 rounded-[4px] shrink-0"
              style={{ border: "1px solid var(--border-2)", background: "var(--panel-raised)" }}
              aria-hidden="true"
            />
            <span style={{ color: "var(--text-2)" }}>Core switch — no attached host</span>
          </div>
          <div className="flex items-center gap-2">
            <span
              className="w-3.5 h-3.5 rounded-[4px] shrink-0"
              style={{ border: "1px solid var(--accent)", boxShadow: "0 0 8px -1px var(--accent-ring)" }}
              aria-hidden="true"
            />
            <span style={{ color: "var(--text-2)" }}>Selected — shown in the inspector</span>
          </div>
        </div>
      </div>

      <p className="t-micro leading-relaxed" style={{ color: "var(--text-4)" }}>
        A link is only drawn as <strong>confirmed</strong> once installed rules
        for its path exist on the switches. Before anything runs, the graph
        shows only the <strong>configured</strong> topology — the static
        definition, not a measurement. Animation and glow run only where
        measured utilisation is above zero; an idle network looks idle.
      </p>
      <p className="t-micro leading-relaxed" style={{ color: "var(--text-4)" }}>
        Host tiles read <span className="readout" style={{ color: "var(--accent)" }}>TX</span>{" "}
        (sent) and <span className="readout" style={{ color: "var(--ok)" }}>RX</span>{" "}
        (received) from that host&rsquo;s own edge-switch port counters. A bright
        value is a current rate in bit/s; a dim one is a byte total, shown when
        the last two counter samples give no rate. The bar under a switch is the
        peak utilisation of its busiest attached link.
      </p>
    </div>
  );
}
