/** Application shell and navigation.
 *
 * Connection state and data provenance live in the header, permanently
 * visible. A dashboard that keeps rendering the last frame after its stream
 * dies is worse than one that shows nothing, because stale numbers look
 * exactly like current ones.
 */
import { useState } from "react";
import { api } from "./api/client";
import { useAsync, useLive } from "./hooks/useLive";
import type { Capabilities, LiveSnapshot } from "./api/types";
import {
  Card, Empty, MetricCard, Pill, SourceBadge, StatusDot, VARIANT_COLOR,
  VARIANT_LABEL, cx, fmtAge, fmtNumber, fmtPercent, fmtTime,
} from "./components/ui";
import { TopologyView } from "./views/TopologyView";
import { FlowTablesView } from "./views/FlowTablesView";
import { RLDecisionsView } from "./views/RLDecisionsView";
import { MetricsView } from "./views/MetricsView";
import { ExperimentsView } from "./views/ExperimentsView";
import { ComparisonView } from "./views/ComparisonView";
import { HistoryView } from "./views/HistoryView";

type Tab =
  | "overview" | "topology" | "flows" | "decisions"
  | "metrics" | "experiments" | "comparison" | "history";

const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "topology", label: "Live Topology" },
  { id: "flows", label: "Flow Tables" },
  { id: "decisions", label: "RL Decisions" },
  { id: "metrics", label: "Live Metrics" },
  { id: "experiments", label: "Experiments" },
  { id: "comparison", label: "A/B/C Comparison" },
  { id: "history", label: "History" },
];

export default function App() {
  const [tab, setTab] = useState<Tab>("overview");
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const feed = useLive(true);
  const caps = useAsync<Capabilities>(() => api.capabilities(), [], 30000);

  const snapshot = feed.snapshot;
  const mock = Boolean(caps.data?.mock_mode || snapshot?.mock_mode);

  return (
    <div className="h-full flex flex-col" style={{ background: "var(--surface-0)" }}>
      {mock && (
        <div
          className="px-4 py-1.5 text-[11px] font-medium text-center"
          style={{ background: "rgba(201,133,0,0.2)", color: "#e0b45a" }}
        >
          MOCK MODE — every value shown is synthetic demo data, not a
          measurement of any network.
        </div>
      )}

      <header
        className="border-b px-4 py-2.5 flex items-center gap-4 flex-wrap"
        style={{ background: "var(--surface-1)", borderColor: "var(--border-1)" }}
      >
        <div className="flex items-center gap-2.5">
          <div
            className="w-7 h-7 rounded-lg flex items-center justify-center text-[13px] font-bold"
            style={{ background: "#1f5fae", color: "#fff" }}
          >
            ⬡
          </div>
          <div>
            <h1 className="text-[13px] font-semibold leading-tight">
              SDN + RL Research Dashboard
            </h1>
            <p className="text-[10px]" style={{ color: "var(--text-3)" }}>
              RL-based traffic routing &amp; flow-table optimisation
            </p>
          </div>
        </div>

        <div className="flex-1" />
        <ConnectionPill feed={feed} />
        <RunPill snapshot={snapshot} runId={runId} />
      </header>

      <nav
        className="border-b px-2 flex gap-0.5 overflow-x-auto"
        style={{ background: "var(--surface-1)", borderColor: "var(--border-1)" }}
      >
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={cx(
              "px-3 py-2 text-[12px] font-medium whitespace-nowrap border-b-2 transition-colors",
            )}
            style={{
              borderColor: tab === t.id ? "#4e9bff" : "transparent",
              color: tab === t.id ? "var(--text-1)" : "var(--text-3)",
            }}
          >
            {t.label}
          </button>
        ))}
      </nav>

      <main className="flex-1 overflow-auto p-4 min-h-0">
        {tab === "overview" && (
          <Overview snapshot={snapshot} caps={caps.data} runId={runId} onTab={setTab} />
        )}
        {tab === "topology" && <TopologyView snapshot={runId ? null : snapshot} />}
        {tab === "flows" && <FlowTablesView runId={runId} />}
        {tab === "decisions" && (
          <RLDecisionsView runId={runId} snapshot={runId ? null : snapshot} />
        )}
        {tab === "metrics" && (
          <MetricsView runId={runId} snapshot={runId ? null : snapshot} />
        )}
        {tab === "experiments" && <ExperimentsView />}
        {tab === "comparison" && <ComparisonView />}
        {tab === "history" && (
          <HistoryView onOpenRun={setRunId} selectedRunId={runId} />
        )}
      </main>
    </div>
  );
}

function ConnectionPill({ feed }: { feed: ReturnType<typeof useLive> }) {
  const map = {
    open: { tone: "ok" as const, label: "Live stream" },
    polling: { tone: "warn" as const, label: "Polling (websocket unavailable)" },
    connecting: { tone: "idle" as const, label: "Connecting…" },
    reconnecting: { tone: "warn" as const, label: `Reconnecting (attempt ${feed.attempts})` },
    closed: { tone: "idle" as const, label: "Disconnected" },
  };
  const s = map[feed.state];
  return (
    <div
      className="flex items-center gap-2 px-2.5 py-1 rounded-lg border text-[11px]"
      style={{ background: "var(--surface-2)", borderColor: "var(--border-1)" }}
      title={feed.error ?? undefined}
    >
      <StatusDot tone={s.tone} pulse={feed.state === "open"} />
      <span style={{ color: "var(--text-2)" }}>{s.label}</span>
      {feed.lastUpdate && (
        <span className="num" style={{ color: "var(--text-3)" }}>
          {fmtAge((Date.now() - feed.lastUpdate) / 1000)}
        </span>
      )}
      {(feed.state === "reconnecting" || feed.state === "closed") && (
        <button onClick={feed.reconnectNow} className="underline" style={{ color: "#5f9fe8" }}>
          retry
        </button>
      )}
    </div>
  );
}

function RunPill({
  snapshot, runId,
}: {
  snapshot: LiveSnapshot | null;
  runId?: string;
}) {
  if (runId) {
    return (
      <div
        className="flex items-center gap-2 px-2.5 py-1 rounded-lg border text-[11px]"
        style={{ background: "rgba(57,135,229,0.1)", borderColor: "rgba(57,135,229,0.35)" }}
      >
        <SourceBadge source="historical" />
        <span className="mono truncate max-w-[200px]" title={runId}>{runId}</span>
      </div>
    );
  }
  const live = snapshot?.live;
  if (!live?.active) {
    return (
      <div
        className="flex items-center gap-2 px-2.5 py-1 rounded-lg border text-[11px]"
        style={{ background: "var(--surface-2)", borderColor: "var(--border-1)" }}
      >
        <StatusDot tone="idle" />
        <span style={{ color: "var(--text-3)" }}>No experiment running</span>
      </div>
    );
  }
  return (
    <div
      className="flex items-center gap-2 px-2.5 py-1 rounded-lg border text-[11px]"
      style={{ background: "rgba(46,163,107,0.1)", borderColor: "rgba(46,163,107,0.35)" }}
    >
      <StatusDot tone="ok" pulse />
      <span style={{ color: VARIANT_COLOR[live.variant ?? "A"] }} className="font-semibold">
        {live.variant}
      </span>
      <span style={{ color: "var(--text-2)" }}>
        {live.workload} · seed {live.seed}
      </span>
      {live.matrix_running && <Pill color="var(--warn)">matrix</Pill>}
    </div>
  );
}

function Overview({
  snapshot, caps, runId, onTab,
}: {
  snapshot: LiveSnapshot | null;
  caps?: Capabilities | null;
  runId?: string;
  onTab: (t: Tab) => void;
}) {
  const live = snapshot?.live;
  const metrics = snapshot?.metrics;
  const latency = snapshot?.latency;
  const verification = snapshot?.verification;
  const values = metrics?.values ?? {};
  const active = live?.active && !runId;

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card title="System status" className="lg:col-span-2">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-[12px]">
            <Stat
              label="Experiment"
              value={active ? "running" : "idle"}
              tone={active ? "ok" : "idle"}
              detail={active ? `${live?.variant} / ${live?.workload} / seed ${live?.seed}` : "—"}
            />
            <Stat
              label="Controller"
              value={active ? "connected" : "not running"}
              tone={active ? "ok" : "idle"}
              detail="OpenFlow 1.3 on 127.0.0.1:6653"
            />
            <Stat
              label="API privileges"
              value={caps?.is_root ? "root" : "unprivileged"}
              tone={caps?.is_root ? "warn" : "ok"}
              detail={caps?.can_start_experiments ? "can start runs" : "read-only"}
            />
            <Stat
              label="Data source"
              value={runId ? "recorded run" : active ? "live" : "none"}
              tone={runId ? "idle" : active ? "ok" : "idle"}
              detail={runId ?? (active ? live?.run_id ?? "" : "no telemetry")}
            />
          </div>
          {live?.detail && (
            <p className="text-[11px] mt-3" style={{ color: "var(--text-3)" }}>
              {live.detail}
            </p>
          )}
        </Card>

        <Card title="Variants under test">
          <div className="space-y-2">
            {(["A", "B", "C"] as const).map((v) => (
              <div key={v} className="flex items-start gap-2">
                <span
                  className="w-2.5 h-2.5 rounded-sm mt-1 shrink-0"
                  style={{ background: VARIANT_COLOR[v] }}
                />
                <div>
                  <p className="text-[12px] font-semibold">Variant {v}</p>
                  <p className="text-[10px]" style={{ color: "var(--text-3)" }}>
                    {VARIANT_LABEL[v]}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </Card>
      </div>

      {active ? (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <MetricCard
              label="Throughput" unit="Mbit/s" source="live"
              value={fmtNumber(((values.delivered_bps as number) ?? 0) / 1e6, 2)}
              sub="delivered to hosts" kind="interval_rate"
            />
            <MetricCard
              label="Median RTT" unit="ms"
              source={latency?.available ? "live" : "unavailable"}
              value={latency?.available ? fmtNumber(latency.median_ms, 1) : "—"}
              sub={latency?.available ? `p95 ${fmtNumber(latency.p95_ms, 1)} ms` : "not measured"}
            />
            <MetricCard
              label="Max link utilisation" unit="%" source="live"
              value={fmtNumber(((values.max_link_util as number) ?? 0) * 100, 1)}
              sub={`A ${fmtPercent(values.util_A as number, 0)} · B ${fmtPercent(values.util_B as number, 0)}`}
              tone={((values.max_link_util as number) ?? 0) > 0.9 ? "warn" : undefined}
            />
            <MetricCard
              label="Flow-table occupancy" unit="rules" source="live"
              value={fmtNumber(values.total_rules as number, 0)}
              sub="routed rules, all switches" kind="instantaneous"
            />
          </div>

          <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
            <Card
              title="Latest agent decision"
              right={<SourceBadge source="live" />}
            >
              {snapshot?.decisions?.length ? (
                <LatestDecision snapshot={snapshot} />
              ) : (
                <Empty title="No decisions yet" detail="The first policy step occurs after the first telemetry interval." />
              )}
              {verification?.detail && (
                <p className="text-[11px] mt-3 leading-relaxed" style={{ color: "var(--text-3)" }}>
                  {verification.detail}
                </p>
              )}
              <button onClick={() => onTab("decisions")}
                      className="text-[11px] underline underline-offset-2 mt-2"
                      style={{ color: "#5f9fe8" }}>
                Open RL decision monitor →
              </button>
            </Card>

            <Card title="Recent events" right={<SourceBadge source="live" />}>
              {snapshot?.events?.length ? (
                <div className="space-y-1 max-h-[200px] overflow-auto">
                  {[...snapshot.events].reverse().map((e, i) => (
                    <div key={i} className="flex gap-2 text-[11px]">
                      <span className="num shrink-0" style={{ color: "var(--text-3)" }}>
                        {fmtTime(e.t)}
                      </span>
                      <span className="font-medium shrink-0">{e.kind}</span>
                      <span className="truncate" style={{ color: "var(--text-3)" }} title={e.detail}>
                        {e.detail}
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <Empty title="No events recorded yet" />
              )}
            </Card>
          </div>
        </>
      ) : (
        <Card title="No live telemetry">
          <Empty
            title={runId ? "Viewing a recorded run" : "No experiment is running"}
            detail={
              runId
                ? "Live cards are hidden while a recorded run is selected. Use the Flow Tables, RL Decisions and Metrics pages to inspect it."
                : "Start a run from the Experiments page, or browse completed runs in History. Live metrics, flow tables and agent decisions appear here while an experiment runs."
            }
          />
        </Card>
      )}

      {caps?.notes?.length ? (
        <Card title="How this dashboard reads the system">
          <ul className="space-y-1.5 text-[11px] leading-relaxed" style={{ color: "var(--text-3)" }}>
            {caps.notes.map((n, i) => <li key={i}>• {n}</li>)}
          </ul>
        </Card>
      ) : null}
    </div>
  );
}

function LatestDecision({ snapshot }: { snapshot: LiveSnapshot }) {
  const decisions = snapshot.decisions ?? [];
  const latest = decisions[decisions.length - 1];
  const previous = decisions.length > 1 ? decisions[decisions.length - 2] : null;
  const status = snapshot.verification?.status;
  const tone =
    status === "confirmed" ? "ok" : status === "not_observed" ? "bad" : "warn";
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3 text-[13px]">
        <span style={{ color: "var(--text-3)" }}>
          {previous ? `Path ${previous.path}` : "—"}
        </span>
        <span style={{ color: "var(--text-3)" }}>→</span>
        <span className="font-semibold">Path {latest.path}</span>
        {latest.idle_timeout !== null && (
          <Pill color="var(--variant-c)">{latest.idle_timeout}s idle</Pill>
        )}
      </div>
      <div className="flex items-center gap-2 text-[11px]">
        <StatusDot tone={tone} pulse={status === "awaiting_confirmation"} />
        <span style={{ color: "var(--text-2)" }}>
          {status === "confirmed"
            ? "Confirmed on switches"
            : status === "awaiting_confirmation"
              ? "Awaiting switch confirmation"
              : status === "not_observed"
                ? "Not observed on switches"
                : "Unverified"}
        </span>
      </div>
      <div className="flex gap-4 text-[11px]" style={{ color: "var(--text-3)" }}>
        <span>step {latest.step}</span>
        <span className="num">reward {fmtNumber(latest.reward, 3)}</span>
        {latest.epsilon !== null && <span className="num">ε {fmtNumber(latest.epsilon, 3)}</span>}
        <span>{latest.exploratory ? "exploratory" : "greedy"}</span>
      </div>
    </div>
  );
}

function Stat({
  label, value, detail, tone,
}: {
  label: string;
  value: string;
  detail?: string;
  tone: "ok" | "warn" | "bad" | "idle";
}) {
  return (
    <div>
      <p className="text-[10px]" style={{ color: "var(--text-3)" }}>{label}</p>
      <p className="flex items-center gap-1.5 font-medium">
        <StatusDot tone={tone} />
        {value}
      </p>
      {detail && (
        <p className="text-[10px] truncate mt-0.5" style={{ color: "var(--text-3)" }} title={detail}>
          {detail}
        </p>
      )}
    </div>
  );
}
