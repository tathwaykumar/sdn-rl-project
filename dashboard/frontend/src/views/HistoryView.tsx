/** Experiment explorer.
 *
 * Replaces the dense table-first view with summary statistics, filters, and
 * expandable run cards — while keeping every technical detail one click away
 * rather than removing it. Provenance (git commit, package versions, reward
 * weights) stays fully accessible, because it is what makes a run reproducible.
 */
import { useMemo, useState } from "react";
import { api, downloadUrls } from "../api/client";
import { useAsync } from "../hooks/useLive";
import type { RunSummary } from "../api/types";
import {
  Button, Card, Details, Empty, ErrorBox, Pill, SectionTitle, Skeleton,
  StatusDot, VARIANT_COLOR, cx, fmtBytes, fmtDuration, inputClass, inputStyle,
} from "../components/ui";

const STATUS_TONE: Record<string, "ok" | "warn" | "bad" | "idle"> = {
  running: "ok", complete: "ok", incomplete: "warn", interrupted: "bad",
};

export function HistoryView({
  onOpenRun, selectedRunId,
}: {
  onOpenRun: (runId: string | undefined) => void;
  selectedRunId?: string;
}) {
  const { data, loading, error, refresh } = useAsync(() => api.runs(), [], 15000);
  const artifacts = useAsync(() => api.artifacts(), []);
  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [variantFilter, setVariantFilter] = useState("all");
  const [expanded, setExpanded] = useState<string | null>(null);

  const all = data?.runs ?? [];

  const stats = useMemo(() => {
    const byStatus: Record<string, number> = {};
    const byVariant: Record<string, number> = {};
    let matrix = 0;
    for (const r of all) {
      byStatus[r.status] = (byStatus[r.status] ?? 0) + 1;
      byVariant[r.variant] = (byVariant[r.variant] ?? 0) + 1;
      // Untagged directories are the controlled sweep.
      if (/^\d{8}-\d{6}-[A-C]-[a-z]+-seed\d+$/.test(r.run_id)) matrix += 1;
    }
    return { byStatus, byVariant, matrix, total: all.length };
  }, [all]);

  const runs = useMemo(() => {
    let list = all;
    if (statusFilter !== "all") list = list.filter((r) => r.status === statusFilter);
    if (variantFilter !== "all") list = list.filter((r) => r.variant === variantFilter);
    if (query.trim()) {
      const q = query.trim().toLowerCase();
      list = list.filter((r) =>
        [r.run_id, r.variant, r.workload, String(r.seed)]
          .some((f) => f.toLowerCase().includes(q)),
      );
    }
    return list;
  }, [all, query, statusFilter, variantFilter]);

  if (loading && !data) return <Card title="Experiments"><Skeleton rows={8} /></Card>;
  if (error) return <Card title="Experiments"><ErrorBox message={error} onRetry={refresh} /></Card>;

  return (
    <div className="space-y-4">
      {selectedRunId && (
        <div
          className="panel px-4 py-3 flex flex-wrap items-center justify-between gap-3"
          style={{
            borderColor: "var(--accent-ring)",
            backgroundImage: "linear-gradient(100deg, var(--accent-dim), transparent 55%)",
          }}
        >
          <span className="t-body">
            Viewing recorded run <span className="mono">{selectedRunId}</span>.
            Metrics, Reward Explorer and Decision Inspector now read this run.
          </span>
          <Button variant="ghost" onClick={() => onOpenRun(undefined)}>
            Return to live
          </Button>
        </div>
      )}

      <section>
        <SectionTitle hint="Every run directory on disk, including interrupted ones">
          Summary
        </SectionTitle>
        <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-3">
          <SummaryTile label="Total runs" value={stats.total} />
          <SummaryTile label="Primary matrix" value={stats.matrix}
                       hint="untagged controlled sweep" tone="ok" />
          <SummaryTile label="Complete" value={stats.byStatus.complete ?? 0} tone="ok" />
          <SummaryTile label="Interrupted" value={stats.byStatus.interrupted ?? 0}
                       tone={(stats.byStatus.interrupted ?? 0) > 0 ? "bad" : undefined} />
          <SummaryTile label="Incomplete" value={stats.byStatus.incomplete ?? 0}
                       tone={(stats.byStatus.incomplete ?? 0) > 0 ? "warn" : undefined} />
        </div>
      </section>

      <section>
        <SectionTitle right={<Button variant="ghost" onClick={refresh}>Refresh</Button>}>
          Explore
        </SectionTitle>

        <div className="panel px-3 py-2.5 mb-3 flex flex-wrap items-center gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search by run id, variant, workload or seed…"
            className={cx(inputClass, "flex-1 min-w-[220px]")}
            style={inputStyle}
            aria-label="Search runs"
          />
          <select value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
                  className={inputClass} style={inputStyle} aria-label="Filter by status">
            <option value="all">All statuses</option>
            {["complete", "running", "incomplete", "interrupted"].map((s) => (
              <option key={s} value={s}>
                {s} ({stats.byStatus[s] ?? 0})
              </option>
            ))}
          </select>
          <div className="flex gap-1">
            {["all", "A", "B", "C"].map((v) => (
              <button
                key={v}
                onClick={() => setVariantFilter(v)}
                className="px-2.5 py-1.5 rounded-lg border t-small font-medium transition-colors"
                style={{
                  background: variantFilter === v ? "var(--surface-3)" : "var(--surface-2)",
                  borderColor: variantFilter === v
                    ? (v === "all" ? "var(--accent)" : VARIANT_COLOR[v])
                    : "var(--border-2)",
                  color: variantFilter === v
                    ? (v === "all" ? "var(--accent)" : VARIANT_COLOR[v])
                    : "var(--text-3)",
                }}
              >
                {v === "all" ? "All" : v}
              </button>
            ))}
          </div>
          <span className="t-small num ml-auto" style={{ color: "var(--text-4)" }}>
            {runs.length} shown
          </span>
        </div>

        {runs.length === 0 ? (
          <Card><Empty title="No runs match" detail="Adjust the search or filters." /></Card>
        ) : (
          <div className="space-y-2">
            {runs.map((run) => (
              <RunCard
                key={run.run_id}
                run={run}
                expanded={expanded === run.run_id}
                onToggle={() => setExpanded(expanded === run.run_id ? null : run.run_id)}
                onOpen={() => onOpenRun(run.run_id)}
                isSelected={selectedRunId === run.run_id}
              />
            ))}
          </div>
        )}
      </section>

      <section>
        <SectionTitle hint={artifacts.data?.note}>Generated artefacts</SectionTitle>
        {artifacts.loading ? (
          <Card><Skeleton rows={3} /></Card>
        ) : !artifacts.data?.available ? (
          <Card>
            <Empty
              title="No analysis output yet"
              detail="Run analysis/aggregate.py, verify_behaviour.py, plots.py and report.py to generate tables, figures and the report."
            />
          </Card>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
            {artifacts.data.items.map((item) => (
              <a
                key={item.name}
                href={downloadUrls.artifact(item.name)}
                target="_blank"
                rel="noreferrer"
                className="panel panel-hover px-3 py-2.5 flex items-center justify-between gap-2"
              >
                <span className="min-w-0">
                  <span className="t-small mono block truncate">{item.name}</span>
                  <span className="t-micro" style={{ color: "var(--text-4)" }}>
                    {item.kind} · {fmtBytes(item.bytes)}
                  </span>
                </span>
                <span style={{ color: "var(--text-4)" }} aria-hidden="true">↗</span>
              </a>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function SummaryTile({
  label, value, hint, tone,
}: {
  label: string;
  value: number;
  hint?: string;
  tone?: "ok" | "warn" | "bad";
}) {
  return (
    <div className="panel px-3.5 py-3">
      <p className="t-small" style={{ color: "var(--text-3)" }}>{label}</p>
      <p className="num" style={{
        fontSize: 24, fontWeight: 640, letterSpacing: "-0.02em",
        color: tone ? `var(--${tone})` : "var(--text-1)",
      }}>
        {value}
      </p>
      {hint && <p className="t-micro" style={{ color: "var(--text-4)" }}>{hint}</p>}
    </div>
  );
}

function RunCard({
  run, expanded, onToggle, onOpen, isSelected,
}: {
  run: RunSummary;
  expanded: boolean;
  onToggle: () => void;
  onOpen: () => void;
  isSelected: boolean;
}) {
  const detail = useAsync(
    () => (expanded ? api.run(run.run_id) : Promise.resolve(null)),
    [expanded, run.run_id],
  );
  const tone = STATUS_TONE[run.status] ?? "idle";
  const isMatrix = /^\d{8}-\d{6}-[A-C]-[a-z]+-seed\d+$/.test(run.run_id);

  return (
    <div
      className={cx("panel panel-hover overflow-hidden")}
      style={isSelected ? { borderColor: "var(--accent-ring)" } : undefined}
    >
      <div className="flex flex-wrap items-center gap-3 px-4 py-3">
        <span
          className="w-1 h-9 rounded-full shrink-0"
          style={{ background: VARIANT_COLOR[run.variant] }}
          aria-hidden="true"
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="t-h2">Variant {run.variant}</span>
            <Pill>{run.workload}</Pill>
            <span className="t-small num" style={{ color: "var(--text-3)" }}>
              seed {run.seed}
            </span>
            {!isMatrix && (
              <Pill color="var(--warn)" title="Not part of the controlled 60-run matrix">
                ad-hoc
              </Pill>
            )}
          </div>
          <p className="t-micro mono truncate mt-0.5" style={{ color: "var(--text-4)" }}>
            {run.run_id}
          </p>
        </div>

        <div className="hidden md:flex items-center gap-5 t-small shrink-0"
             style={{ color: "var(--text-3)" }}>
          <span className="num">{fmtDuration(run.duration_s)}</span>
          <span className="num">{run.flow_dump_samples ?? 0} dumps</span>
          <span className="num">{run.iperf_files} iperf</span>
        </div>

        <span className="flex items-center gap-1.5 shrink-0">
          <StatusDot tone={tone} pulse={run.status === "running"} />
          <span className="t-small" style={{ color: "var(--text-2)" }}>{run.status}</span>
        </span>

        <div className="flex items-center gap-1.5 shrink-0">
          <Button variant="ghost" onClick={onOpen}>Open</Button>
          <button
            onClick={onToggle}
            aria-expanded={expanded}
            aria-label={expanded ? "Hide details" : "Show details"}
            className="px-2 py-1.5 rounded-lg border t-small"
            style={{ borderColor: "var(--border-2)", color: "var(--text-3)" }}
          >
            {expanded ? "▴" : "▾"}
          </button>
        </div>
      </div>

      {expanded && (
        <div className="px-4 pb-4 pt-1 border-t rise"
             style={{ borderColor: "var(--hairline)" }}>
          {detail.loading ? (
            <Skeleton rows={4} />
          ) : detail.error ? (
            <ErrorBox message={detail.error} />
          ) : detail.data ? (
            <RunDetail data={detail.data as Record<string, any>} runId={run.run_id} />
          ) : null}
        </div>
      )}
    </div>
  );
}

function RunDetail({ data, runId }: { data: Record<string, any>; runId: string }) {
  const config = data.config ?? {};
  const manifest = data.manifest ?? null;
  const provenance = config.provenance ?? {};

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 pt-2">
      <div>
        <p className="t-label mb-1.5" style={{ color: "var(--text-4)" }}>Configuration</p>
        <dl className="space-y-1 t-small">
          <KV k="Variant" v={`${config.variant} — ${config.variant_label ?? ""}`} />
          <KV k="Workload" v={config.workload} />
          <KV k="Seed" v={config.seed} />
          <KV k="Duration" v={`${config.duration_s} s`} />
          <KV k="Poll interval" v={`${config.poll_interval_s} s`} />
          <KV k="Fixed idle timeout" v={`${config.fixed_idle_timeout_s} s`} />
        </dl>
      </div>

      <div>
        <p className="t-label mb-1.5" style={{ color: "var(--text-4)" }}>Provenance</p>
        <dl className="space-y-1 t-small">
          <KV k="Git commit"
              v={provenance.git_commit
                ? <span className="mono">{String(provenance.git_commit).slice(0, 12)}</span>
                : <span style={{ color: "var(--warn)" }}>not recorded</span>} />
          <KV k="Dirty tree" v={String(provenance.git_dirty ?? "unknown")} />
          <KV k="Python" v={provenance.python} />
          <KV k="Open vSwitch" v={provenance.ovs_version} />
          <KV k="Mininet" v={provenance.mininet_version} />
          <KV k="os-ken" v={provenance.os_ken_version} />
        </dl>
      </div>

      <div>
        <p className="t-label mb-1.5" style={{ color: "var(--text-4)" }}>Artefacts</p>
        {manifest ? (
          <dl className="space-y-1 t-small">
            <KV k="Complete" v={String(manifest.complete)} />
            <KV k="iperf3 files" v={String((manifest.iperf3_json_files ?? []).length)} />
            <KV k="Flow dumps" v={String(manifest.flow_dump_samples ?? 0)} />
          </dl>
        ) : (
          <p className="t-small" style={{ color: "var(--text-3)" }}>
            No manifest — the run did not complete its teardown.
          </p>
        )}
        <div className="mt-2.5 flex flex-wrap gap-1.5">
          <a href={downloadUrls.runCsv(runId, "full")}
             className="px-2.5 py-1 rounded-lg border t-small"
             style={{ borderColor: "var(--border-2)", color: "var(--text-2)" }}>
            CSV · full run
          </a>
          <a href={downloadUrls.runCsv(runId, "second_half")}
             className="px-2.5 py-1 rounded-lg border t-small"
             style={{ borderColor: "var(--border-2)", color: "var(--text-2)" }}>
            CSV · second half
          </a>
        </div>

        <Details summary="Reward weights used by this run">
          <dl className="space-y-1">
            {Object.entries(config.reward_weights ?? {}).map(([k, v]) => (
              <KV key={k} k={k} v={String(v)} />
            ))}
          </dl>
        </Details>
      </div>
    </div>
  );
}

function KV({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3">
      <dt style={{ color: "var(--text-3)" }}>{k}</dt>
      <dd className="text-right">{v ?? "—"}</dd>
    </div>
  );
}
