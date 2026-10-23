/** Experiment history and exports.
 *
 * Lists every run directory, including interrupted and incomplete ones, so
 * failures are visible rather than quietly absent. Opening a run switches the
 * live views to read that run's recorded data instead of the running one.
 *
 * Raw run output is never copied or modified here; exports are generated on
 * demand from the stored measurements.
 */
import { useMemo, useState } from "react";
import { api, downloadUrls } from "../api/client";
import { useAsync } from "../hooks/useLive";
import type { RunSummary } from "../api/types";
import {
  Button, Card, Empty, ErrorBox, Pill, Skeleton, SourceBadge, StatusDot,
  VARIANT_COLOR, cx, fmtBytes, fmtDateTime, fmtDuration, inputClass, inputStyle,
} from "../components/ui";

const STATUS_TONE: Record<string, "ok" | "warn" | "bad" | "idle"> = {
  running: "ok", complete: "ok", incomplete: "warn", interrupted: "bad",
};

export function HistoryView({
  onOpenRun,
  selectedRunId,
}: {
  onOpenRun: (runId: string | undefined) => void;
  selectedRunId?: string;
}) {
  const { data, loading, error, refresh } = useAsync(() => api.runs(), [], 15000);
  const artifacts = useAsync(() => api.artifacts(), []);
  const [filter, setFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [detailId, setDetailId] = useState<string | null>(null);

  const detail = useAsync(
    () => (detailId ? api.run(detailId) : Promise.resolve(null)),
    [detailId],
  );

  const runs = useMemo(() => {
    let list = data?.runs ?? [];
    if (statusFilter !== "all") list = list.filter((r) => r.status === statusFilter);
    if (filter.trim()) {
      const q = filter.trim().toLowerCase();
      list = list.filter((r) =>
        [r.run_id, r.variant, r.workload, String(r.seed)]
          .some((f) => f.toLowerCase().includes(q)),
      );
    }
    return list;
  }, [data, filter, statusFilter]);

  if (loading && !data) return <Card title="Experiment history"><Skeleton rows={8} /></Card>;
  if (error) return <Card title="Experiment history"><ErrorBox message={error} onRetry={refresh} /></Card>;

  const counts = (data?.runs ?? []).reduce<Record<string, number>>((acc, r) => {
    acc[r.status] = (acc[r.status] ?? 0) + 1;
    return acc;
  }, {});

  return (
    <div className="flex flex-col gap-4">
      {selectedRunId && (
        <div
          className="rounded-xl border px-4 py-2.5 flex items-center justify-between gap-3"
          style={{
            background: "rgba(57,135,229,0.08)",
            borderColor: "rgba(57,135,229,0.35)",
          }}
        >
          <span className="text-[12px]">
            Viewing recorded run <span className="mono">{selectedRunId}</span> —
            the Topology, Flow Tables, RL Decisions and Metrics pages now show
            this run's stored data instead of live telemetry.
          </span>
          <Button variant="ghost" onClick={() => onOpenRun(undefined)}>
            Return to live
          </Button>
        </div>
      )}

      <Card
        title="Runs"
        subtitle={`${data?.total ?? 0} run directories`}
        right={<SourceBadge source="historical" />}
        bodyClassName="p-0"
      >
        <div
          className="flex flex-wrap items-center gap-2 px-4 py-2.5 border-b"
          style={{ borderColor: "var(--border-1)" }}
        >
          <input
            value={filter} onChange={(e) => setFilter(e.target.value)}
            placeholder="Filter by id, variant, workload, seed…"
            className={cx(inputClass, "flex-1 min-w-[200px]")} style={inputStyle}
          />
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
                  className={inputClass} style={inputStyle}>
            <option value="all">All statuses</option>
            <option value="complete">Complete ({counts.complete ?? 0})</option>
            <option value="running">Running ({counts.running ?? 0})</option>
            <option value="incomplete">Incomplete ({counts.incomplete ?? 0})</option>
            <option value="interrupted">Interrupted ({counts.interrupted ?? 0})</option>
          </select>
          <Button variant="ghost" onClick={refresh}>Refresh</Button>
        </div>

        <div className="overflow-auto" style={{ maxHeight: 460 }}>
          {runs.length === 0 ? (
            <Empty title="No runs match" detail="Adjust the filter above." />
          ) : (
            <table className="w-full text-[11px]">
              <thead className="sticky top-0" style={{ background: "var(--surface-2)" }}>
                <tr style={{ color: "var(--text-3)" }}>
                  <Th>Status</Th><Th>Run</Th><Th>Variant</Th><Th>Workload</Th>
                  <Th right>Seed</Th><Th>Started</Th><Th right>Duration</Th>
                  <Th right>Dumps</Th><Th>Commit</Th><Th>Actions</Th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => <RunRow key={r.run_id} run={r}
                                         onOpen={() => onOpenRun(r.run_id)}
                                         onDetail={() => setDetailId(r.run_id)} />)}
              </tbody>
            </table>
          )}
        </div>
      </Card>

      {detailId && (
        <Card
          title={`Run detail — ${detailId}`}
          right={<Button variant="ghost" onClick={() => setDetailId(null)}>Close</Button>}
        >
          {detail.loading ? (
            <Skeleton rows={6} />
          ) : detail.error ? (
            <ErrorBox message={detail.error} />
          ) : detail.data ? (
            <RunDetail data={detail.data as Record<string, any>} runId={detailId} />
          ) : null}
        </Card>
      )}

      <Card
        title="Generated analysis artefacts"
        subtitle={artifacts.data?.note}
      >
        {artifacts.loading ? (
          <Skeleton rows={4} />
        ) : !artifacts.data?.available ? (
          <Empty
            title="No analysis output yet"
            detail="Run analysis/aggregate.py, verify_behaviour.py, plots.py and report.py to generate tables, figures and the report."
          />
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {artifacts.data.items.map((item) => (
              <a
                key={item.name}
                href={downloadUrls.artifact(item.name)}
                target="_blank"
                rel="noreferrer"
                className="rounded-lg border px-3 py-2 flex items-center justify-between gap-2 hover:opacity-80"
                style={{ background: "var(--surface-2)", borderColor: "var(--border-1)" }}
              >
                <div className="min-w-0">
                  <p className="text-[11px] truncate mono">{item.name}</p>
                  <p className="text-[10px]" style={{ color: "var(--text-3)" }}>
                    {item.kind} · {fmtBytes(item.bytes)}
                  </p>
                </div>
                <span style={{ color: "var(--text-3)" }}>↗</span>
              </a>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th className={cx(
      "px-3 py-2 font-medium text-[10px] uppercase tracking-wider whitespace-nowrap",
      right ? "text-right" : "text-left",
    )}>{children}</th>
  );
}

function RunRow({
  run, onOpen, onDetail,
}: {
  run: RunSummary;
  onOpen: () => void;
  onDetail: () => void;
}) {
  return (
    <tr className="border-t hover:bg-white/[0.02]" style={{ borderColor: "var(--border-1)" }}>
      <td className="px-3 py-1.5">
        <span className="inline-flex items-center gap-1.5">
          <StatusDot tone={STATUS_TONE[run.status] ?? "idle"} pulse={run.status === "running"} />
          <span style={{ color: "var(--text-2)" }}>{run.status}</span>
        </span>
      </td>
      <td className="px-3 py-1.5 mono truncate max-w-[220px]" title={run.run_id}>
        {run.run_id}
      </td>
      <td className="px-3 py-1.5">
        <Pill color={VARIANT_COLOR[run.variant]}>{run.variant}</Pill>
      </td>
      <td className="px-3 py-1.5">{run.workload}</td>
      <td className="px-3 py-1.5 num text-right">{run.seed}</td>
      <td className="px-3 py-1.5">{fmtDateTime(run.started_at)}</td>
      <td className="px-3 py-1.5 num text-right">{fmtDuration(run.duration_s)}</td>
      <td className="px-3 py-1.5 num text-right">{run.flow_dump_samples ?? "—"}</td>
      <td className="px-3 py-1.5 mono" style={{ color: "var(--text-3)" }}>
        {run.git_commit ? run.git_commit.slice(0, 7) : "—"}
      </td>
      <td className="px-3 py-1.5">
        <div className="flex gap-1.5">
          <button onClick={onOpen} className="underline underline-offset-2"
                  style={{ color: "#5f9fe8" }}>open</button>
          <button onClick={onDetail} className="underline underline-offset-2"
                  style={{ color: "var(--text-3)" }}>detail</button>
          {run.complete && (
            <a href={downloadUrls.runCsv(run.run_id)} className="underline underline-offset-2"
               style={{ color: "var(--text-3)" }}>csv</a>
          )}
        </div>
      </td>
    </tr>
  );
}

function RunDetail({ data, runId }: { data: Record<string, any>; runId: string }) {
  const config = data.config ?? {};
  const manifest = data.manifest ?? null;
  const provenance = config.provenance ?? {};
  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
      <div>
        <h3 className="text-[11px] font-semibold mb-2" style={{ color: "var(--text-2)" }}>
          Configuration
        </h3>
        <dl className="space-y-1 text-[11px]">
          <KV k="Variant" v={`${config.variant} — ${config.variant_label ?? ""}`} />
          <KV k="Workload" v={config.workload} />
          <KV k="Seed" v={config.seed} />
          <KV k="Duration" v={`${config.duration_s} s`} />
          <KV k="Poll interval" v={`${config.poll_interval_s} s`} />
          <KV k="Fixed idle timeout" v={`${config.fixed_idle_timeout_s} s`} />
          <KV k="Controller app" v={config.controller_app} mono />
        </dl>
        <h3 className="text-[11px] font-semibold mt-3 mb-2" style={{ color: "var(--text-2)" }}>
          Reward weights
        </h3>
        <dl className="space-y-1 text-[11px]">
          {Object.entries(config.reward_weights ?? {}).map(([k, v]) => (
            <KV key={k} k={k} v={String(v)} />
          ))}
        </dl>
      </div>
      <div>
        <h3 className="text-[11px] font-semibold mb-2" style={{ color: "var(--text-2)" }}>
          Provenance
        </h3>
        <dl className="space-y-1 text-[11px]">
          <KV k="Git commit" v={provenance.git_commit?.slice(0, 12) ?? "—"} mono />
          <KV k="Dirty tree" v={String(provenance.git_dirty ?? "—")} />
          <KV k="Python" v={provenance.python} />
          <KV k="Open vSwitch" v={provenance.ovs_version} />
          <KV k="Mininet" v={provenance.mininet_version} />
          <KV k="os-ken" v={provenance.os_ken_version} />
          <KV k="Kernel" v={provenance.kernel} mono />
        </dl>
        <h3 className="text-[11px] font-semibold mt-3 mb-2" style={{ color: "var(--text-2)" }}>
          Artefacts
        </h3>
        {manifest ? (
          <dl className="space-y-1 text-[11px]">
            <KV k="Complete" v={String(manifest.complete)} />
            <KV k="iperf3 files" v={String((manifest.iperf3_json_files ?? []).length)} />
            <KV k="Flow dumps" v={String(manifest.flow_dump_samples ?? 0)} />
          </dl>
        ) : (
          <p className="text-[11px]" style={{ color: "var(--text-3)" }}>
            No manifest — the run did not complete its teardown.
          </p>
        )}
        <div className="mt-3 flex gap-2">
          <a href={downloadUrls.runCsv(runId, "full")}
             className="px-2.5 py-1 rounded border text-[11px]"
             style={{ borderColor: "var(--border-2)", color: "var(--text-2)" }}>
            CSV (full)
          </a>
          <a href={downloadUrls.runCsv(runId, "second_half")}
             className="px-2.5 py-1 rounded border text-[11px]"
             style={{ borderColor: "var(--border-2)", color: "var(--text-2)" }}>
            CSV (second half)
          </a>
        </div>
      </div>
    </div>
  );
}

function KV({ k, v, mono }: { k: string; v: React.ReactNode; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-3">
      <dt style={{ color: "var(--text-3)" }}>{k}</dt>
      <dd className={cx("text-right", mono && "mono")}>{v ?? "—"}</dd>
    </div>
  );
}
