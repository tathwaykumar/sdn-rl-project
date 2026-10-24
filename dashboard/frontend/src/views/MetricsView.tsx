/** Live metrics.
 *
 * Every card states its unit, its measurement interval and how old the reading
 * is. Instantaneous values (flow-table occupancy) and interval aggregates
 * (throughput, rates) are labelled differently because they are not the same
 * kind of number.
 *
 * Latency comes from the host's own ping stream, which the agent never sees.
 * Throughput is shown from the OpenFlow counters; iperf3's own figure is a
 * post-run measurement and is reported in the comparison views instead of
 * being presented as live.
 */
import {
  CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../api/client";
import { useAsync } from "../hooks/useLive";
import type { LatencyPayload, LiveMetrics, LiveSnapshot, MetricSeries } from "../api/types";
import {
  Card, ChartLegend, Empty, ErrorBox, MetricCard, Skeleton, SourceBadge,
  tooltipLabelStyle, tooltipStyle,
  fmtNumber, fmtPercent,
} from "../components/ui";

export function MetricsView({
  runId,
  snapshot,
}: {
  runId?: string;
  snapshot: LiveSnapshot | null;
}) {
  const series = useAsync<MetricSeries>(
    () => api.metricsSeries(runId, 180),
    [runId],
    runId ? undefined : 3000,
  );
  const latencyQuery = useAsync<LatencyPayload>(
    () => api.latency(runId, 30),
    [runId],
    runId ? undefined : 3000,
  );

  const live: LiveMetrics | undefined =
    (!runId ? snapshot?.metrics : undefined) ??
    (series.data?.points.length
      ? {
          available: true,
          source: series.data.source,
          t: series.data.points[series.data.points.length - 1].t,
          interval_s: series.data.interval_s ?? null,
          age_s: series.data.age_s ?? null,
          values: series.data.points[series.data.points.length - 1] as Record<string, number | null>,
          metric_meta: series.data.metric_meta,
        }
      : undefined);

  const latency = (!runId ? snapshot?.latency : undefined) ?? latencyQuery.data ?? undefined;

  if (series.loading && !series.data) {
    return <Card title="Live metrics"><Skeleton rows={6} /></Card>;
  }
  if (series.error && !series.data) {
    return <Card title="Live metrics"><ErrorBox message={series.error} onRetry={series.refresh} /></Card>;
  }

  const values = live?.values ?? {};
  const points = series.data?.points ?? [];
  const meta = live?.metric_meta ?? series.data?.metric_meta ?? {};
  const interval = live?.interval_s ?? series.data?.interval_s ?? null;

  if (!live?.available && points.length === 0) {
    return (
      <Card title="Live metrics" right={<SourceBadge source="unavailable" />}>
        <Empty
          title="No metrics available"
          detail={
            live?.reason ??
            series.data?.reason ??
            "Metrics come from the controller's telemetry. Start an experiment, or open a completed run."
          }
        />
      </Card>
    );
  }

  const chartData = points
    .filter((p) => p.valid)
    .map((p) => ({
      t: new Date(p.t * 1000).toLocaleTimeString(),
      throughput: ((p.delivered_bps as number) ?? 0) / 1e6,
      utilA: ((p.util_A as number) ?? 0) * 100,
      utilB: ((p.util_B as number) ?? 0) * 100,
      rules: (p.total_rules as number) ?? 0,
      packetIn: (p.packet_in_rate as number) ?? 0,
      flowMod: (p.flow_mod_rate as number) ?? 0,
      misses: (p.table_misses as number) ?? 0,
      loss: ((p.loss_fraction as number) ?? 0) * 100,
    }));

  const src = live?.source ?? series.data?.source ?? "unavailable";

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <p className="text-[11px]" style={{ color: "var(--text-3)" }}>
          {interval
            ? `Interval aggregates over ${fmtNumber(interval, 2)} s polling windows.`
            : "Measurement interval unknown."}
        </p>
        <SourceBadge source={src} age={live?.age_s ?? null} />
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <MetricCard
          label="Throughput (delivered)"
          value={fmtNumber(((values.delivered_bps as number) ?? 0) / 1e6, 2)}
          unit="Mbit/s"
          sub="host-facing egress counters"
          kind={meta.delivered_bps?.kind}
          source={src}
        />
        <MetricCard
          label="Latency (median RTT)"
          value={latency?.available ? fmtNumber(latency.median_ms, 1) : "—"}
          unit="ms"
          sub={
            latency?.available
              ? `p95 ${fmtNumber(latency.p95_ms, 1)} ms · ${latency.samples} probes / ${fmtNumber(latency.window_s, 0)}s`
              : (latency?.reason ?? "not measured")
          }
          kind="interval_rate"
          source={latency?.available ? (latency.source ?? src) : "unavailable"}
        />
        <MetricCard
          label="Packet loss (end-to-end)"
          value={
            latency?.available && latency.loss_fraction !== null &&
            latency.loss_fraction !== undefined
              ? fmtNumber(latency.loss_fraction * 100, 2)
              : "N/A"
          }
          unit="%"
          sub={
            latency?.available
              ? `${latency.lost ?? 0} lost of ${latency.probes_sent ?? 0} probes`
              : "no probe data"
          }
          tone={(latency?.loss_fraction ?? 0) > 0.05 ? "bad"
            : (latency?.loss_fraction ?? 0) > 0.01 ? "warn" : undefined}
          source={latency?.available ? (latency.source ?? src) : "unavailable"}
          direction="lower_is_better"
          explain="Probes that never received a reply. Probes answered late count as delayed, not lost."
        />
        <MetricCard
          label="Probes delayed"
          value={
            latency?.available && latency.delayed_fraction !== null &&
            latency.delayed_fraction !== undefined
              ? fmtNumber(latency.delayed_fraction * 100, 1)
              : "N/A"
          }
          unit="%"
          sub={
            latency?.available
              ? `${latency.delayed ?? 0} answered late (>200 ms)`
              : "no probe data"
          }
          tone={(latency?.delayed_fraction ?? 0) > 0.5 ? "warn" : undefined}
          source={latency?.available ? (latency.source ?? src) : "unavailable"}
          direction="lower_is_better"
          explain="Delivered, but only after the next probe was due. A symptom of queueing, not loss."
        />
        <MetricCard
          label="Max link utilisation"
          value={fmtNumber(((values.max_link_util as number) ?? 0) * 100, 1)}
          unit="%"
          sub={`A ${fmtPercent(values.util_A as number, 0)} · B ${fmtPercent(values.util_B as number, 0)}`}
          tone={((values.max_link_util as number) ?? 0) > 0.9 ? "warn" : undefined}
          kind={meta.max_link_util?.kind}
          source={src}
        />
        <MetricCard
          label="Flow-table occupancy"
          value={fmtNumber(values.total_rules as number, 0)}
          unit="rules"
          sub="routed rules across all switches"
          kind={meta.total_rules?.kind}
          source={src}
        />
        <MetricCard
          label="Table misses"
          value={fmtNumber(values.table_misses as number, 0)}
          unit="packets"
          sub={`${fmtNumber(values.miss_rate_per_s as number, 1)} /s`}
          kind={meta.table_misses?.kind}
          source={src}
        />
        <MetricCard
          label="Controller PacketIn"
          value={fmtNumber(values.packet_in_rate as number, 1)}
          unit="msg/s"
          sub="reactive load"
          kind={meta.packet_in_rate?.kind}
          source={src}
        />
        <MetricCard
          label="Controller FlowMod"
          value={fmtNumber(values.flow_mod_rate as number, 1)}
          unit="msg/s"
          sub="rule installations"
          kind={meta.flow_mod_rate?.kind}
          source={src}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <Chart
          title="Throughput"
          subtitle="Delivered to hosts, from switch egress counters"
          data={chartData}
          lines={[{ key: "throughput", label: "Mbit/s", color: "var(--variant-a)" }]}
        />
        <Chart
          title="Path utilisation"
          subtitle="Per-path bottleneck utilisation"
          data={chartData}
          unit="%"
          lines={[
            { key: "utilA", label: "Path A", color: "var(--variant-a)" },
            { key: "utilB", label: "Path B", color: "var(--variant-c)" },
          ]}
        />
        <Chart
          title="Flow-table occupancy"
          subtitle="Routed rules installed across all switches"
          data={chartData}
          lines={[{ key: "rules", label: "rules", color: "var(--variant-b)" }]}
        />
        <Chart
          title="Controller load"
          subtitle="Reactive messages only; stats polling is excluded"
          data={chartData}
          unit="msg/s"
          lines={[
            { key: "packetIn", label: "PacketIn", color: "var(--variant-b)" },
            { key: "flowMod", label: "FlowMod", color: "var(--variant-c)" },
          ]}
        />
      </div>

      <LossPanel latency={latency} runId={runId} snapshot={snapshot} />

      <Card title="What is not shown live" bodyClassName="p-4">
        <ul className="text-[11px] space-y-1.5 leading-relaxed" style={{ color: "var(--text-3)" }}>
          <li>
            <strong style={{ color: "var(--text-2)" }}>iperf3 throughput</strong> — iperf3
            writes its JSON report only when a test finishes, so a per-flow
            figure does not exist until the run (or that segment) ends. The
            throughput above is the switch-counter measurement; the iperf3
            figure appears in the comparison views.
          </li>
          <li>
            <strong style={{ color: "var(--text-2)" }}>Recovery time</strong> — defined
            relative to a link-failure event, so it only exists for runs using
            the <span className="mono">failure</span> workload, after the failure
            has occurred.
          </li>
          <li>
            <strong style={{ color: "var(--text-2)" }}>Aggregates across seeds</strong> —
            medians and confidence intervals need completed runs; see A/B/C
            Comparison.
          </li>
        </ul>
      </Card>
    </div>
  );
}

function Chart({
  title, subtitle, data, lines, unit,
}: {
  title: string;
  subtitle?: string;
  data: Record<string, string | number>[];
  lines: { key: string; label: string; color: string }[];
  unit?: string;
}) {
  if (data.length < 2) {
    return (
      <Card title={title} subtitle={subtitle}>
        <Empty title="Not enough samples yet" detail="Needs at least two valid intervals." />
      </Card>
    );
  }
  return (
    <Card title={title} subtitle={subtitle}>
      <div style={{ height: 190 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 4, right: 8, bottom: 0, left: -18 }}>
            <CartesianGrid stroke="var(--border-1)" strokeDasharray="2 4" vertical={false} />
            <XAxis
              dataKey="t" tick={{ fontSize: 9, fill: "var(--text-3)" }}
              axisLine={{ stroke: "var(--border-1)" }} tickLine={false} minTickGap={40}
            />
            <YAxis
              tick={{ fontSize: 9, fill: "var(--text-3)" }}
              axisLine={false} tickLine={false} width={48}
              label={
                unit
                  ? {
                      value: unit, angle: -90, position: "insideLeft",
                      offset: 22, fontSize: 9, fill: "var(--text-4)",
                    }
                  : undefined
              }
            />
            <Tooltip
              contentStyle={tooltipStyle}
              labelStyle={tooltipLabelStyle}
              cursor={{ stroke: "var(--border-3)", strokeDasharray: "3 3" }}
              formatter={(value: number, name: string) => [
                `${fmtNumber(value, 2)}${unit ? ` ${unit}` : ""}`, name,
              ]}
            />
            {lines.map((l) => (
              <Line
                key={l.key} type="monotone" dataKey={l.key} name={l.label}
                stroke={l.color} strokeWidth={2} dot={false} isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
      {/* Two or more series always get a key; one series is named by the
          title, so a legend there would only repeat it. */}
      {lines.length > 1 && (
        <ChartLegend
          items={lines.map((l) => ({ label: l.label, color: l.color }))}
          note={unit ? `values in ${unit}` : undefined}
        />
      )}
    </Card>
  );
}

/** Packet-loss detail: counts, context, and an honest threshold.
 *
 * Loss is shown with the packet counts behind it, separated from delay, and
 * tied to the run it came from. The warning deliberately has no universal
 * threshold: the heavy workload offers more traffic than the bottleneck path
 * can carry, so loss there is the experiment working as designed, while the
 * same figure on the light workload would indicate a fault.
 */
function LossPanel({
  latency,
  runId,
  snapshot,
}: {
  latency?: LatencyPayload;
  runId?: string;
  snapshot: LiveSnapshot | null;
}) {
  const workload = runId
    ? undefined
    : snapshot?.live?.workload ?? undefined;
  const sourceRun = runId ?? snapshot?.live?.run_id ?? undefined;

  if (!latency?.available) {
    return (
      <Card title="Packet loss detail" right={<SourceBadge source="unavailable" />}>
        <Empty
          title="No probe data"
          detail={
            latency?.reason ??
            "Loss is measured by a ping stream that runs during an experiment. Start a run, or open a completed one."
          }
        />
      </Card>
    );
  }

  const loss = latency.loss_fraction ?? 0;
  const delayed = latency.delayed_fraction ?? 0;
  const sent = latency.probes_sent ?? 0;
  const received = latency.replies_received ?? 0;
  const lost = latency.lost ?? 0;

  // Congestion is expected on heavy; it is not on light.
  const congestionExpected = workload === "heavy" || workload === "failure";
  const high = loss > (congestionExpected ? 0.05 : 0.01);

  return (
    <Card
      title="Packet loss detail"
      subtitle={
        workload
          ? `Workload: ${workload} · measurement window ${fmtNumber(latency.window_s, 0)} s`
          : `Measurement window ${fmtNumber(latency.window_s, 0)} s`
      }
      right={<SourceBadge source={latency.source} age={latency.age_s} />}
    >
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 mb-3">
        <Counter label="Probes sent" value={sent} />
        <Counter label="Replies received" value={received} tone="ok" />
        <Counter label="Never answered" value={lost} tone={lost > 0 ? "bad" : undefined} />
        <Counter label="Answered late" value={latency.delayed ?? 0} tone="warn" />
      </div>

      <div className="space-y-2">
        <Bar label="Lost" fraction={loss} color="var(--bad)" />
        <Bar label="Delayed but delivered" fraction={delayed} color="var(--warn)" />
      </div>

      {high && (
        <div
          className="mt-3 rounded-lg border px-3 py-2 text-[11.5px] leading-relaxed"
          style={{
            borderColor: "rgba(248,113,113,0.35)",
            background: "var(--bad-dim)",
            color: "#fca5a5",
          }}
        >
          <strong>Loss is high for this workload.</strong>{" "}
          {congestionExpected
            ? "The heavy and failure workloads deliberately offer more traffic than the bottleneck path can carry, so some loss is expected — but this is above what congestion alone usually produces here."
            : "This workload offers well under the path capacity, so loss indicates a fault rather than congestion."}
        </div>
      )}

      <p className="text-[11px] mt-3 leading-relaxed" style={{ color: "var(--text-3)" }}>
        Lower loss is generally better, but there is no single threshold that
        applies to every workload: <span className="mono">heavy</span> is
        designed to saturate the 5 Mbit path, so a small amount of loss there
        is the experiment working. The same figure on{" "}
        <span className="mono">light</span> would mean something is wrong.
      </p>
      <p className="text-[11px] mt-1.5 leading-relaxed" style={{ color: "var(--text-4)" }}>
        {latency.source_note}
        {sourceRun && (
          <>
            {" "}Source run: <span className="mono">{sourceRun}</span>
          </>
        )}
      </p>
    </Card>
  );
}

function Counter({
  label,
  value,
  tone,
}: {
  label: string;
  value: number;
  tone?: "ok" | "warn" | "bad";
}) {
  return (
    <div className="rounded-lg px-2.5 py-1.5" style={{ background: "var(--surface-2)" }}>
      <p className="text-[10px]" style={{ color: "var(--text-4)" }}>{label}</p>
      <p className="text-[16px] font-semibold num"
         style={{ color: tone ? `var(--${tone})` : "var(--text-1)" }}>
        {value.toLocaleString()}
      </p>
    </div>
  );
}

function Bar({
  label,
  fraction,
  color,
}: {
  label: string;
  fraction: number;
  color: string;
}) {
  const pct = Math.min(100, Math.max(0, fraction * 100));
  return (
    <div>
      <div className="flex justify-between text-[11px] mb-0.5">
        <span style={{ color: "var(--text-2)" }}>{label}</span>
        <span className="num" style={{ color: "var(--text-2)" }}>
          {fmtNumber(pct, 2)}%
        </span>
      </div>
      <div className="h-1.5 rounded-full overflow-hidden" style={{ background: "var(--surface-3)" }}>
        <div className="h-full rounded-full transition-all"
             style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  );
}
