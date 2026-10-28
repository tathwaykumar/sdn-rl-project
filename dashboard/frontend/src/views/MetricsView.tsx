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
  Area, AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip,
  XAxis, YAxis,
} from "recharts";
import { api } from "../api/client";
import { useAsync } from "../hooks/useLive";
import type { LatencyPayload, LiveMetrics, LiveSnapshot, MetricSeries } from "../api/types";
import {
  AboutPanel, Card, ChartLegend, Empty, ErrorBox, MetricCard, PATH_COLOR,
  Skeleton, SourceBadge, tooltipLabelStyle, tooltipStyle,
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

  // The *last row written* can be an invalid interval (a counter reset) or,
  // for a completed run, a quiet tail sample taken after traffic had already
  // stopped but before the controller tore down -- neither is what "current
  // reading" should mean. The last VALID row is the right fallback for the
  // supporting readings (occupancy, rates); the headline throughput figure
  // additionally uses the run's peak below, since a tail-of-run reading of
  // ~0 badly undersells a run that is otherwise shown, chart and all, right
  // next to it.
  const validPoints = (series.data?.points ?? []).filter((p) => p.valid);
  const lastValidPoint = validPoints.length ? validPoints[validPoints.length - 1] : undefined;

  const live: LiveMetrics | undefined =
    (!runId ? snapshot?.metrics : undefined) ??
    (lastValidPoint
      ? {
          available: true,
          source: series.data?.source ?? "unavailable",
          t: lastValidPoint.t as number,
          interval_s: series.data?.interval_s ?? null,
          age_s: series.data?.age_s ?? null,
          values: lastValidPoint as Record<string, number | null>,
          metric_meta: series.data?.metric_meta,
        }
      : undefined);

  const peakThroughputMbit = runId && validPoints.length
    ? Math.max(...validPoints.map((p) => ((p.delivered_bps as number) ?? 0))) / 1e6
    : null;

  // Same reasoning as the throughput headline above: a completed run's last
  // valid interval is reliably a quiet, post-traffic moment (the dataplane
  // idles or the controller flushes routed flows before the process exits),
  // so every supporting reading would otherwise read as "nothing happened"
  // regardless of what the run actually did. Each reading here is pinned to
  // the interval where *that* reading peaked, not to one shared moment --
  // "peak link utilisation" and "peak table occupancy" don't necessarily
  // land on the same second, and claiming they did would be inventing a
  // correlation the data doesn't show.
  const peakBy = (key: string): Record<string, number | null> | undefined => {
    if (!runId || !validPoints.length) return undefined;
    return validPoints.reduce((best, p) =>
      ((p[key] as number) ?? -Infinity) > ((best[key] as number) ?? -Infinity) ? p : best,
    ) as Record<string, number | null>;
  };
  const utilSrc = peakBy("max_link_util");
  const occupancySrc = peakBy("total_rules");
  const missSrc = peakBy("miss_rate_per_s");
  const packetInSrc = peakBy("packet_in_rate");
  const flowModSrc = peakBy("flow_mod_rate");
  const peakSuffix = runId ? " (peak)" : "";

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
    <div className="flex flex-col gap-7">
      <AboutPanel
        summary={
          <>
            Every measured figure for the current or selected run: switch
            egress counters for throughput and rules, the controller's own
            message rates, and the host's independent ping stream for latency
            and loss. Each card states its unit and whether higher or lower is
            desirable; nothing here is recomputed from the project's own
            analysis — it reads the same counters.
          </>
        }
        terms={[
          { term: "Interval", def: "Averaged over one polling window, shown above the charts." },
          { term: "Instant", def: "A single reading at one moment, not averaged." },
          { term: "↑ / ↓ better", def: "Which direction is desirable; ‘context’ means neither." },
        ]}
      />
      <div className="flex items-center justify-between">
        <p className="text-[11px]" style={{ color: "var(--text-3)" }}>
          {interval
            ? `Interval aggregates over ${fmtNumber(interval, 2)} s polling windows.`
            : "Measurement interval unknown."}
        </p>
        <SourceBadge source={src} age={live?.age_s ?? null} />
      </div>

      {/* Two headline figures and a spec sheet, not nine identical cards.
          A 3x3 grid of same-sized plates gives every reading the same weight,
          so nothing is findable: delivered throughput and end-to-end latency
          are what the project is judged on, and the other seven are the
          supporting readings you scan as a column. */}
      {/* The two headline figures sit directly above the trace they summarise,
          with the spec sheet running down the side. Both halves are then about
          the same height, so neither column ends in dead space. */}
      <div className="grid grid-cols-1 items-start
                      xl:grid-cols-[minmax(0,1.9fr)_minmax(0,1fr)] gap-5">
        <div className="space-y-5 min-w-0">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
            <MetricCard
              label={peakThroughputMbit !== null ? "Throughput (peak)" : "Throughput (delivered)"}
              value={fmtNumber(
                peakThroughputMbit !== null
                  ? peakThroughputMbit
                  : ((values.delivered_bps as number) ?? 0) / 1e6,
                2,
              )}
              unit="Mbit/s"
              sub={
                peakThroughputMbit !== null
                  ? "highest interval reached this run · host-facing egress counters"
                  : "host-facing egress counters"
              }
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
          </div>

          <Chart
            title="Throughput"
            subtitle="Delivered to hosts, from switch egress counters"
            data={chartData}
            tall
            lines={[{ key: "throughput", label: "Mbit/s", color: "var(--accent)" }]}
            caption="Higher is better. A rising line means more data is reaching the hosts each second; a flat line near the workload's capacity usually means the link carrying traffic is saturated, not that something is broken."
          />
        </div>

        <Card
          title="Supporting readings"
          subtitle={runId
            ? "Each reading below is this run's own peak interval for that reading, not one shared moment"
            : undefined}
          className="xl:row-span-1"
        >
          <div>
            <SpecRow
              label="Packet loss"
              value={
                latency?.available && latency.loss_fraction != null
                  ? fmtNumber(latency.loss_fraction * 100, 2) : "N/A"
              }
              unit="%"
              note={latency?.available
                ? `${latency.lost ?? 0} of ${latency.probes_sent ?? 0} probes`
                : "no probe data"}
              tone={(latency?.loss_fraction ?? 0) > 0.05 ? "bad"
                : (latency?.loss_fraction ?? 0) > 0.01 ? "warn" : undefined}
              explain="Probes that never received a reply. Probes answered late count as delayed, not lost."
            />
            <SpecRow
              label="Probes delayed"
              value={
                latency?.available && latency.delayed_fraction != null
                  ? fmtNumber(latency.delayed_fraction * 100, 1) : "N/A"
              }
              unit="%"
              note={latency?.available
                ? `${latency.delayed ?? 0} answered late (>200 ms)`
                : "no probe data"}
              tone={(latency?.delayed_fraction ?? 0) > 0.5 ? "warn" : undefined}
              explain="Delivered, but only after the next probe was due. A symptom of queueing, not loss."
            />
            <SpecRow
              label={`Max link utilisation${peakSuffix}`}
              value={fmtNumber((((utilSrc ?? values).max_link_util as number) ?? 0) * 100, 1)}
              unit="%"
              note={`A ${fmtPercent((utilSrc ?? values).util_A as number, 0)} · B ${fmtPercent((utilSrc ?? values).util_B as number, 0)}`}
              tone={(((utilSrc ?? values).max_link_util as number) ?? 0) > 0.9 ? "warn" : undefined}
            />
            <SpecRow
              label={`Flow-table occupancy${peakSuffix}`}
              value={fmtNumber((occupancySrc ?? values).total_rules as number, 0)}
              unit="rules"
              note="routed rules across all switches"
            />
            <SpecRow
              label={`Table misses${peakSuffix}`}
              value={fmtNumber((missSrc ?? values).table_misses as number, 0)}
              unit="packets"
              note={`${fmtNumber((missSrc ?? values).miss_rate_per_s as number, 1)} /s`}
            />
            <SpecRow
              label={`Controller PacketIn${peakSuffix}`}
              value={fmtNumber((packetInSrc ?? values).packet_in_rate as number, 1)}
              unit="msg/s"
              note="reactive load"
            />
            <SpecRow
              label={`Controller FlowMod${peakSuffix}`}
              value={fmtNumber((flowModSrc ?? values).flow_mod_rate as number, 1)}
              unit="msg/s"
              note="rule installations"
            />
          </div>
        </Card>
      </div>

      {/* The three supporting traces share a row. In an even 2x2 the delivered
          -throughput trace — the result the project reports — had exactly the
          same prominence as the controller's message rate. */}
      <div>
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
          <Chart
            title="Path utilisation"
            subtitle="Per-path bottleneck utilisation"
            data={chartData}
            unit="%"
            lines={[
              { key: "utilA", label: "Path A", color: PATH_COLOR.A },
              { key: "utilB", label: "Path B", color: PATH_COLOR.B },
            ]}
            caption="How full each candidate path is, as a % of its own link capacity. If one line sits near 100% while the other stays low, traffic is being forced onto one path instead of spread across both — the thing variants B/C try to improve on."
          />
          <Chart
            title="Flow-table occupancy"
            subtitle="Routed rules across all switches"
            data={chartData}
            lines={[{ key: "rules", label: "rules", color: "var(--rl)" }]}
            caption="How many forwarding rules the switches are holding right now. A steady climb means rules aren't expiring fast enough for the traffic (idle timeout too long); a flat line means the table has settled at a steady size."
          />
          <Chart
            title="Controller load"
            subtitle="Reactive messages only; polling excluded"
            data={chartData}
            unit="msg/s"
            lines={[
              { key: "packetIn", label: "PacketIn", color: "var(--warn)" },
              { key: "flowMod", label: "FlowMod", color: "var(--accent)" },
            ]}
            caption="PacketIn = packets a switch couldn't match locally and sent up to the controller; FlowMod = rules the controller pushed back down in response. Both low and steady is good — frequent spikes mean rules keep expiring and the switch keeps having to ask the controller again."
          />
        </div>
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

/** One supporting reading: name, figure, and how it was measured.
 *
 * These were nine equal cards; as rows they are scanned as a column and take a
 * third of the height, which is what leaves room for the charts to be the
 * thing you see first. */
function SpecRow({
  label, value, unit, note, tone, explain,
}: {
  label: string;
  value: string;
  unit: string;
  note: string;
  tone?: "warn" | "bad";
  explain?: string;
}) {
  const unavailable = value === "N/A" || value === "—";
  const color = unavailable
    ? "var(--text-4)"
    : tone === "bad"
      ? "var(--bad)"
      : tone === "warn" ? "var(--warn)" : "var(--text-1)";
  return (
    <div className="spec" title={explain ?? label}>
      <div className="min-w-0">
        <p className="t-small truncate" style={{ color: "var(--text-2)" }}>
          {label}
        </p>
        <p className="t-micro truncate" style={{ color: "var(--text-4)" }}>
          {note}
        </p>
      </div>
      <p className="readout text-[17px] whitespace-nowrap" style={{ color }}>
        {value}
        <span className="t-micro ml-1.5" style={{ color: "var(--text-4)" }}>
          {unavailable ? "" : unit}
        </span>
      </p>
    </div>
  );
}

function Chart({
  title, subtitle, data, lines, unit, tall, caption,
}: {
  title: string;
  subtitle?: string;
  data: Record<string, string | number>[];
  lines: { key: string; label: string; color: string }[];
  unit?: string;
  /** The page's primary trace, given roughly twice the plot height. */
  tall?: boolean;
  /** Plain-language "what this is telling you", shown under the chart. */
  caption?: string;
}) {
  if (data.length < 2) {
    return (
      <Card title={title} subtitle={subtitle}>
        <Empty title="Not enough samples yet" detail="Needs at least two valid intervals." />
      </Card>
    );
  }
  // A single series is drawn as a filled area: the fill gives the plot weight
  // and costs nothing, because there is no other series for it to hide. Two or
  // more stay as lines, where overlapping fills would occlude each other.
  const single = lines.length === 1;
  const axes = (
    <>
      <CartesianGrid stroke="var(--border-1)" strokeDasharray="2 5" vertical={false} />
      <XAxis
        dataKey="t" tick={{ fontSize: 10, fill: "var(--text-3)" }}
        axisLine={{ stroke: "var(--border-1)" }} tickLine={false} minTickGap={48}
        dy={4}
      />
      <YAxis
        tick={{ fontSize: 10, fill: "var(--text-3)" }}
        axisLine={false} tickLine={false} width={52}
        label={
          unit
            ? {
                value: unit, angle: -90, position: "insideLeft",
                offset: 24, fontSize: 9, fill: "var(--text-4)",
              }
            : undefined
        }
      />
      <Tooltip
        contentStyle={tooltipStyle}
        labelStyle={tooltipLabelStyle}
        cursor={{ stroke: "var(--accent-ring)", strokeDasharray: "3 4" }}
        formatter={(value: number, name: string) => [
          `${fmtNumber(value, 2)}${unit ? ` ${unit}` : ""}`, name,
        ]}
      />
    </>
  );

  return (
    <Card title={title} subtitle={subtitle}>
      <div style={{ height: tall ? 300 : 204 }}>
        <ResponsiveContainer width="100%" height="100%">
          {single ? (
            <AreaChart data={data} margin={{ top: 8, right: 10, bottom: 0, left: -14 }}>
              <defs>
                <linearGradient id={`fill-${lines[0].key}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor={lines[0].color} stopOpacity={0.4} />
                  <stop offset="100%" stopColor={lines[0].color} stopOpacity={0} />
                </linearGradient>
              </defs>
              {axes}
              <Area
                type="monotone" dataKey={lines[0].key} name={lines[0].label}
                stroke={lines[0].color} strokeWidth={2}
                fill={`url(#fill-${lines[0].key})`}
                dot={false} isAnimationActive={false}
              />
            </AreaChart>
          ) : (
            <LineChart data={data} margin={{ top: 8, right: 10, bottom: 0, left: -14 }}>
              {axes}
              {lines.map((l) => (
                <Line
                  key={l.key} type="monotone" dataKey={l.key} name={l.label}
                  stroke={l.color} strokeWidth={2} dot={false} isAnimationActive={false}
                />
              ))}
            </LineChart>
          )}
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
      {caption && (
        <p
          className="text-[11px] leading-relaxed mt-2 pt-2 border-t"
          style={{ color: "var(--text-3)", borderColor: "var(--border-1)" }}
        >
          {caption}
        </p>
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
            borderColor: "color-mix(in srgb, var(--bad) 35%, transparent)",
            background: "var(--bad-dim)",
            color: "var(--bad)",
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
