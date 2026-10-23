/** Live topology.
 *
 * The graph is built from the backend's topology payload, which comes from
 * `topo.spec` -- the same module Mininet and the controller use. There is no
 * second topology definition that could drift.
 *
 * A path is drawn as *carrying traffic* only when installed rules at the edge
 * switches point down it. The agent choosing a path does not colour it: that
 * would show a route change that has not happened yet, which is the single
 * easiest way for a dashboard like this to lie.
 */
import { useMemo, useState } from "react";
import ReactFlow, {
  Background, BackgroundVariant, Controls, Handle, Position,
  type Edge, type Node, type NodeProps,
} from "reactflow";
import "reactflow/dist/style.css";

import { api } from "../api/client";
import { useAsync } from "../hooks/useLive";
import type { LiveSnapshot, TopoLink, TopoNode, TopologyPayload } from "../api/types";
import {
  Card, Empty, ErrorBox, Skeleton, SourceBadge, StatusDot,
  cx, fmtAge, fmtBits, fmtPercent,
} from "../components/ui";

const POSITIONS: Record<string, { x: number; y: number }> = {
  h1: { x: 0, y: 30 }, h2: { x: 0, y: 190 },
  s1: { x: 180, y: 110 },
  s2: { x: 400, y: 10 },
  s3: { x: 330, y: 215 }, s5: { x: 500, y: 215 },
  s4: { x: 650, y: 110 },
  h3: { x: 840, y: 30 }, h4: { x: 840, y: 190 },
};

function SwitchNode({ data }: NodeProps) {
  const { label, dpid, isEdge, ruleCount, selected } = data as {
    label: string; dpid?: number; isEdge?: boolean;
    ruleCount?: number | null; selected?: boolean;
  };
  return (
    <div
      className={cx(
        "rounded-lg border px-3 py-2 min-w-[92px] text-center cursor-pointer transition-all",
        selected && "ring-2",
      )}
      style={{
        background: isEdge ? "var(--surface-3)" : "var(--surface-2)",
        borderColor: selected ? "#4e9bff" : "var(--border-2)",
      }}
    >
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
      <Handle type="target" position={Position.Top} id="t" style={{ opacity: 0 }} />
      <Handle type="source" position={Position.Bottom} id="b" style={{ opacity: 0 }} />
      <div className="text-[13px] font-semibold">{label}</div>
      <div className="text-[9px]" style={{ color: "var(--text-3)" }}>
        dpid {dpid} {isEdge ? "· edge" : ""}
      </div>
      {ruleCount !== null && ruleCount !== undefined && (
        <div className="text-[9px] num mt-0.5" style={{ color: "var(--text-2)" }}>
          {ruleCount} rules
        </div>
      )}
    </div>
  );
}

function HostNode({ data }: NodeProps) {
  const { label, ip, selected } = data as { label: string; ip?: string; selected?: boolean };
  return (
    <div
      className={cx("rounded-lg border px-3 py-1.5 text-center cursor-pointer", selected && "ring-2")}
      style={{
        background: "var(--surface-1)",
        borderColor: selected ? "#4e9bff" : "var(--border-1)",
      }}
    >
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div className="text-[12px] font-medium">{label}</div>
      <div className="text-[9px] mono" style={{ color: "var(--text-3)" }}>{ip}</div>
    </div>
  );
}

const NODE_TYPES = { switchNode: SwitchNode, hostNode: HostNode };

type Selection =
  | { kind: "node"; id: string }
  | { kind: "link"; id: string }
  | null;

export function TopologyView({ snapshot }: { snapshot: LiveSnapshot | null }) {
  const { data: topo, loading, error, refresh } = useAsync<TopologyPayload>(
    () => api.topologyState(),
    [],
    8000,
  );
  const [selection, setSelection] = useState<Selection>(null);

  // Live link state arrives on the websocket between the slower topology
  // refreshes, so prefer it when present.
  const live = snapshot?.topology ?? topo?.live;
  const linkState = live?.link_state ?? {};
  const activePaths = live?.active_paths;
  const flowCounts = snapshot?.flow_counts ?? {};

  const { nodes, edges } = useMemo(() => {
    if (!topo) return { nodes: [] as Node[], edges: [] as Edge[] };

    const nodeList: Node[] = topo.nodes.map((n: TopoNode) => ({
      id: n.id,
      type: n.kind === "switch" ? "switchNode" : "hostNode",
      position: POSITIONS[n.id] ?? { x: 0, y: 0 },
      draggable: false,
      data: {
        label: n.label,
        dpid: n.dpid,
        isEdge: n.is_edge,
        ip: n.ip,
        ruleCount: n.kind === "switch" ? (flowCounts[n.id]?.routed ?? null) : null,
        selected: selection?.kind === "node" && selection.id === n.id,
      },
    }));

    const dominant = activePaths?.available ? activePaths.dominant_path : null;
    const ruleCounts = activePaths?.rule_counts ?? {};

    const edgeList: Edge[] = topo.links.map((l: TopoLink) => {
      const state = linkState[l.id];
      const failed = state?.state === "down";
      const util = state?.utilisation ?? null;
      // "Carrying" means rules exist on this path at the edge switches.
      const carrying =
        l.kind === "core" &&
        l.paths.some((p) => (ruleCounts[p] ?? 0) > 0);
      const isDominant =
        l.kind === "core" && dominant !== null && l.paths.includes(dominant ?? "");

      let stroke = "var(--idle)";
      let width = 1.5;
      if (failed) {
        stroke = "var(--bad)";
        width = 2;
      } else if (carrying) {
        stroke = isDominant ? "var(--ok)" : "#5f9fe8";
        width = isDominant ? 3 : 2.2;
      }

      const selected = selection?.kind === "link" && selection.id === l.id;

      return {
        id: l.id,
        source: l.source,
        target: l.target,
        type: "smoothstep",
        animated: false,
        style: {
          stroke: selected ? "#4e9bff" : stroke,
          strokeWidth: selected ? width + 1.5 : width,
          strokeDasharray: failed ? "5 4" : undefined,
        },
        className: carrying && !failed ? "edge-flowing" : undefined,
        label:
          l.kind === "core"
            ? failed
              ? "DOWN"
              : util !== null
                ? fmtPercent(util, 0)
                : undefined
            : undefined,
        labelStyle: {
          fill: failed ? "var(--bad)" : "var(--text-2)",
          fontSize: 10,
          fontWeight: 600,
        },
        labelBgStyle: { fill: "var(--surface-0)", fillOpacity: 0.85 },
        labelBgPadding: [3, 2] as [number, number],
        labelBgBorderRadius: 3,
      };
    });

    return { nodes: nodeList, edges: edgeList };
  }, [topo, linkState, activePaths, flowCounts, selection]);

  if (loading && !topo) {
    return <Card title="Live topology"><Skeleton rows={6} /></Card>;
  }
  if (error && !topo) {
    return <Card title="Live topology"><ErrorBox message={error} onRetry={refresh} /></Card>;
  }
  if (!topo) return null;

  const liveAvailable = live?.available ?? false;

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[1fr_320px] gap-4 h-full min-h-0">
      <Card
        title="Live topology"
        subtitle={
          liveAvailable
            ? "Link colour reflects rules actually installed at the edge switches, not the agent's latest choice."
            : (live?.reason ?? "No experiment running; showing the configured topology only.")
        }
        right={
          <SourceBadge
            source={liveAvailable ? (live?.source ?? "live") : "config"}
            age={live?.age_s ?? null}
          />
        }
        bodyClassName="p-0"
        className="min-h-[460px]"
      >
        <div className="h-full w-full" style={{ minHeight: 440 }}>
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            fitView
            fitViewOptions={{ padding: 0.18 }}
            proOptions={{ hideAttribution: true }}
            onNodeClick={(_, node) => setSelection({ kind: "node", id: node.id })}
            onEdgeClick={(_, edge) => setSelection({ kind: "link", id: edge.id })}
            onPaneClick={() => setSelection(null)}
            nodesDraggable={false}
            nodesConnectable={false}
            elementsSelectable
          >
            <Background
              variant={BackgroundVariant.Dots}
              gap={18}
              size={1}
              color="#1e2833"
            />
            <Controls showInteractive={false} />
          </ReactFlow>
        </div>
      </Card>

      <div className="flex flex-col gap-4 min-h-0 overflow-auto">
        <PathsPanel topo={topo} activePaths={activePaths} />
        <DetailPanel topo={topo} selection={selection} linkState={linkState} flowCounts={flowCounts} />
        <LegendPanel />
      </div>
    </div>
  );
}

function PathsPanel({
  topo,
  activePaths,
}: {
  topo: TopologyPayload;
  activePaths?: LiveSnapshot["topology"] extends infer _ ? any : never;
}) {
  const counts: Record<string, number> = activePaths?.rule_counts ?? {};
  const total = Object.values(counts).reduce((a: number, b) => a + (b as number), 0);
  return (
    <Card
      title="Candidate paths"
      subtitle="Which path is carrying rules right now"
      right={<SourceBadge source={activePaths?.available ? "live" : "unavailable"} />}
    >
      <div className="space-y-3">
        {Object.values(topo.paths).map((p) => {
          const count = counts[p.name] ?? 0;
          const share = total ? count / total : 0;
          const carrying = count > 0;
          return (
            <div key={p.name}>
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-2">
                  <StatusDot tone={carrying ? "ok" : "idle"} />
                  <span className="text-[12px] font-semibold">Path {p.name}</span>
                  <span className="text-[10px]" style={{ color: "var(--text-3)" }}>
                    {p.switches.join(" → ")}
                  </span>
                </div>
                <span className="text-[11px] num" style={{ color: "var(--text-2)" }}>
                  {count} rules
                </span>
              </div>
              <div
                className="h-1.5 rounded-full overflow-hidden"
                style={{ background: "var(--surface-3)" }}
              >
                <div
                  className="h-full rounded-full transition-all"
                  style={{
                    width: `${Math.round(share * 100)}%`,
                    background: carrying ? "var(--ok)" : "var(--idle)",
                  }}
                />
              </div>
              <div className="flex gap-3 mt-1 text-[10px]" style={{ color: "var(--text-3)" }}>
                <span>{p.hops} hops</span>
                <span>{p.bottleneck_mbit} Mbit/s bottleneck</span>
              </div>
            </div>
          );
        })}
        {!activePaths?.available && (
          <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-3)" }}>
            {activePaths?.reason ??
              "No flow-table dump is available, so no path can be confirmed as active."}
          </p>
        )}
      </div>
    </Card>
  );
}

function DetailPanel({
  topo, selection, linkState, flowCounts,
}: {
  topo: TopologyPayload;
  selection: Selection;
  linkState: Record<string, any>;
  flowCounts: Record<string, { total: number; routed: number }>;
}) {
  if (!selection) {
    return (
      <Card title="Details">
        <Empty
          title="Nothing selected"
          detail="Click a switch or a link in the diagram to inspect it."
        />
      </Card>
    );
  }

  if (selection.kind === "node") {
    const node = topo.nodes.find((n) => n.id === selection.id);
    if (!node) return null;
    const counts = flowCounts[node.id];
    const attached = topo.links.filter(
      (l) => l.source === node.id || l.target === node.id,
    );
    return (
      <Card title={`${node.kind === "switch" ? "Switch" : "Host"} ${node.label}`}>
        <dl className="space-y-1.5 text-[12px]">
          {node.kind === "switch" ? (
            <>
              <Row k="Datapath ID" v={String(node.dpid)} />
              <Row k="Role" v={node.is_edge ? "edge switch" : "core switch"} />
              <Row
                k="Installed rules"
                v={counts ? `${counts.routed} routed / ${counts.total} total` : "unavailable"}
              />
            </>
          ) : (
            <>
              <Row k="IP" v={node.ip ?? "—"} mono />
              <Row k="MAC" v={node.mac ?? "—"} mono />
              <Row k="Attached" v={`${node.attached_to}:${node.attached_port}`} />
            </>
          )}
        </dl>
        <div className="mt-3">
          <p className="text-[11px] font-medium mb-1.5" style={{ color: "var(--text-2)" }}>
            Links
          </p>
          <div className="space-y-1">
            {attached.map((l) => (
              <div key={l.id} className="flex items-center justify-between text-[11px]">
                <span className="mono">{l.id}</span>
                <span style={{ color: "var(--text-3)" }}>
                  {l.bandwidth_mbit} Mbit · {l.delay_ms} ms
                </span>
              </div>
            ))}
          </div>
        </div>
      </Card>
    );
  }

  const link = topo.links.find((l) => l.id === selection.id);
  if (!link) return null;
  const state = linkState[link.id];
  const failed = state?.state === "down";
  return (
    <Card title={`Link ${link.id}`}>
      <dl className="space-y-1.5 text-[12px]">
        <Row
          k="State"
          v={
            <span className="inline-flex items-center gap-1.5">
              <StatusDot tone={failed ? "bad" : "ok"} />
              {state?.state ?? "unknown"}
              {state && !state.state_observed && link.kind === "core" && (
                <span className="text-[10px]" style={{ color: "var(--text-3)" }}>
                  (no change reported)
                </span>
              )}
            </span>
          }
        />
        <Row k="Type" v={link.kind} />
        <Row k="Endpoints" v={`${link.source}:${link.source_port} ↔ ${link.target}:${link.target_port}`} mono />
        <Row k="Bandwidth" v={`${link.bandwidth_mbit} Mbit/s`} />
        <Row k="Delay" v={`${link.delay_ms} ms`} />
        {link.queue_packets !== undefined && (
          <Row k="Queue" v={`${link.queue_packets} packets`} />
        )}
        <Row
          k="Utilisation"
          v={state?.utilisation !== null && state?.utilisation !== undefined
            ? fmtPercent(state.utilisation)
            : "not measured"}
        />
        <Row k="On paths" v={link.paths.length ? link.paths.join(", ") : "—"} />
      </dl>
      {state?.directions && Object.keys(state.directions).length > 0 && (
        <div className="mt-3">
          <p className="text-[11px] font-medium mb-1.5" style={{ color: "var(--text-2)" }}>
            Per direction
          </p>
          {Object.entries(state.directions).map(([dir, d]: [string, any]) => (
            <div key={dir} className="flex items-center justify-between text-[11px]">
              <span className="mono">{dir}</span>
              <span className="num" style={{ color: "var(--text-2)" }}>
                {fmtBits(d.bps)} · {fmtPercent(d.utilisation)}
              </span>
            </div>
          ))}
        </div>
      )}
      {state?.state_changed_at && (
        <p className="text-[10px] mt-2" style={{ color: "var(--text-3)" }}>
          State last changed {fmtAge((Date.now() / 1000) - state.state_changed_at)}
        </p>
      )}
    </Card>
  );
}

function Row({ k, v, mono }: { k: string; v: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt className="text-[11px]" style={{ color: "var(--text-3)" }}>{k}</dt>
      <dd className={cx("text-right", mono && "mono text-[11px]")}>{v}</dd>
    </div>
  );
}

function LegendPanel() {
  const items = [
    { color: "var(--ok)", label: "Carrying traffic (dominant path)" },
    { color: "#5f9fe8", label: "Has installed rules" },
    { color: "var(--idle)", label: "Idle — no rules installed" },
    { color: "var(--bad)", label: "Link down (reported by the controller)" },
  ];
  return (
    <Card title="Legend">
      <div className="space-y-1.5">
        {items.map((i) => (
          <div key={i.label} className="flex items-center gap-2 text-[11px]">
            <span className="w-5 h-0.5 rounded" style={{ background: i.color }} />
            <span style={{ color: "var(--text-2)" }}>{i.label}</span>
          </div>
        ))}
      </div>
      <p className="text-[10px] mt-3 leading-relaxed" style={{ color: "var(--text-3)" }}>
        Link colour is driven by rules read back from the switches. A path is
        never highlighted because the agent selected it — only because rules
        for it exist.
      </p>
    </Card>
  );
}
