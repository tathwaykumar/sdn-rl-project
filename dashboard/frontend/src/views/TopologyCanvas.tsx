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
import { useMemo } from "react";
import ReactFlow, {
  Background, BackgroundVariant, Controls, Handle, Position,
  type Edge, type Node, type NodeProps,
} from "reactflow";
import "reactflow/dist/style.css";

import { api } from "../api/client";
import { useAsync } from "../hooks/useLive";
import type { LiveSnapshot, TopoLink, TopoNode, TopologyPayload } from "../api/types";
import { Empty, ErrorBox, Skeleton, cx, fmtPercent } from "../components/ui";

const POSITIONS: Record<string, { x: number; y: number }> = {
  h1: { x: 0, y: 36 }, h2: { x: 0, y: 196 },
  s1: { x: 186, y: 116 },
  s2: { x: 410, y: 12 },
  s3: { x: 338, y: 222 }, s5: { x: 512, y: 222 },
  s4: { x: 664, y: 116 },
  h3: { x: 856, y: 36 }, h4: { x: 856, y: 196 },
};

function SwitchNode({ data }: NodeProps) {
  const { label, dpid, isEdge, ruleCount, selected, utilisation } = data as {
    label: string; dpid?: number; isEdge?: boolean;
    ruleCount?: number | null; selected?: boolean; utilisation?: number | null;
  };
  const busy = (utilisation ?? 0) > 0.75;
  return (
    <div
      className={cx(
        "rounded-xl border px-3 py-2 min-w-[104px] text-center cursor-pointer",
        "transition-all duration-200",
      )}
      style={{
        background: selected
          ? "linear-gradient(180deg, var(--surface-4), var(--surface-3))"
          : "linear-gradient(180deg, var(--surface-3), var(--surface-2))",
        borderColor: selected
          ? "var(--accent)"
          : busy ? "rgba(251,191,36,0.4)" : "var(--border-2)",
        boxShadow: selected
          ? "0 0 0 3px var(--accent-glow), var(--e3)"
          : "var(--e2)",
      }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
      <Handle type="target" position={Position.Top} id="t" style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Bottom} id="b" style={{ opacity: 0 }} />

      <div className="flex items-center justify-center gap-1.5">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none"
             stroke={isEdge ? "var(--accent)" : "var(--text-3)"} strokeWidth="2.4"
             aria-hidden="true">
          <rect x="3" y="7" width="18" height="10" rx="2" />
          <path d="M7 11h2M12 11h2M17 11h0" />
        </svg>
        <span className="t-h2" style={{ fontSize: 13 }}>{label}</span>
      </div>
      <div className="t-micro" style={{ color: "var(--text-4)" }}>
        dpid {dpid}{isEdge ? " · edge" : ""}
      </div>
      {ruleCount != null && (
        <div className="t-micro num mt-0.5" style={{ color: "var(--text-2)" }}>
          {ruleCount} rules
        </div>
      )}
    </div>
  );
}

function HostNode({ data }: NodeProps) {
  const { label, ip, selected } = data as
    { label: string; ip?: string; selected?: boolean };
  return (
    <div
      className="rounded-xl border px-3 py-2 text-center cursor-pointer transition-all duration-200"
      style={{
        background: "linear-gradient(180deg, var(--surface-2), var(--surface-1))",
        borderColor: selected ? "var(--accent)" : "var(--border-1)",
        boxShadow: selected ? "0 0 0 3px var(--accent-glow), var(--e2)" : "var(--e1)",
      }}
    >
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div className="flex items-center justify-center gap-1.5">
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none"
             stroke="var(--text-3)" strokeWidth="2.4" aria-hidden="true">
          <rect x="4" y="4" width="16" height="12" rx="2" />
          <path d="M9 20h6" />
        </svg>
        <span className="t-h2" style={{ fontSize: 12 }}>{label}</span>
      </div>
      <div className="t-micro mono" style={{ color: "var(--text-4)" }}>{ip}</div>
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
  selection,
  onSelect,
  runId,
}: {
  snapshot: LiveSnapshot | null;
  compact?: boolean;
  selection?: Selection;
  onSelect?: (s: Selection) => void;
  runId?: string;
}) {
  const { data: topo, loading, error, refresh } = useAsync<TopologyPayload>(
    () => api.topologyState(runId),
    [runId],
    10000,
  );

  const live = snapshot?.topology ?? topo?.live;
  const linkState = live?.link_state ?? {};
  const activePaths = live?.active_paths;
  const flowCounts = snapshot?.flow_counts ?? {};

  const { nodes, edges } = useMemo(() => {
    if (!topo) return { nodes: [] as Node[], edges: [] as Edge[] };

    const ruleCounts = (activePaths?.rule_counts ?? {}) as Record<string, number>;
    const dominant = activePaths?.available ? activePaths.dominant_path : null;

    // Per-switch peak utilisation, used only to tint a node when it is hot.
    const switchUtil: Record<string, number> = {};
    for (const link of topo.links) {
      const u = linkState[link.id]?.utilisation ?? 0;
      for (const end of [link.source, link.target]) {
        switchUtil[end] = Math.max(switchUtil[end] ?? 0, u ?? 0);
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
        utilisation: switchUtil[n.id] ?? null,
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
      let width = 1.5;
      if (failed) {
        stroke = "var(--bad)";
        width = 2.2;
      } else if (carrying) {
        if ((util ?? 0) > 0.85) stroke = "var(--warn)";
        else stroke = isDominant ? "var(--accent)" : "var(--accent-3)";
        width = isDominant ? 3.2 : 2.4;
      }

      // Animate only where telemetry reports real traffic.
      const flowing = carrying && !failed && (util ?? 0) > 0.02;
      const fast = (util ?? 0) > 0.6;

      return {
        id: l.id,
        source: l.source,
        target: l.target,
        type: "smoothstep",
        style: {
          stroke: isSelected ? "var(--accent-2)" : stroke,
          strokeWidth: isSelected ? width + 1.6 : width,
          strokeDasharray: failed ? "6 5" : undefined,
          filter: isDominant && !failed && carrying
            ? "drop-shadow(0 0 5px var(--accent-glow))" : undefined,
        },
        className: flowing ? (fast ? "edge-flowing-fast" : "edge-flowing") : undefined,
        label: l.kind === "core"
          ? failed ? "DOWN" : util != null ? fmtPercent(util, 0) : undefined
          : undefined,
        labelStyle: {
          fill: failed ? "var(--bad)" : (util ?? 0) > 0.85
            ? "var(--warn)" : "var(--text-2)",
          fontSize: 10, fontWeight: 600,
        },
        labelBgStyle: { fill: "var(--surface-0)", fillOpacity: 0.92 },
        labelBgPadding: [4, 2] as [number, number],
        labelBgBorderRadius: 4,
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
    <div className="h-full w-full">
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={NODE_TYPES}
        fitView
        fitViewOptions={{ padding: compact ? 0.14 : 0.2 }}
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
        <Background variant={BackgroundVariant.Dots} gap={20} size={1}
                    color="#16202e" />
        {!compact && <Controls showInteractive={false} />}
      </ReactFlow>
    </div>
  );
}

/** Legend shared by both hosts of the canvas. */
export function TopologyLegend() {
  const items = [
    { color: "var(--accent)", label: "Dominant confirmed path", dash: false },
    { color: "var(--accent-3)", label: "Carrying installed rules", dash: false },
    { color: "var(--warn)", label: "Above 85% utilisation", dash: false },
    { color: "var(--idle)", label: "Idle — no rules installed", dash: false },
    { color: "var(--bad)", label: "Link down (controller-reported)", dash: true },
  ];
  return (
    <div className="space-y-1.5">
      {items.map((i) => (
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
      <p className="t-micro leading-relaxed pt-1" style={{ color: "var(--text-4)" }}>
        Animation runs only where measured utilisation is above zero. An idle
        network looks idle.
      </p>
    </div>
  );
}
