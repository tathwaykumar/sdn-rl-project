/** Overview — the product's front page.
 *
 * An ordinary dashboard layout: a status banner, a row of KPI cards, a boxed
 * topology panel beside a forwarding-evidence panel, then run history, the
 * learning snapshot and the event feed. Every panel is a plain card with its
 * own border and header — nothing bleeds to the window edge and nothing
 * floats over the canvas, so the page reads the same way any other page in
 * the console does.
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
import type {
  Capabilities, DataSource, Decision, LiveSnapshot, RunSummary, TopologyPayload,
} from "../api/types";
import type { PageId } from "../components/Shell";
import {
  AboutPanel, Card, Empty, Pill, SectionTitle, SourceBadge, StatusDot,
  VARIANT_COLOR, VARIANT_LABEL, cx, fmtDuration, fmtNumber, fmtTime,
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

  // The live websocket feed only ever carries the network's current state,
  // so it is forced to null for a selected run (see App.tsx) -- a recorded
  // run's vitals come from the same per-run endpoints every other page uses
  // instead, not from the live feed.
  const historicalMetrics = useAsync(() => api.metricsLive(runId), [runId]);
  const historicalLatency = useAsync(() => api.latency(runId), [runId]);
  const historicalEvents = useAsync(() => api.events(runId), [runId]);
  const metrics = runId ? historicalMetrics.data : snapshot?.metrics;
  const latency = runId ? historicalLatency.data : snapshot?.latency;
  const events = runId ? historicalEvents.data?.events ?? [] : snapshot?.events ?? [];
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
  const vitalSource = active ? "live" : runId ? "historical" : "unavailable";

  const vitals = [
    {
      label: "Throughput",
      value: metrics?.available
        ? fmtNumber(((values.delivered_bps as number) ?? 0) / 1e6, 2) : "N/A",
      unit: "Mbit/s",
      spark: active ? sparks.throughput : undefined,
      direction: "higher_is_better",
      explain: "Bits per second reaching the destination hosts, from edge-switch egress counters.",
    },
    {
      label: "Latency",
      value: latency?.available ? fmtNumber(latency.median_ms, 1) : "N/A",
      unit: "ms median",
      direction: "lower_is_better",
      explain: "Round-trip time of the host's own ICMP probe, h1 to h3.",
    },
    {
      label: "Packet loss",
      value: latency?.available && latency.loss_fraction != null
        ? fmtNumber(latency.loss_fraction * 100, 2) : "N/A",
      unit: "%",
      tone: (latency?.loss_fraction ?? 0) > 0.05
        ? ("bad" as const)
        : (latency?.loss_fraction ?? 0) > 0.01 ? ("warn" as const) : undefined,
      direction: "lower_is_better",
      explain: "Probes that never received a reply. Late replies count as delayed, not lost.",
    },
    {
      label: "Flow rules",
      value: metrics?.available ? fmtNumber(values.total_rules as number, 0) : "N/A",
      unit: "installed",
      spark: active ? sparks.rules : undefined,
      direction: "context",
      explain: "Routed rules (priority 10+) held by the switches right now.",
    },
  ];

  return (
    <div className="space-y-8">
      <AboutPanel
        summary={
          <>
            The front page of the console: what the network and the learning
            agent are doing right now, or what a recorded run did. Everything
            below drills into one part of this summary — confirmed forwarding
            on Live Topology, measured traffic on Metrics &amp; Analytics, the
            agent's scoring and decisions under Reinforcement Learning, and
            past runs under Runs.
          </>
        }
        terms={[
          { term: "Live", def: "Read from an experiment running right now." },
          { term: "Recorded", def: "Read from a completed run's stored files, not a live measurement." },
          { term: "N/A", def: "Not measurable in this state — never shown as zero." },
        ]}
      />

      <section>
        <WorkflowStrip onNavigate={onNavigate} />
      </section>

      {/* Status banner: a plain header panel naming what is running (or
          recorded), not fused with the readings below it. */}
      <section>
        <StatusBanner
          live={live} active={active} runId={runId} caps={caps} onNavigate={onNavigate}
        />
      </section>

      {/* Vital strip: one continuous plate, numbers ruled apart rather than
          four identical boxes — the same reading a row of cards would give,
          without the card-grid furniture around every figure. */}
      <section className="stagger">
        <VitalStrip vitals={vitals} source={vitalSource} />
      </section>

      {/* Topology and forwarding evidence as two boxed cards side by side,
          not a full-bleed canvas with panels floating on top of it. */}
      <section className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.9fr)_minmax(0,1fr)] gap-6">
        <Card
          icon={<StatusDot tone={active || runId ? "ok" : "idle"} pulse={active} />}
          title="Live topology"
          subtitle={active
            ? "4 hosts · 5 switches · two link-disjoint paths"
            : runId
              ? "Forwarding state recorded for this run"
              : "Configured topology — nothing running"}
          right={
            <div className="flex items-center gap-2.5">
              <SourceBadge source={active ? "live" : runId ? "historical" : "config"} />
              <button
                onClick={() => onNavigate("topology")}
                className="t-small font-semibold underline underline-offset-2"
                style={{ color: "var(--accent)" }}
              >
                Inspect →
              </button>
            </div>
          }
          bodyClassName="p-0"
        >
          <div className="h-[clamp(380px,48vh,520px)]">
            <TopologyCanvas snapshot={runId ? null : snapshot} runId={runId} compact />
          </div>
        </Card>

        <PathSummary snapshot={snapshot} runId={runId} />
      </section>

      {/* Three columns, widest to narrowest: the run log is a list people
          scan, the learning snapshot and the event feed are each a glance at
          one other group — put here so Overview is a hub for all three
          sections of the rail, not only Operations. */}
      <section className="grid grid-cols-1 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)_minmax(0,1fr)] gap-6">
        <div>
          <SectionTitle
            right={
              <button
                onClick={() => onNavigate("history")}
                className="t-small underline underline-offset-2"
                style={{ color: "var(--accent)" }}
              >
                All {runs.data?.runs?.length ?? ""} experiments →
              </button>
            }
          >
            Recent experiments
          </SectionTitle>
          {recent.length ? (
            <div className="panel overflow-hidden">
              {recent.map((run, i) => (
                <RunRow
                  key={run.run_id}
                  run={run}
                  first={i === 0}
                  onOpen={() => onOpenRun(run.run_id)}
                />
              ))}
            </div>
          ) : (
            <Card>
              <Empty title="No runs recorded yet"
                     detail="Start one from the Experiments page." />
            </Card>
          )}
        </div>

        <div>
          <SectionTitle hint="The agent's most recent scored decisions">
            Learning snapshot
          </SectionTitle>
          <RlSnapshot
            snapshot={snapshot} active={active} runId={runId} onNavigate={onNavigate}
          />
        </div>

        <div>
          <SectionTitle hint="Controller, routing and link events as they occur">
            Event feed
          </SectionTitle>
          <EventTimeline events={events} active={active} runId={runId} />
        </div>
      </section>
    </div>
  );
}

interface VitalSpec {
  label: string;
  value: string;
  unit: string;
  tone?: "warn" | "bad";
  spark?: number[];
  direction: string;
  explain: string;
}

/** The four live readings as one continuous plate: big mono numbers ruled
    apart by hairlines, not four identical bordered boxes. The number carries
    the weight; the label beneath is furniture. */
function VitalStrip({
  vitals, source,
}: {
  vitals: VitalSpec[];
  source: DataSource;
}) {
  return (
    <div className="glow-emerald panel overflow-hidden">
      <div className="flex flex-wrap divide-x" style={{ borderColor: "var(--hairline)" }}>
        {vitals.map((v) => (
          <VitalFigure key={v.label} {...v} source={source} />
        ))}
      </div>
    </div>
  );
}

function VitalFigure({
  label, value, unit, tone, spark, direction, explain, source,
}: VitalSpec & { source: DataSource }) {
  const unavailable = value === "N/A" || value === "—";
  const color = unavailable
    ? "var(--text-4)"
    : tone === "bad"
      ? "var(--bad)"
      : tone === "warn"
        ? "var(--warn)"
        : "var(--accent-strong)";
  return (
    <div
      className="flex-1 min-w-[180px] px-6 py-6 sm:px-7 sm:py-7 first:pl-7 last:pr-7"
      title={explain}
    >
      <div className="flex items-center gap-2 mb-3">
        <span className="t-label truncate" style={{ color: "var(--text-3)" }}>
          {label}
        </span>
        <SourceBadge source={source} />
      </div>
      <div className="flex items-baseline gap-2 flex-wrap">
        <span
          className="readout leading-none"
          style={{ color, fontSize: "clamp(32px, 2.6vw, 46px)", fontWeight: 600 }}
        >
          {value}
        </span>
        {unit && !unavailable && (
          <span className="t-small" style={{ color: "var(--text-3)" }}>
            {unit}
          </span>
        )}
      </div>
      <div className="flex items-center justify-between gap-2 mt-3">
        <span className="t-micro" style={{ color: "var(--text-4)" }}>
          {unavailable ? "not measurable" : direction === "context" ? "context" : null}
        </span>
        {!unavailable && direction !== "context" && (
          <span className="t-micro" style={{ color: "var(--text-4)" }}>
            {direction === "higher_is_better" ? "↑ better" : "↓ better"}
          </span>
        )}
      </div>
      {spark && spark.length > 1 && !unavailable && (
        <div className="mt-2.5">
          <StripMeter values={spark} tone={tone} />
        </div>
      )}
    </div>
  );
}

/** Recent samples as a compact bar strip, from the same telemetry the number
    above came from. */
function StripMeter({ values, tone }: { values: number[]; tone?: "warn" | "bad" }) {
  const window = values.slice(-24);
  const max = Math.max(...window, 0.0001);
  const color = tone === "bad" ? "var(--bad)" : tone === "warn" ? "var(--warn)" : "var(--accent)";
  return (
    <div className="flex items-end gap-[2px] h-6" aria-hidden="true">
      {window.map((v, i) => (
        <span
          key={i}
          className="flex-1 rounded-t-[2px]"
          style={{
            height: `${Math.max(6, (v / max) * 100)}%`,
            background: color,
            opacity: i >= window.length - 3 ? 0.95 : 0.2 + (i / window.length) * 0.35,
          }}
        />
      ))}
    </div>
  );
}

const WORKFLOW_STEPS: { label: string; detail: string }[] = [
  { label: "Observe", detail: "Network state, as 4 coarse bins" },
  { label: "Decide", detail: "Greedy best action, or explore" },
  { label: "Route", detail: "Controller applies it to switches" },
  { label: "Measure", detail: "Switches report what happened" },
  { label: "Reward", detail: "Outcome scored by the formula" },
  { label: "Learn", detail: "Q-value updates for next time" },
];

/** A compact map of the agent's control loop, so a first-time reader can
    place everything else on this page — and the rest of the console — on one
    cycle before diving into any single part of it. Every step links to the
    page that shows it happening for one real, recorded decision. */
function WorkflowStrip({ onNavigate }: { onNavigate: (p: PageId) => void }) {
  return (
    <div className="panel px-4 py-3.5">
      <p className="t-label mb-2.5" style={{ color: "var(--text-4)" }}>
        How the agent learns, one decision at a time
      </p>
      {/* Wraps rather than scrolls off-screen: a horizontally-scrolling strip
          with no visible scrollbar hid steps 4-6 on a phone with nothing to
          suggest they existed. */}
      <div className="flex flex-wrap items-start gap-y-3 gap-x-0.5">
        {WORKFLOW_STEPS.map((s, i) => (
          <div key={s.label} className="flex items-start shrink-0">
            <div className="flex flex-col items-center text-center w-[104px] px-1">
              <span
                className="w-7 h-7 rounded-full grid place-items-center t-small font-bold mb-1.5 shrink-0"
                style={{ background: "var(--rl-dim)", color: "var(--rl)" }}
                aria-hidden="true"
              >
                {i + 1}
              </span>
              <p className="t-small font-semibold">{s.label}</p>
              <p className="t-micro leading-snug" style={{ color: "var(--text-4)" }}>
                {s.detail}
              </p>
            </div>
            {i < WORKFLOW_STEPS.length - 1 && (
              <span
                className="t-small mt-[5px] shrink-0"
                style={{ color: "var(--text-4)" }}
                aria-hidden="true"
              >
                →
              </span>
            )}
          </div>
        ))}
      </div>
      <button
        onClick={() => onNavigate("decisions")}
        className="t-small underline underline-offset-2 mt-2.5"
        style={{ color: "var(--accent)" }}
      >
        See this cycle for one real, recorded decision →
      </button>
    </div>
  );
}

/** The page's opening statement: what is running, or what is being viewed.
 *
 * A plain header panel — identity only. The four live readings used to be
 * fused into this same object as a ruled row; they are now their own KPI
 * cards below, which is the ordinary dashboard shape: a status line, then the
 * numbers. */
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
    <div className={cx("glow-emerald panel px-6 py-7 sm:px-8 sm:py-8", active && "panel-live")}>
      <div className="flex flex-wrap items-start justify-between gap-6">
        <div className="min-w-0">
          <div className="flex items-center gap-2.5 mb-3">
            <StatusDot tone={active ? "ok" : "idle"} pulse={active} />
            <span
              className="t-label"
              style={{ color: active ? "var(--ok)" : "var(--text-3)" }}
            >
              {active ? "Experiment running" : runId ? "Viewing recorded run" : "Idle"}
            </span>
          </div>

          <h2 className="t-display">
            {active ? (
              <>
                <span style={{ color: VARIANT_COLOR[live?.variant ?? "A"] }}>
                  Variant {live?.variant}
                </span>
                <span style={{ color: "var(--text-4)" }}> · </span>
                {live?.workload}
              </>
            ) : runId ? (
              "Recorded run"
            ) : (
              "SDN Network Intelligence"
            )}
          </h2>

          <p className="t-body mt-1.5 text-balance" style={{ color: "var(--text-2)" }}>
            {active
              ? VARIANT_LABEL[live?.variant ?? "A"]
              : runId
                ? runId
                : "Reinforcement-learning traffic routing and flow-table optimisation."}
          </p>

          {active && live?.run_id && (
            <div className="mt-4 max-w-sm">
              <div className="flex items-center justify-between t-small mb-2 gap-2">
                <span
                  className="mono truncate flex items-center gap-2"
                  style={{ color: "var(--text-4)" }}
                >
                  {live.run_id}
                  {live.matrix_running && (
                    <Pill color="var(--warn)" title="Part of a matrix sweep">
                      matrix
                    </Pill>
                  )}
                </span>
                {progress !== null && (
                  <span className="readout shrink-0" style={{ color: "var(--text-3)" }}>
                    {fmtDuration(live.elapsed_s)} / {fmtDuration(live.duration_s)}
                  </span>
                )}
              </div>
              {progress !== null && (
                <div
                  className="h-1.5 rounded-full overflow-hidden"
                  style={{ background: "var(--surface-4)" }}
                >
                  <div
                    className="h-full rounded-full transition-all duration-700"
                    style={{ width: `${progress}%`, background: "var(--accent)" }}
                  />
                </div>
              )}
            </div>
          )}
        </div>

        {!active && !runId && (
          <button
            onClick={() => onNavigate("experiments")}
            className="shrink-0 px-5 py-3 rounded-[8px] border t-small font-semibold
                       transition-all hover:brightness-110 hover:-translate-y-px"
            style={{
              background: caps?.can_start_experiments
                ? "var(--accent)" : "var(--surface-3)",
              borderColor: caps?.can_start_experiments
                ? "var(--accent)" : "var(--border-2)",
              color: caps?.can_start_experiments ? "var(--on-accent)" : "var(--text-2)",
              boxShadow: caps?.can_start_experiments
                ? "0 8px 24px -8px var(--accent-ring)" : "none",
            }}
          >
            {caps?.can_start_experiments ? "Start an experiment →" : "Experiment controls →"}
          </button>
        )}
      </div>
    </div>
  );
}

function EventTimeline({
  events: rawEvents, active, runId,
}: {
  events: { t: number | null; kind: string; detail: string }[];
  active: boolean;
  runId?: string;
}) {
  const events = [...rawEvents].reverse();

  const toneFor = (kind: string): "ok" | "warn" | "bad" | "idle" => {
    if (kind.includes("down") || kind.includes("disconnect")) return "bad";
    if (kind.includes("up") || kind.includes("connected") || kind.includes("installed")) return "ok";
    if (kind.includes("flush") || kind.includes("removed")) return "warn";
    return "idle";
  };

  const source = runId ? "historical" : active ? "live" : "unavailable";

  return (
    // No title here: the section heading above already names it, and two
    // headings one above the other read as two panels.
    <Card
      right={<SourceBadge source={source} />}
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
            detail={runId
              ? "No controller events were recorded for this run."
              : active
                ? "Switch connects, link changes, new flow installations (table-miss responses) and rule expiries will appear here."
                : "Events are recorded while an experiment runs."}
          />
        </div>
      )}
    </Card>
  );
}

/** A glance at the agent's reinforcement-learning state: the Overview's only
    link to the Reward Explorer and Decision Inspector, so those two pages are
    discoverable from the hub rather than only from the rail. Reads the same
    decision log the Shell's top-strip reward readout uses — nothing new is
    fetched for this card. */
function RlSnapshot({
  snapshot, active, runId, onNavigate,
}: {
  snapshot: LiveSnapshot | null;
  active: boolean;
  runId?: string;
  onNavigate: (p: PageId) => void;
}) {
  // Same fix as the metrics/latency/events cards above: the live websocket
  // snapshot is forced to null for a selected run, so a recorded run needs
  // its own fetch or this card always reads "no decisions" even when the
  // run's agent made hundreds of them.
  const historicalDecisions = useAsync(() => api.decisions(runId, 60), [runId]);
  const decisions = runId
    ? historicalDecisions.data?.decisions ?? []
    : snapshot?.decisions ?? [];
  const last: Decision | undefined = decisions[decisions.length - 1];
  const trend = decisions.slice(-20).map((d) => d.reward ?? 0);
  const source = runId ? "historical" : active ? "live" : "unavailable";

  return (
    <Card
      right={<SourceBadge source={source} />}
      bodyClassName="p-0"
      className="flex-1 min-h-0"
    >
      {last ? (
        <div className="px-4 py-3.5 space-y-3.5">
          <div>
            <div className="flex items-baseline justify-between gap-2">
              <span className="t-label" style={{ color: "var(--text-3)" }}>
                Last reward
              </span>
              <span className="t-micro" style={{ color: "var(--text-4)" }}>
                step {last.step}
              </span>
            </div>
            <div className="flex items-baseline gap-2 mt-1">
              <span
                className="readout text-[26px] leading-none"
                style={{
                  color: last.reward == null
                    ? "var(--text-4)"
                    : last.reward >= 0 ? "var(--ok)" : "var(--bad)",
                }}
              >
                {fmtNumber(last.reward, 3)}
              </span>
              <Pill color={last.exploratory ? "var(--rl)" : "var(--accent)"}>
                {last.exploratory ? "exploring" : "greedy"}
              </Pill>
            </div>
          </div>

          {trend.length > 1 && (
            <div className="flex items-end gap-[2px] h-6" aria-hidden="true">
              {trend.map((v, i) => {
                const max = Math.max(...trend.map(Math.abs), 0.0001);
                const h = Math.max(8, (Math.abs(v) / max) * 100);
                return (
                  <span
                    key={i}
                    className="flex-1 rounded-t-[2px]"
                    style={{
                      height: `${h}%`,
                      background: v >= 0 ? "var(--ok)" : "var(--bad)",
                      opacity: i >= trend.length - 3 ? 0.95 : 0.25 + (i / trend.length) * 0.35,
                    }}
                  />
                );
              })}
            </div>
          )}

          <div className="flex items-center justify-between t-small">
            <span style={{ color: "var(--text-3)" }}>
              Chose path <strong>{last.path}</strong>
            </span>
            <span className="t-micro" style={{ color: "var(--text-4)" }}>
              ε {fmtNumber(last.epsilon, 2)}
            </span>
          </div>

          <div className="flex items-center gap-4 pt-1 border-t" style={{ borderColor: "var(--hairline)" }}>
            <button
              onClick={() => onNavigate("reward")}
              className="t-small underline underline-offset-2 mt-2.5"
              style={{ color: "var(--accent)" }}
            >
              Reward Explorer →
            </button>
            <button
              onClick={() => onNavigate("decisions")}
              className="t-small underline underline-offset-2 mt-2.5"
              style={{ color: "var(--accent)" }}
            >
              Decision Inspector →
            </button>
          </div>
        </div>
      ) : (
        <div className="p-4">
          <Empty
            title="No decisions yet"
            detail={runId
              ? "No decisions were recorded for this run — variant A has no learning agent, so this is expected for it."
              : active
                ? "The agent records one decision per polling interval."
                : "Decisions are recorded while an experiment runs."}
          />
        </div>
      )}
    </Card>
  );
}

/** Forwarding evidence, in its own boxed card beside the topology rather than
    floated on top of it. */
function PathSummary({ snapshot, runId }: { snapshot: LiveSnapshot | null; runId?: string }) {
  // Same fix as everywhere else on this page: the live websocket snapshot
  // only ever carries the current experiment's topology, so a selected run
  // needs its own fetch or this card silently reads "no forwarding data"
  // for every recorded run, regardless of what it actually did.
  const topo = useAsync<TopologyPayload>(() => api.topologyState(runId), [runId]);
  const live = runId ? topo.data?.live : snapshot?.topology;
  const paths = live?.active_paths;
  const counts = (paths?.rule_counts ?? {}) as Record<string, number>;
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const source = runId ? "historical" : "live";

  if (!paths?.available) {
    return (
      <Card title="Confirmed forwarding" right={<SourceBadge source="unavailable" />}>
        <div className="flex items-center gap-2.5">
          <StatusDot tone="idle" />
          <span className="t-small" style={{ color: "var(--text-3)" }}>
            No forwarding data yet
          </span>
        </div>
      </Card>
    );
  }

  return (
    <Card title="Confirmed forwarding" right={<SourceBadge source={source} />}>
      <div className="space-y-3">
        {(["A", "B"] as const).map((p) => {
          const n = counts[p] ?? 0;
          const share = total ? n / total : 0;
          const carrying = n > 0;
          return (
            <div key={p}>
              <div className="flex items-center justify-between mb-1.5 gap-2">
                <span className="flex items-center gap-2 t-small min-w-0">
                  <StatusDot tone={carrying ? "ok" : "idle"} />
                  <span className="font-semibold">Path {p}</span>
                  <span className="truncate" style={{ color: "var(--text-4)" }}>
                    {p === "A" ? "2 hops" : "3 hops"}
                  </span>
                </span>
                <span className="readout text-[12px] shrink-0"
                      style={{ color: "var(--text-2)" }}>
                  {n}
                </span>
              </div>
              <div className="h-1.5 rounded-full overflow-hidden"
                   style={{ background: "var(--surface-4)" }}>
                <div
                  className="h-full rounded-full transition-all duration-500"
                  style={{
                    width: `${Math.round(share * 100)}%`,
                    background: carrying ? "var(--accent)" : "var(--idle)",
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
      <p className="t-micro leading-relaxed mt-3" style={{ color: "var(--text-4)" }}>
        A path counts as carrying traffic only when rules for it exist on the
        switches — never because the agent selected it.
      </p>
    </Card>
  );
}

/** One run as a ledger row.
 *
 * Six of these were six identical plates in a grid, which is the shape you use
 * for things that are chosen between, not for a reverse-chronological log.
 * Ruled rows in one plate let the eye run down the variant column. */
function RunRow({
  run, first, onOpen,
}: {
  run: RunSummary;
  first: boolean;
  onOpen: () => void;
}) {
  const tone =
    run.status === "running" ? "ok"
      : run.status === "complete" ? "ok"
        : run.status === "interrupted" ? "bad" : "warn";
  return (
    <button
      onClick={onOpen}
      className={cx(
        "w-full text-left px-5 py-3.5 grid items-center gap-x-5 transition-colors",
        "grid-cols-[auto_minmax(0,1fr)_auto] sm:grid-cols-[auto_minmax(0,1fr)_auto_auto]",
        "hover:bg-[var(--hover-wash)]",
        !first && "border-t",
      )}
      style={{ borderColor: "var(--hairline)" }}
    >
      <span
        className="w-[3px] h-9 rounded-full shrink-0"
        style={{ background: VARIANT_COLOR[run.variant] }}
        aria-hidden="true"
      />
      <span className="min-w-0">
        <span className="flex items-baseline gap-2 flex-wrap">
          <span className="text-[13px] font-semibold">Variant {run.variant}</span>
          <span className="t-small" style={{ color: "var(--text-3)" }}>
            {run.workload} · seed {run.seed}
          </span>
        </span>
        <span
          className="t-micro mono truncate block mt-0.5"
          style={{ color: "var(--text-4)" }}
        >
          {run.run_id}
        </span>
      </span>
      <span
        className="readout text-[12px] hidden sm:block text-right"
        style={{ color: "var(--text-3)" }}
      >
        {fmtDuration(run.duration_s)}
      </span>
      <span className="flex items-center gap-2 shrink-0 justify-self-end">
        <StatusDot tone={tone} pulse={run.status === "running"} />
        <span className="t-small" style={{ color: "var(--text-3)" }}>
          {run.status}
        </span>
      </span>
    </button>
  );
}
