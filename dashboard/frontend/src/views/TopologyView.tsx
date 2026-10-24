/** Live Topology — the full-canvas network view with an inspector.
 *
 * Shares its graph with the Overview via TopologyCanvas, so the two can never
 * disagree about the network. This page adds the detail panels: per-switch
 * ports and installed flows, per-link telemetry, and the path summary.
 */
import { useState } from "react";
import { api } from "../api/client";
import { useAsync } from "../hooks/useLive";
import type {
  FlowTablePayload, LiveSnapshot, TopologyPayload,
} from "../api/types";
import {
  Card, Details, Empty, Pill, SourceBadge, StatusDot, fmtAge, fmtBits,
  fmtPercent,
} from "../components/ui";
import { TopologyCanvas, TopologyLegend, type Selection } from "./TopologyCanvas";

export function TopologyView({
  snapshot, runId,
}: {
  snapshot: LiveSnapshot | null;
  runId?: string;
}) {
  const [selection, setSelection] = useState<Selection>(null);
  const topo = useAsync<TopologyPayload>(
    () => api.topologyState(runId), [runId], 10000,
  );
  const flows = useAsync<FlowTablePayload>(
    () => api.flows(undefined, runId), [runId], 6000,
  );

  const live = snapshot?.topology ?? topo.data?.live;
  const available = live?.available ?? false;

  return (
    <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_356px] gap-4">
      <Card
        accent="net"
        raised
        title="Network topology"
        subtitle={
          available
            ? "Link colour and animation follow measured utilisation and installed rules"
            : (live?.reason ?? "No experiment running — showing the configured topology")
        }
        right={<SourceBadge source={available ? "live" : "config"}
                            age={live?.age_s ?? null} />}
        bodyClassName="p-0"
        className="min-h-[600px]"
      >
        <div style={{ height: 580 }}>
          <TopologyCanvas
            snapshot={runId ? null : snapshot}
            selection={selection}
            onSelect={setSelection}
            runId={runId}
          />
        </div>
      </Card>

      <div className="flex flex-col gap-4 min-h-0">
        <Inspector
          topo={topo.data}
          selection={selection}
          linkState={live?.link_state ?? {}}
          flows={flows.data}
          flowCounts={snapshot?.flow_counts ?? {}}
        />
        <Card title="Legend" accent="net">
          <TopologyLegend />
        </Card>
      </div>
    </div>
  );
}

function Inspector({
  topo, selection, linkState, flows, flowCounts,
}: {
  topo?: TopologyPayload | null;
  selection: Selection;
  linkState: Record<string, any>;
  flows?: FlowTablePayload | null;
  flowCounts: Record<string, { total: number; routed: number }>;
}) {
  if (!topo) return <Card title="Inspector"><Empty title="Loading topology" /></Card>;

  if (!selection) {
    return (
      <Card title="Inspector" subtitle="Click any switch, host or link">
        <Empty
          title="Nothing selected"
          detail="Select a switch to see its ports and installed flow rules, or a link for its live telemetry."
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
    const rules = (flows?.rules ?? []).filter((r) => r.switch === node.id);
    const routed = rules.filter((r) => r.kind === "routed");
    const timeouts = Array.from(
      new Set(routed.map((r) => r.idle_timeout).filter((t) => t != null)),
    ).sort((a, b) => (a as number) - (b as number));

    return (
      <Card
        title={`${node.kind === "switch" ? "Switch" : "Host"} ${node.label}`}
        subtitle={node.kind === "switch" ? `Datapath ${node.dpid}` : node.ip}
        accent="net"
        right={node.is_edge ? <Pill color="var(--accent)">edge</Pill> : undefined}
      >
        {node.kind === "switch" ? (
          <>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 mb-3">
              <Stat label="Total rules" value={counts ? counts.total : "—"} />
              <Stat label="Routed" value={counts ? counts.routed : "—"} tone="ok" />
              <Stat label="Ports" value={attached.length} />
            </div>

            <p className="t-label mb-1.5" style={{ color: "var(--text-4)" }}>
              Ports
            </p>
            <div className="space-y-1 mb-3">
              {attached.map((l) => {
                const isSource = l.source === node.id;
                const port = isSource ? l.source_port : l.target_port;
                const peer = isSource ? l.target : l.source;
                const state = linkState[l.id];
                const down = state?.state === "down";
                return (
                  <div key={l.id}
                       className="flex items-center justify-between t-small rounded-lg px-2 py-1"
                       style={{ background: "var(--surface-2)" }}>
                    <span className="flex items-center gap-2 min-w-0">
                      <StatusDot tone={down ? "bad" : "ok"} />
                      <span className="mono shrink-0">:{port}</span>
                      <span className="truncate" style={{ color: "var(--text-3)" }}>
                        → {peer}
                      </span>
                    </span>
                    <span className="num shrink-0" style={{ color: "var(--text-3)" }}>
                      {state?.utilisation != null
                        ? fmtPercent(state.utilisation, 0) : "—"}
                    </span>
                  </div>
                );
              })}
            </div>

            <p className="t-label mb-1.5" style={{ color: "var(--text-4)" }}>
              Installed idle timeouts
            </p>
            {timeouts.length ? (
              <div className="flex flex-wrap gap-1.5">
                {timeouts.map((t) => (
                  <Pill key={String(t)} color="var(--rl)">{String(t)}s</Pill>
                ))}
              </div>
            ) : (
              <p className="t-small" style={{ color: "var(--text-4)" }}>
                No routed rules on this switch right now.
              </p>
            )}

            <Details summary={`Flow rules on ${node.label} (${rules.length})`}>
              {rules.length ? (
                <div className="space-y-1 max-h-[220px] overflow-auto">
                  {rules.slice(0, 40).map((r, i) => (
                    <div key={i} className="flex items-center justify-between gap-2">
                      <span className="mono truncate" title={r.raw}>
                        {r.kind === "table_miss" ? "table-miss"
                          : r.nw_src ? `${r.nw_src}→${r.nw_dst}` : r.proto ?? "*"}
                      </span>
                      <span className="shrink-0 num" style={{ color: "var(--text-3)" }}>
                        {r.output_port === "CONTROLLER" ? "→ctrl"
                          : r.output_port === "drop" ? "drop"
                            : `→:${r.output_port}`}
                        {r.idle_timeout != null && ` · ${r.idle_timeout}s`}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p>No rules in the most recent dump.</p>
              )}
              <p className="mt-2" style={{ color: "var(--text-4)" }}>
                Read from the runner's ovs-ofctl dumps, refreshed every 5s.
              </p>
            </Details>
          </>
        ) : (
          <dl className="space-y-1.5 t-body">
            <Row k="IP" v={<span className="mono">{node.ip}</span>} />
            <Row k="MAC" v={<span className="mono">{node.mac}</span>} />
            <Row k="Attached to"
                 v={<span className="mono">{node.attached_to}:{node.attached_port}</span>} />
          </dl>
        )}
      </Card>
    );
  }

  const link = topo.links.find((l) => l.id === selection.id);
  if (!link) return null;
  const state = linkState[link.id];
  const down = state?.state === "down";

  return (
    <Card
      title={`Link ${link.id}`}
      subtitle={`${link.kind} link`}
      accent="net"
      right={
        <Pill color={down ? "var(--bad)" : "var(--ok)"}>
          {state?.state ?? "unknown"}
        </Pill>
      }
    >
      <div className="grid grid-cols-2 gap-2 mb-3">
        <Stat label="Bandwidth" value={`${link.bandwidth_mbit} Mbit/s`} />
        <Stat label="Delay" value={`${link.delay_ms} ms`} />
        <Stat
          label="Utilisation"
          value={state?.utilisation != null ? fmtPercent(state.utilisation) : "N/A"}
          tone={(state?.utilisation ?? 0) > 0.85 ? "warn" : undefined}
        />
        <Stat label="Queue"
              value={link.queue_packets != null ? `${link.queue_packets} pkt` : "—"} />
      </div>

      <dl className="space-y-1.5 t-body mb-2">
        <Row k="Endpoints"
             v={<span className="mono">{link.source}:{link.source_port} ↔ {link.target}:{link.target_port}</span>} />
        <Row k="On paths" v={link.paths.length ? link.paths.join(", ") : "—"} />
      </dl>

      {state?.directions && Object.keys(state.directions).length > 0 && (
        <>
          <p className="t-label mb-1.5" style={{ color: "var(--text-4)" }}>
            Per direction
          </p>
          <div className="space-y-1">
            {Object.entries(state.directions).map(([dir, d]: [string, any]) => (
              <div key={dir} className="flex items-center justify-between t-small">
                <span className="mono">{dir}</span>
                <span className="num" style={{ color: "var(--text-2)" }}>
                  {fmtBits(d.bps)} · {fmtPercent(d.utilisation)}
                </span>
              </div>
            ))}
          </div>
        </>
      )}

      {state?.state_changed_at && (
        <p className="t-micro mt-2.5" style={{ color: "var(--text-4)" }}>
          State last changed {fmtAge(Date.now() / 1000 - state.state_changed_at)}
        </p>
      )}
      {!state?.state_observed && link.kind === "core" && (
        <p className="t-micro mt-1" style={{ color: "var(--text-4)" }}>
          No state change has been reported for this link, so it is assumed up.
        </p>
      )}
    </Card>
  );
}

function Stat({
  label, value, tone,
}: {
  label: string;
  value: React.ReactNode;
  tone?: "ok" | "warn" | "bad";
}) {
  return (
    <div className="rounded-lg px-2.5 py-1.5" style={{ background: "var(--surface-2)" }}>
      <p className="t-micro" style={{ color: "var(--text-4)" }}>{label}</p>
      <p className="t-h2 num" style={{ color: tone ? `var(--${tone})` : "var(--text-1)" }}>
        {value}
      </p>
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt style={{ color: "var(--text-3)" }}>{k}</dt>
      <dd className="text-right">{v}</dd>
    </div>
  );
}
