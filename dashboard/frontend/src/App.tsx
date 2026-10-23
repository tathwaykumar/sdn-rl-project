/** SDN Network Operations & RL Analytics Console.
 *
 * The shell owns navigation and the always-visible connection/provenance
 * state; each page owns its own data. Pages are kept as separate modules and
 * reused rather than duplicated — the Overview links into them instead of
 * reimplementing their content.
 */
import { useCallback, useState } from "react";
import { api } from "./api/client";
import { useAsync, useLive } from "./hooks/useLive";
import type { Capabilities, LiveSnapshot } from "./api/types";
import { Shell, type PageId } from "./components/Shell";
import {
  Card, Empty, MetricCard, Pill, SourceBadge, StatusDot, VARIANT_COLOR,
  VARIANT_LABEL, fmtNumber, fmtPercent, fmtTime,
} from "./components/ui";
import { TopologyView } from "./views/TopologyView";
import { FlowTablesView } from "./views/FlowTablesView";
import { MetricsView } from "./views/MetricsView";
import { RewardExplorerView } from "./views/RewardExplorerView";
import { DecisionInspectorView } from "./views/DecisionInspectorView";
import { ExperimentsView } from "./views/ExperimentsView";
import { ComparisonView } from "./views/ComparisonView";
import { HistoryView } from "./views/HistoryView";

export default function App() {
  const [page, setPage] = useState<PageId>("overview");
  const [runId, setRunId] = useState<string | undefined>(undefined);
  const feed = useLive(true);
  const caps = useAsync<Capabilities>(() => api.capabilities(), [], 30000);

  const snapshot = feed.snapshot;
  const mock = Boolean(caps.data?.mock_mode || snapshot?.mock_mode);
  // When a recorded run is open, live-only pages read that run instead.
  const liveSnapshot = runId ? null : snapshot;

  const openRun = useCallback((id: string | undefined) => {
    setRunId(id);
    if (id) setPage("metrics");
  }, []);

  return (
    <Shell
      page={page}
      onNavigate={setPage}
      connection={feed.state}
      lastUpdate={feed.lastUpdate}
      connectionError={feed.error}
      onReconnect={feed.reconnectNow}
      mock={mock}
      runBadge={<RunBadge snapshot={snapshot} runId={runId} onClear={() => setRunId(undefined)} />}
    >
      {page === "overview" && (
        <Overview
          snapshot={liveSnapshot} caps={caps.data} runId={runId} onNavigate={setPage}
        />
      )}
      {page === "topology" && <TopologyView snapshot={liveSnapshot} />}
      {page === "metrics" && (
        <div className="space-y-4">
          <MetricsView runId={runId} snapshot={liveSnapshot} />
          <FlowTablesView runId={runId} />
        </div>
      )}
      {page === "reward" && <RewardExplorerView runId={runId} />}
      {page === "decisions" && (
        <DecisionInspectorView runId={runId} snapshot={liveSnapshot} />
      )}
      {page === "experiments" && <ExperimentsView />}
      {page === "comparison" && <ComparisonView />}
      {page === "history" && (
        <HistoryView onOpenRun={openRun} selectedRunId={runId} />
      )}
    </Shell>
  );
}

function RunBadge({
  snapshot, runId, onClear,
}: {
  snapshot: LiveSnapshot | null;
  runId?: string;
  onClear: () => void;
}) {
  if (runId) {
    return (
      <div
        className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg border text-[11px]"
        style={{ background: "var(--surface-2)", borderColor: "var(--border-2)" }}
      >
        <SourceBadge source="historical" />
        <span className="mono truncate max-w-[180px]" title={runId}>{runId}</span>
        <button
          onClick={onClear}
          className="underline shrink-0"
          style={{ color: "var(--accent)" }}
        >
          back to live
        </button>
      </div>
    );
  }

  const live = snapshot?.live;
  if (!live?.active) {
    return (
      <div
        className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg border text-[11px]"
        style={{ background: "var(--surface-2)", borderColor: "var(--border-1)" }}
      >
        <StatusDot tone="idle" />
        <span style={{ color: "var(--text-3)" }}>No experiment running</span>
      </div>
    );
  }

  return (
    <div
      className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg border text-[11px]"
      style={{ background: "var(--ok-dim)", borderColor: "rgba(52,211,153,0.3)" }}
    >
      <StatusDot tone="ok" pulse />
      <span className="font-semibold" style={{ color: VARIANT_COLOR[live.variant ?? "A"] }}>
        {live.variant}
      </span>
      <span style={{ color: "var(--text-2)" }} className="truncate">
        {live.workload} · seed {live.seed}
      </span>
      {live.elapsed_s !== null && live.duration_s && (
        <span className="num" style={{ color: "var(--text-3)" }}>
          {Math.round(live.elapsed_s)}/{Math.round(live.duration_s)}s
        </span>
      )}
      {live.matrix_running && <Pill color="var(--warn)">matrix</Pill>}
    </div>
  );
}

function Overview({
  snapshot, caps, runId, onNavigate,
}: {
  snapshot: LiveSnapshot | null;
  caps?: Capabilities | null;
  runId?: string;
  onNavigate: (p: PageId) => void;
}) {
  const live = snapshot?.live;
  const metrics = snapshot?.metrics;
  const latency = snapshot?.latency;
  const verification = snapshot?.verification;
  const values = metrics?.values ?? {};
  const active = Boolean(live?.active) && !runId;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card title="System status" className="lg:col-span-2">
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <Stat
              label="Experiment" value={active ? "Running" : "Idle"}
              tone={active ? "ok" : "idle"}
              detail={active ? `${live?.variant} · ${live?.workload} · seed ${live?.seed}` : "nothing in flight"}
            />
            <Stat
              label="Controller" value={active ? "Connected" : "Not running"}
              tone={active ? "ok" : "idle"} detail="OpenFlow 1.3 · 127.0.0.1:6653"
            />
            <Stat
              label="Experiment launching"
              value={
                caps?.start_mechanism === "launcher" ? "Enabled"
                  : caps?.start_mechanism === "root_api" ? "Root API"
                    : "Unavailable"
              }
              tone={caps?.start_mechanism === "launcher" ? "ok"
                : caps?.start_mechanism === "root_api" ? "warn" : "idle"}
              detail={
                caps?.start_mechanism === "launcher"
                  ? "privileged launcher connected"
                  : caps?.start_mechanism === "root_api"
                    ? "API running as root"
                    : "launcher not running"
              }
            />
            <Stat
              label="Data source"
              value={runId ? "Recorded" : active ? "Live" : "None"}
              tone={runId ? "idle" : active ? "ok" : "idle"}
              detail={runId ?? (active ? live?.run_id ?? "" : "no telemetry")}
            />
          </div>
          {live?.detail && (
            <p className="text-[11.5px] mt-3" style={{ color: "var(--text-3)" }}>
              {live.detail}
            </p>
          )}
        </Card>

        <Card title="Variants under test">
          <div className="space-y-2.5">
            {(["A", "B", "C"] as const).map((v) => (
              <div key={v} className="flex items-start gap-2.5">
                <span
                  className="w-2.5 h-2.5 rounded-sm mt-1 shrink-0"
                  style={{ background: VARIANT_COLOR[v] }}
                  aria-hidden="true"
                />
                <div className="min-w-0">
                  <p className="text-[12px] font-semibold">Variant {v}</p>
                  <p className="text-[10.5px] leading-snug" style={{ color: "var(--text-3)" }}>
                    {VARIANT_LABEL[v]}
                  </p>
                </div>
              </div>
            ))}
          </div>
          <button
            onClick={() => onNavigate("comparison")}
            className="text-[11px] underline underline-offset-2 mt-3"
            style={{ color: "var(--accent)" }}
          >
            Compare measured results →
          </button>
        </Card>
      </div>

      {active ? (
        <>
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            <MetricCard
              label="Throughput" unit="Mbit/s" source="live" kind="interval_rate"
              value={fmtNumber(((values.delivered_bps as number) ?? 0) / 1e6, 2)}
              sub="delivered to hosts" direction="higher_is_better"
            />
            <MetricCard
              label="Latency (median)" unit="ms"
              source={latency?.available ? "live" : "unavailable"}
              value={latency?.available ? fmtNumber(latency.median_ms, 1) : "N/A"}
              sub={latency?.available ? `p95 ${fmtNumber(latency.p95_ms, 1)} ms` : "no probe data"}
              direction="lower_is_better"
            />
            <MetricCard
              label="Max link utilisation" unit="%" source="live" kind="interval_rate"
              value={fmtNumber(((values.max_link_util as number) ?? 0) * 100, 1)}
              sub={`A ${fmtPercent(values.util_A as number, 0)} · B ${fmtPercent(values.util_B as number, 0)}`}
              tone={((values.max_link_util as number) ?? 0) > 0.9 ? "warn" : undefined}
              direction="context"
            />
            <MetricCard
              label="Flow-table occupancy" unit="rules" source="live" kind="instantaneous"
              value={fmtNumber(values.total_rules as number, 0)}
              sub="installed across all switches" direction="lower_is_better"
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
                <Empty
                  title="No decisions yet"
                  detail="The first policy step happens after the first telemetry interval."
                />
              )}
              {verification?.detail && (
                <p className="text-[11px] mt-3 leading-relaxed" style={{ color: "var(--text-3)" }}>
                  {verification.detail}
                </p>
              )}
              <div className="flex gap-3 mt-2">
                <button onClick={() => onNavigate("decisions")}
                        className="text-[11px] underline underline-offset-2"
                        style={{ color: "var(--accent)" }}>
                  Decision Inspector →
                </button>
                <button onClick={() => onNavigate("reward")}
                        className="text-[11px] underline underline-offset-2"
                        style={{ color: "var(--rl)" }}>
                  Reward Explorer →
                </button>
              </div>
            </Card>

            <Card title="Recent events" right={<SourceBadge source="live" />}>
              {snapshot?.events?.length ? (
                <div className="space-y-1 max-h-[210px] overflow-auto">
                  {[...snapshot.events].reverse().map((e, i) => (
                    <div key={i} className="flex gap-2 text-[11px]">
                      <span className="num shrink-0" style={{ color: "var(--text-4)" }}>
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
        <Card title={runId ? "Viewing a recorded run" : "No live telemetry"}>
          <Empty
            title={runId ? "Live cards are hidden" : "No experiment is running"}
            detail={
              runId
                ? "A recorded run is open. Use Metrics, Reward Explorer and Decision Inspector to inspect it, or return to live from the header."
                : "Start a run from Experiments, or open a completed one from History. Live metrics, decisions and topology appear here while an experiment runs."
            }
            action={
              <div className="flex gap-2 mt-1">
                <button onClick={() => onNavigate("experiments")}
                        className="px-3 py-1.5 rounded-lg border text-[12px] font-medium"
                        style={{ background: "var(--accent-dim)", borderColor: "var(--accent-ring)",
                                 color: "var(--accent)" }}>
                  Go to Experiments
                </button>
                <button onClick={() => onNavigate("history")}
                        className="px-3 py-1.5 rounded-lg border text-[12px]"
                        style={{ borderColor: "var(--border-2)", color: "var(--text-2)" }}>
                  Browse History
                </button>
              </div>
            }
          />
        </Card>
      )}

      {caps?.notes?.length ? (
        <Card title="How this console reads the system">
          <ul className="space-y-1.5 text-[11.5px] leading-relaxed" style={{ color: "var(--text-3)" }}>
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
  const tone = status === "confirmed" ? "ok" : status === "not_observed" ? "bad" : "warn";
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-3 text-[13px]">
        <span style={{ color: "var(--text-3)" }}>
          {previous ? `Path ${previous.path}` : "—"}
        </span>
        <span style={{ color: "var(--text-4)" }}>→</span>
        <span className="font-semibold">Path {latest.path}</span>
        {latest.idle_timeout !== null && (
          <Pill color="var(--rl)">{latest.idle_timeout}s idle</Pill>
        )}
      </div>
      <div className="flex items-center gap-2 text-[11.5px]">
        <StatusDot tone={tone} pulse={status === "awaiting_confirmation"} />
        <span style={{ color: "var(--text-2)" }}>
          {status === "confirmed" ? "Confirmed on switches"
            : status === "awaiting_confirmation" ? "Awaiting switch confirmation"
              : status === "not_observed" ? "Not observed on switches"
                : "Unverified"}
        </span>
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px]" style={{ color: "var(--text-3)" }}>
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
      <p className="text-[10.5px]" style={{ color: "var(--text-4)" }}>{label}</p>
      <p className="flex items-center gap-1.5 font-semibold text-[13px]">
        <StatusDot tone={tone} />
        {value}
      </p>
      {detail && (
        <p className="text-[10px] truncate mt-0.5" style={{ color: "var(--text-4)" }}
           title={detail}>
          {detail}
        </p>
      )}
    </div>
  );
}
