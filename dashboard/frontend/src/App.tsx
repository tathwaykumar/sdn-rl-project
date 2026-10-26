/** SDN Network Intelligence Console.
 *
 * The shell owns navigation and the always-visible connection and provenance
 * state; each page owns its own data. Pages are separate modules and reused
 * rather than duplicated — the Overview embeds the shared topology canvas and
 * links into the other pages instead of reimplementing them.
 */
import { useCallback, useState } from "react";
import { api } from "./api/client";
import { useAsync, useLive } from "./hooks/useLive";
import type { Capabilities, LiveSnapshot } from "./api/types";
import { Shell, type PageId } from "./components/Shell";
import { Pill, SourceBadge, StatusDot, VARIANT_COLOR } from "./components/ui";
import { OverviewView } from "./views/OverviewView";
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
  // With a recorded run open, live-only pages read that run instead.
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
      snapshot={snapshot}
      runBadge={
        <RunBadge snapshot={snapshot} runId={runId}
                  onClear={() => setRunId(undefined)} />
      }
    >
      {page === "overview" && (
        <OverviewView
          snapshot={liveSnapshot} caps={caps.data} runId={runId}
          onNavigate={setPage} onOpenRun={openRun}
        />
      )}
      {page === "topology" && (
        <TopologyView snapshot={liveSnapshot} runId={runId} />
      )}
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
        className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg border t-small"
        style={{ background: "var(--surface-2)", borderColor: "var(--border-2)" }}
      >
        <SourceBadge source="historical" />
        <span className="mono truncate max-w-[160px]" title={runId}>{runId}</span>
        <button onClick={onClear} className="underline shrink-0"
                style={{ color: "var(--accent)" }}>
          back to live
        </button>
      </div>
    );
  }

  const live = snapshot?.live;
  if (!live?.active) {
    return (
      <div
        className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg border t-small"
        style={{ background: "var(--surface-2)", borderColor: "var(--border-1)" }}
      >
        <StatusDot tone="idle" />
        <span style={{ color: "var(--text-3)" }}>No experiment running</span>
      </div>
    );
  }

  return (
    <div
      className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg border t-small"
      style={{
        background: "var(--ok-dim)",
        borderColor: "color-mix(in srgb, var(--ok) 32%, transparent)",
      }}
    >
      <StatusDot tone="ok" pulse />
      <span className="font-semibold"
            style={{ color: VARIANT_COLOR[live.variant ?? "A"] }}>
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
