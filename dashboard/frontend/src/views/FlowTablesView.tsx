/** Flow-table inspector.
 *
 * Shows the rules a switch is actually holding, parsed from the runner's
 * `ovs-ofctl dump-flows` snapshots by the same parser the research analysis
 * uses. Counters, timeouts and durations are reproduced as reported; nothing
 * is computed or estimated here. Where a field is absent from the dump it is
 * shown as "—" rather than filled in.
 */
import { useMemo, useState } from "react";
import { api } from "../api/client";
import { useAsync } from "../hooks/useLive";
import type { FlowRule, FlowTablePayload } from "../api/types";
import {
  Button, Card, Empty, ErrorBox, Pill, Skeleton, SourceBadge,
  cx, fmtBytes, fmtNumber, inputClass, inputStyle,
} from "../components/ui";

const SWITCHES = ["s1", "s2", "s3", "s4", "s5"];

type SortKey = "priority" | "n_packets" | "n_bytes" | "duration_s" | "idle_timeout";

const KIND_STYLE: Record<string, { label: string; color: string; title: string }> = {
  routed: {
    label: "routed",
    color: "#43c98a",
    title: "A forwarding rule the controller installed for a flow (priority ≥ 10).",
  },
  table_miss: {
    label: "table-miss",
    color: "#e0a93a",
    title:
      "The priority-0 catch-all that sends unmatched packets to the controller. In OpenFlow 1.3 this is a real flow entry, which is why table misses are counted from its own packet counter.",
  },
  infrastructure: {
    label: "infra",
    color: "#8b9aab",
    title:
      "Controller plumbing (the IPv6 discard rule). Excluded from occupancy and hit-ratio measurements.",
  },
};

export function FlowTablesView({ runId }: { runId?: string }) {
  const [selected, setSelected] = useState("s1");
  const [query, setQuery] = useState("");
  const [kindFilter, setKindFilter] = useState<string>("all");
  const [sortKey, setSortKey] = useState<SortKey>("priority");
  const [sortDesc, setSortDesc] = useState(true);

  const { data, loading, error, refresh } = useAsync<FlowTablePayload>(
    () => api.flows(undefined, runId),
    [runId],
    5000,
  );
  const occupancy = useAsync(() => api.flowOccupancy(runId, 60), [runId], 10000);

  const rules = useMemo(() => {
    const all = data?.rules ?? [];
    let filtered = all.filter((r) => r.switch === selected);
    if (kindFilter !== "all") filtered = filtered.filter((r) => r.kind === kindFilter);
    if (query.trim()) {
      const q = query.trim().toLowerCase();
      filtered = filtered.filter((r) =>
        [r.nw_src, r.nw_dst, r.proto, r.output_port, r.cookie, r.raw]
          .filter(Boolean)
          .some((f) => String(f).toLowerCase().includes(q)),
      );
    }
    const dir = sortDesc ? -1 : 1;
    return [...filtered].sort((a, b) => {
      const av = (a[sortKey] ?? -1) as number;
      const bv = (b[sortKey] ?? -1) as number;
      return av === bv ? 0 : av < bv ? -dir : dir;
    });
  }, [data, selected, query, kindFilter, sortKey, sortDesc]);

  const counts = data?.counts_by_switch ?? {};
  const timeouts = data?.routed_timeout_distribution ?? {};

  if (loading && !data) return <Card title="Flow tables"><Skeleton rows={8} /></Card>;
  if (error && !data) {
    return <Card title="Flow tables"><ErrorBox message={error} onRetry={refresh} /></Card>;
  }

  if (!data?.available) {
    return (
      <Card
        title="Flow tables"
        right={<SourceBadge source="unavailable" />}
      >
        <Empty
          title="No flow-table data"
          detail={
            data?.reason ??
            "Flow tables are captured by the experiment runner. Start an experiment, or open a completed run from Experiment History."
          }
        />
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-4 min-h-0">
      <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
        {SWITCHES.map((s) => {
          const c = counts[s];
          const active = s === selected;
          return (
            <button
              key={s}
              onClick={() => setSelected(s)}
              className={cx(
                "rounded-xl border px-3 py-2 text-left transition-colors",
                active && "ring-1",
              )}
              style={{
                background: active ? "var(--surface-3)" : "var(--surface-1)",
                borderColor: active ? "#4e9bff" : "var(--border-1)",
              }}
            >
              <div className="flex items-center justify-between">
                <span className="text-[13px] font-semibold">{s}</span>
                <span className="text-[11px] num" style={{ color: "var(--text-2)" }}>
                  {c ? c.total : "—"}
                </span>
              </div>
              <div className="text-[10px] mt-0.5" style={{ color: "var(--text-3)" }}>
                {c ? `${c.routed} routed` : "no data"}
              </div>
            </button>
          );
        })}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[1fr_300px] gap-4 min-h-0">
        <Card
          title={`Flow table — ${selected}`}
          subtitle={data.note}
          right={
            <div className="flex items-center gap-2">
              <SourceBadge source={data.source} age={data.age_s} stale={data.stale} />
              <Button variant="ghost" onClick={refresh}>Refresh</Button>
            </div>
          }
          bodyClassName="p-0 flex flex-col min-h-0"
        >
          <div
            className="flex flex-wrap items-center gap-2 px-4 py-2.5 border-b"
            style={{ borderColor: "var(--border-1)" }}
          >
            <input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter by IP, protocol, port, cookie…"
              className={cx(inputClass, "flex-1 min-w-[180px]")}
              style={inputStyle}
            />
            <select
              value={kindFilter}
              onChange={(e) => setKindFilter(e.target.value)}
              className={inputClass}
              style={inputStyle}
            >
              <option value="all">All rule kinds</option>
              <option value="routed">Routed only</option>
              <option value="table_miss">Table-miss only</option>
              <option value="infrastructure">Infrastructure only</option>
            </select>
            <select
              value={sortKey}
              onChange={(e) => setSortKey(e.target.value as SortKey)}
              className={inputClass}
              style={inputStyle}
            >
              <option value="priority">Sort: priority</option>
              <option value="n_packets">Sort: packets</option>
              <option value="n_bytes">Sort: bytes</option>
              <option value="duration_s">Sort: duration</option>
              <option value="idle_timeout">Sort: idle timeout</option>
            </select>
            <Button variant="ghost" onClick={() => setSortDesc((v) => !v)}>
              {sortDesc ? "↓ desc" : "↑ asc"}
            </Button>
            <span className="text-[11px] num" style={{ color: "var(--text-3)" }}>
              {rules.length} shown
            </span>
          </div>

          <div className="overflow-auto flex-1" style={{ maxHeight: 460 }}>
            {rules.length === 0 ? (
              <Empty title="No rules match" detail="Adjust the filter or pick another switch." />
            ) : (
              <table className="w-full text-[11px]">
                <thead
                  className="sticky top-0"
                  style={{ background: "var(--surface-2)" }}
                >
                  <tr style={{ color: "var(--text-3)" }}>
                    <Th>Kind</Th><Th>Prio</Th><Th>Match</Th><Th>Actions</Th>
                    <Th right>Idle</Th><Th right>Hard</Th><Th right>Age</Th>
                    <Th right>Packets</Th><Th right>Bytes</Th><Th>Cookie</Th>
                  </tr>
                </thead>
                <tbody>
                  {rules.map((r, i) => <RuleRow key={i} rule={r} />)}
                </tbody>
              </table>
            )}
          </div>
        </Card>

        <div className="flex flex-col gap-4">
          <Card title="Idle-timeout distribution" subtitle="Across routed rules on all switches">
            {Object.keys(timeouts).length === 0 ? (
              <Empty title="No routed rules" />
            ) : (
              <div className="space-y-2">
                {Object.entries(timeouts)
                  .sort((a, b) => Number(a[0]) - Number(b[0]))
                  .map(([timeout, count]) => {
                    const total = Object.values(timeouts).reduce((a, b) => a + b, 0);
                    return (
                      <div key={timeout}>
                        <div className="flex justify-between text-[11px] mb-0.5">
                          <span>{timeout}s</span>
                          <span className="num" style={{ color: "var(--text-2)" }}>
                            {count} ({fmtNumber((count / total) * 100, 0)}%)
                          </span>
                        </div>
                        <div className="h-1.5 rounded-full" style={{ background: "var(--surface-3)" }}>
                          <div
                            className="h-full rounded-full"
                            style={{
                              width: `${(count / total) * 100}%`,
                              background: "var(--variant-c)",
                            }}
                          />
                        </div>
                      </div>
                    );
                  })}
                <p className="text-[10px] leading-relaxed pt-1" style={{ color: "var(--text-3)" }}>
                  Variants A and B install a single fixed timeout. Several
                  distinct values here means the agent is choosing them, which
                  only variant C does.
                </p>
              </div>
            )}
          </Card>

          <Card title="Occupancy over time" subtitle="Routed rules per dump">
            {occupancy.data?.history?.length ? (
              <OccupancySpark history={occupancy.data.history} />
            ) : (
              <Empty title="Not enough samples yet" detail="Needs at least one flow dump." />
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th
      className={cx(
        "px-3 py-2 font-medium text-[10px] uppercase tracking-wider whitespace-nowrap",
        right ? "text-right" : "text-left",
      )}
    >
      {children}
    </th>
  );
}

function RuleRow({ rule }: { rule: FlowRule }) {
  const kind = KIND_STYLE[rule.kind] ?? KIND_STYLE.infrastructure;
  const match =
    rule.nw_src && rule.nw_dst
      ? `${rule.proto ?? "ip"} ${rule.nw_src} → ${rule.nw_dst}`
      : rule.kind === "table_miss"
        ? "* (catch-all)"
        : rule.proto ?? "*";
  const action =
    rule.output_port === "CONTROLLER"
      ? "→ CONTROLLER"
      : rule.output_port === "drop"
        ? "drop"
        : rule.output_port
          ? `→ port ${rule.output_port}`
          : "—";
  return (
    <tr
      className="border-t hover:bg-white/[0.02]"
      style={{ borderColor: "var(--border-1)" }}
    >
      <td className="px-3 py-1.5">
        <Pill color={kind.color} title={kind.title}>{kind.label}</Pill>
      </td>
      <td className="px-3 py-1.5 num">{rule.priority}</td>
      <td className="px-3 py-1.5 mono truncate max-w-[220px]" title={rule.raw}>{match}</td>
      <td className="px-3 py-1.5 mono">{action}</td>
      <td className="px-3 py-1.5 num text-right">
        {rule.idle_timeout ?? "—"}
      </td>
      <td className="px-3 py-1.5 num text-right">{rule.hard_timeout ?? "—"}</td>
      <td className="px-3 py-1.5 num text-right">
        {rule.duration_s !== null ? `${fmtNumber(rule.duration_s, 1)}s` : "—"}
      </td>
      <td className="px-3 py-1.5 num text-right">{rule.n_packets.toLocaleString()}</td>
      <td className="px-3 py-1.5 num text-right">{fmtBytes(rule.n_bytes)}</td>
      <td className="px-3 py-1.5 mono" style={{ color: "var(--text-3)" }}>{rule.cookie}</td>
    </tr>
  );
}

function OccupancySpark({
  history,
}: {
  history: { t: number; per_switch: Record<string, number>; total: number }[];
}) {
  const max = Math.max(...history.map((h) => h.total), 1);
  const width = 260;
  const height = 70;
  const step = history.length > 1 ? width / (history.length - 1) : width;
  const points = history
    .map((h, i) => `${i * step},${height - (h.total / max) * height}`)
    .join(" ");
  const latest = history[history.length - 1];
  return (
    <div>
      <svg width="100%" viewBox={`0 0 ${width} ${height}`} preserveAspectRatio="none" height={height}>
        <polyline
          points={points}
          fill="none"
          stroke="var(--variant-a)"
          strokeWidth={2}
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <div className="flex justify-between text-[10px] mt-1" style={{ color: "var(--text-3)" }}>
        <span>{history.length} dumps</span>
        <span className="num">now {latest.total} rules · peak {max}</span>
      </div>
    </div>
  );
}
