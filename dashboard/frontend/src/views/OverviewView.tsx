/** Overview — the product's front page.
 *
 * The topology is the focal point, not a thumbnail between cards. Everything
 * else on this page supports reading it: headline metrics above, a live event
 * timeline beside it, recent runs below.
 *
 * Two rules the visuals follow, because they are what make the page honest:
 *   - A link is only drawn as carrying traffic when rules for its path are
 *     installed on the switches. The agent choosing a path never colours it.
 *   - Packet-flow animation runs only where telemetry reports utilisation.
 *     An idle network looks idle.
 */
import { useMemo } from "react";
import { api } from "../api/client";
import { useAsync } from "../hooks/useLive";
import type { Capabilities, LiveSnapshot, RunSummary } from "../api/types";
import type { PageId } from "../components/Shell";
import {
  Card, Empty, HeroMetric, Pill, SectionTitle, SourceBadge, StatusDot,
  VARIANT_COLOR, VARIANT_LABEL, fmtDuration, fmtNumber, fmtTime,
} from "../components/ui";
import { TopologyCanvas } from "./TopologyCanvas";

export function OverviewView({
  snapshot, caps, runId, onNavigate, onOpenRun,
}: {
  snapshot: LiveSnapshot | null;
  caps?: Capabilities | null;
  runId?: string;
  onNavigate: (p: PageId) => void;
  onOpenRun: (id: string) => void;
}) {
  const live = snapshot?.live;
  const active = Boolean(live?.active) && !runId;
  const metrics = snapshot?.metrics;
  const latency = snapshot?.latency;
  const values = metrics?.values ?? {};

  const runs = useAsync(() => api.runs(), [], 30000);
  const series = useAsync(
    () => api.metricsSeries(undefined, 60),
    [],
    active ? 6000 : undefined,
  );

  // Sparklines come from the same telemetry the cards read; no synthesis.
  const sparks = useMemo(() => {
    const pts = (series.data?.points ?? []).filter((p) => p.valid);
    const grab = (k: string) =>
      pts.map((p) => Number(p[k] ?? 0)).filter((n) => Number.isFinite(n));
    return {
      throughput: grab("delivered_bps"),
      util: grab("max_link_util"),
      rules: grab("total_rules"),
    };
  }, [series.data]);

  const recent = (runs.data?.runs ?? []).slice(0, 6);

  return (
    <div className="space-y-5">
      <StatusBanner
        live={live} active={active} runId={runId} caps={caps}
        onNavigate={onNavigate}
      />

      <section>
        <SectionTitle hint="Measured from the running experiment's own telemetry">
          Live performance
        </SectionTitle>
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
          <HeroMetric
            label="Throughput"
            value={active ? fmtNumber(((values.delivered_bps as number) ?? 0) / 1e6, 2) : "N/A"}
            unit="Mbit/s"
            sub={active ? "delivered to hosts" : "no experiment running"}
            source={active ? "live" : "unavailable"}
            direction="higher_is_better"
            spark={active ? sparks.throughput : undefined}
            explain="Bits per second reaching the destination hosts, from edge-switch egress counters."
          />
          <HeroMetric
            label="Latency (median)"
            value={latency?.available ? fmtNumber(latency.median_ms, 1) : "N/A"}
            unit="ms"
            sub={
              latency?.available
                ? `p95 ${fmtNumber(latency.p95_ms, 1)} ms`
                : "no probe data"
            }
            source={latency?.available ? "live" : "unavailable"}
            direction="lower_is_better"
            explain="Round-trip time of the host's own ICMP probe, h1 to h3."
          />
          <HeroMetric
            label="Packet loss"
            value={
              latency?.available && latency.loss_fraction != null
                ? fmtNumber(latency.loss_fraction * 100, 2)
                : "N/A"
            }
            unit="%"
            sub={
              latency?.available
                ? `${latency.lost ?? 0} of ${latency.probes_sent ?? 0} probes`
                : "no probe data"
            }
            tone={
              (latency?.loss_fraction ?? 0) > 0.05
                ? "bad"
                : (latency?.loss_fraction ?? 0) > 0.01
                  ? "warn"
                  : undefined
            }
            source={latency?.available ? "live" : "unavailable"}
            direction="lower_is_better"
            explain="Probes that never received a reply. Late replies count as delayed, not lost."
          />
          <HeroMetric
            label="Flow-table occupancy"
            value={active ? fmtNumber(values.total_rules as number, 0) : "N/A"}
            unit="rules"
            sub={active ? "installed across all switches" : "no experiment running"}
            source={active ? "live" : "unavailable"}
            direction="lower_is_better"
            spark={active ? sparks.rules : undefined}
            explain="Routed rules (priority 10+) held by the switches right now."
          />
        </div>
      </section>

      <section>
        <SectionTitle
          hint="Link colour reflects rules installed on the switches, not the agent's intent"
          right={
            <button
              onClick={() => onNavigate("topology")}
              className="t-small underline underline-offset-2"
              style={{ color: "var(--accent)" }}
            >
              Open full topology →
            </button>
          }
        >
          Network
        </SectionTitle>

        <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_340px] gap-4">
          <Card
            accent="net"
            className="min-h-[440px]"
            bodyClassName="p-0"
            raised
            title="Live topology"
            subtitle={
              active
                ? "4 hosts · 5 switches · two link-disjoint paths"
                : "Configured topology — no live state while nothing is running"
            }
            right={<SourceBadge source={active ? "live" : "config"} />}
          >
            <div style={{ height: 420 }}>
              <TopologyCanvas snapshot={runId ? null : snapshot} compact />
            </div>
          </Card>

          <div className="flex flex-col gap-4 min-h-0">
            <EventTimeline snapshot={snapshot} active={active} />
            <PathSummary snapshot={snapshot} />
          </div>
        </div>
      </section>

      <section>
        <SectionTitle
          right={
            <button
              onClick={() => onNavigate("history")}
              className="t-small underline underline-offset-2"
              style={{ color: "var(--accent)" }}
            >
              All experiments →
            </button>
          }
          hint="Most recent runs, newest first"
        >
          Recent experiments
        </SectionTitle>
        {recent.length ? (
          <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-3">
            {recent.map((run) => (
              <RunTile key={run.run_id} run={run} onOpen={() => onOpenRun(run.run_id)} />
            ))}
          </div>
        ) : (
          <Card>
            <Empty title="No runs recorded yet"
                   detail="Start one from the Experiments page." />
          </Card>
        )}
      </section>
    </div>
  );
}

/** The page's opening statement: what is running, and what can be done. */
function StatusBanner({
  live, active, runId, caps, onNavigate,
}: {
  live?: LiveSnapshot["live"];
  active: boolean;
  runId?: string;
  caps?: Capabilities | null;
  onNavigate: (p: PageId) => void;
}) {
  const progress =
    active && live?.elapsed_s != null && live?.duration_s
      ? Math.min(100, (live.elapsed_s / live.duration_s) * 100)
      : null;

  return (
    <div
      className="panel panel-raised relative overflow-hidden px-5 py-4"
      style={{
        backgroundImage:
          "linear-gradient(120deg, rgba(34,211,238,0.07), transparent 46%), " +
          "linear-gradient(300deg, rgba(139,92,246,0.06), transparent 44%)",
      }}
    >
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2 mb-1">
            <StatusDot tone={active ? "ok" : "idle"} pulse={active} />
            <span className="t-label" style={{ color: active ? "var(--ok)" : "var(--text-3)" }}>
              {active ? "Experiment running" : runId ? "Viewing recorded run" : "Idle"}
            </span>
          </div>
          <h1 className="t-display">
            {active ? (
              <>
                <span style={{ color: VARIANT_COLOR[live?.variant ?? "A"] }}>
                  Variant {live?.variant}
                </span>
                <span style={{ color: "var(--text-3)" }}> · </span>
                {live?.workload}
              </>
            ) : runId ? (
              "Recorded run"
            ) : (
              "SDN Network Intelligence"
            )}
          </h1>
          <p className="t-body mt-0.5 text-balance" style={{ color: "var(--text-3)" }}>
            {active
              ? VARIANT_LABEL[live?.variant ?? "A"]
              : runId
                ? runId
                : "Reinforcement-learning traffic routing and flow-table optimisation."}
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2 shrink-0">
          {active && live?.seed != null && (
            <Pill title="Random seed for this run">seed {live.seed}</Pill>
          )}
          {active && live?.matrix_running && (
            <Pill color="var(--warn)" title="Part of a matrix sweep">matrix sweep</Pill>
          )}
          {!active && !runId && (
            <button
              onClick={() => onNavigate("experiments")}
              className="px-3 py-1.5 rounded-lg border t-small font-medium transition-colors"
              style={{
                background: caps?.can_start_experiments
                  ? "var(--accent-dim)" : "var(--surface-2)",
                borderColor: caps?.can_start_experiments
                  ? "var(--accent-ring)" : "var(--border-2)",
                color: caps?.can_start_experiments
                  ? "var(--accent)" : "var(--text-2)",
              }}
            >
              {caps?.can_start_experiments ? "Start an experiment" : "Experiment controls"}
            </button>
          )}
        </div>
      </div>

      {active && live?.run_id && (
        <div className="mt-3.5">
          <div className="flex items-center justify-between t-small mb-1.5">
            <span className="mono truncate" style={{ color: "var(--text-4)" }}>
              {live.run_id}
            </span>
            {progress !== null && (
              <span className="num shrink-0" style={{ color: "var(--text-3)" }}>
                {fmtDuration(live.elapsed_s)} of {fmtDuration(live.duration_s)}
              </span>
            )}
          </div>
          {progress !== null && (
            <div className="h-1.5 rounded-full overflow-hidden"
                 style={{ background: "var(--surface-3)" }}>
              <div
                className="h-full rounded-full transition-all duration-700"
                style={{
                  width: `${progress}%`,
                  background: "linear-gradient(90deg, var(--accent-3), var(--accent))",
                }}
              />
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function EventTimeline({
  snapshot, active,
}: {
  snapshot: LiveSnapshot | null;
  active: boolean;
}) {
  const events = [...(snapshot?.events ?? [])].reverse();

  const toneFor = (kind: string): "ok" | "warn" | "bad" | "idle" => {
    if (kind.includes("down") || kind.includes("disconnect")) return "bad";
    if (kind.includes("up") || kind.includes("connected")) return "ok";
    if (kind.includes("flush") || kind.includes("removed")) return "warn";
    return "idle";
  };

  return (
    <Card
      title="Event timeline"
      subtitle="Controller, routing and link events as they occur"
      right={<SourceBadge source={active ? "live" : "unavailable"} />}
      bodyClassName="p-0"
      className="flex-1 min-h-0"
    >
      {events.length ? (
        <ol className="relative px-4 py-3 overflow-auto" style={{ maxHeight: 260 }}>
          <span
            className="absolute left-[21px] top-4 bottom-4 w-px"
            style={{ background: "var(--border-1)" }}
            aria-hidden="true"
          />
          {events.map((e, i) => (
            <li key={i} className="relative pl-6 pb-3 last:pb-0">
              <span
                className="absolute left-0 top-[5px] w-[9px] h-[9px] rounded-full border-2"
                style={{
                  background: "var(--surface-1)",
                  borderColor: `var(--${toneFor(e.kind)})`,
                }}
                aria-hidden="true"
              />
              <div className="flex items-baseline justify-between gap-2">
                <span className="t-small font-medium truncate">{e.kind}</span>
                <span className="t-micro num shrink-0" style={{ color: "var(--text-4)" }}>
                  {fmtTime(e.t)}
                </span>
              </div>
              {e.detail && (
                <p className="t-micro truncate" style={{ color: "var(--text-3) " }}
                   title={e.detail}>
                  {e.detail}
                </p>
              )}
            </li>
          ))}
        </ol>
      ) : (
        <div className="p-4">
          <Empty
            title="No events yet"
            detail={active
              ? "Switch connects, link changes and rule expiries will appear here."
              : "Events are recorded while an experiment runs."}
          />
        </div>
      )}
    </Card>
  );
}

function PathSummary({ snapshot }: { snapshot: LiveSnapshot | null }) {
  const paths = snapshot?.topology?.active_paths;
  const counts = (paths?.rule_counts ?? {}) as Record<string, number>;
  const total = Object.values(counts).reduce((a, b) => a + b, 0);

  return (
    <Card
      title="Confirmed forwarding"
      subtitle="Rules actually installed at the edge switches"
      right={<SourceBadge source={paths?.available ? "live" : "unavailable"} />}
    >
      {paths?.available ? (
        <div className="space-y-3">
          {(["A", "B"] as const).map((p) => {
            const n = counts[p] ?? 0;
            const share = total ? n / total : 0;
            const carrying = n > 0;
            return (
              <div key={p}>
                <div className="flex items-center justify-between mb-1">
                  <span className="flex items-center gap-2 t-small">
                    <StatusDot tone={carrying ? "ok" : "idle"} />
                    <span className="font-semibold">Path {p}</span>
                    <span style={{ color: "var(--text-4)" }}>
                      {p === "A" ? "2 hops · 5 Mbit" : "3 hops · 10 Mbit"}
                    </span>
                  </span>
                  <span className="num t-small" style={{ color: "var(--text-2)" }}>
                    {n} rules
                  </span>
                </div>
                <div className="h-1.5 rounded-full overflow-hidden"
                     style={{ background: "var(--surface-3)" }}>
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{
                      width: `${Math.round(share * 100)}%`,
                      background: carrying
                        ? "linear-gradient(90deg, var(--accent-3), var(--accent))"
                        : "var(--idle)",
                    }}
                  />
                </div>
              </div>
            );
          })}
          <p className="t-micro leading-relaxed pt-0.5" style={{ color: "var(--text-4)" }}>
            A path is shown as carrying traffic only when rules for it exist on
            the switches — never because the agent selected it.
          </p>
        </div>
      ) : (
        <Empty title="No forwarding data"
               detail={paths?.reason ?? "Appears once a flow-table dump is available."} />
      )}
    </Card>
  );
}

function RunTile({ run, onOpen }: { run: RunSummary; onOpen: () => void }) {
  const tone =
    run.status === "running" ? "ok"
      : run.status === "complete" ? "ok"
        : run.status === "interrupted" ? "bad" : "warn";
  return (
    <button
      onClick={onOpen}
      className="panel panel-hover text-left px-4 py-3 flex flex-col gap-2 w-full"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 min-w-0">
          <span
            className="w-2 h-2 rounded-sm shrink-0"
            style={{ background: VARIANT_COLOR[run.variant] }}
            aria-hidden="true"
          />
          <span className="t-h2">Variant {run.variant}</span>
          <span className="t-small truncate" style={{ color: "var(--text-3)" }}>
            {run.workload}
          </span>
        </span>
        <span className="flex items-center gap-1.5 shrink-0">
          <StatusDot tone={tone} pulse={run.status === "running"} />
          <span className="t-micro" style={{ color: "var(--text-3)" }}>
            {run.status}
          </span>
        </span>
      </div>
      <div className="flex items-center justify-between t-micro"
           style={{ color: "var(--text-4)" }}>
        <span className="num">seed {run.seed} · {fmtDuration(run.duration_s)}</span>
        <span className="num">{run.flow_dump_samples ?? 0} dumps</span>
      </div>
      <span className="t-micro mono truncate" style={{ color: "var(--text-4)" }}>
        {run.run_id}
      </span>
    </button>
  );
}
