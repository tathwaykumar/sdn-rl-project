/** Decision Inspector.
 *
 * Hosts the Decision Journey — the Observe → Choose Action → Apply Decision →
 * Measure Outcome → Calculate Reward → Update Learning → Next Decision
 * sequence for one specific, actually-recorded decision — plus a history
 * table to step through every decision in the run.
 *
 * The core discipline this page enforces throughout: an agent decision is an
 * intention, a switch-confirmed action is a fact, and the reward is a
 * consequence measured after the fact, never the stated reason for the
 * action. Nothing here claims more than the recorded data supports.
 */
import { useMemo, useState, type ReactNode } from "react";
import {
  Area, CartesianGrid, ComposedChart, Line, ReferenceArea, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../api/client";
import { useAsync } from "../hooks/useLive";
import type {
  Decision, LiveSnapshot, MetricSeries, RewardExplain,
} from "../api/types";
import {
  Card, ChartLegend, Details, Empty, ErrorBox, Pill, Skeleton, SourceBadge, cx,
  tooltipStyle,
  fmtNumber, fmtTime,
} from "../components/ui";
import { DecisionJourney } from "./DecisionJourney";

export function DecisionInspectorView({
  runId, snapshot,
}: {
  runId?: string;
  snapshot: LiveSnapshot | null;
}) {
  const decisions = useAsync(
    () => api.decisions(runId, 150),
    [runId],
    runId ? undefined : 4000,
  );
  const rewardExplain = useAsync<RewardExplain>(
    () => api.rewardExplain(runId, 150),
    [runId],
    runId ? undefined : 4000,
  );
  const [selected, setSelected] = useState<number | null>(null);

  const live = !runId ? snapshot?.decisions : undefined;
  const rows: Decision[] = (live?.length ? live : decisions.data?.decisions) ?? [];
  const states = decisions.data?.states ?? {};

  const effectiveRunId = runId ?? snapshot?.live?.run_id ?? undefined;
  const runDetail = useAsync(
    () => (effectiveRunId ? api.run(effectiveRunId) : Promise.resolve(null)),
    [effectiveRunId],
  );
  const agentConfig = (runDetail.data as Record<string, any> | null)
    ?.config?.agent as { alpha?: number; gamma?: number } | undefined;

  const { current, previous, next } = useMemo(() => {
    if (!rows.length) return { current: null, previous: null, next: null };
    const idx = selected === null
      ? rows.length - 1
      : rows.findIndex((r) => r.step === selected);
    const i = idx < 0 ? rows.length - 1 : idx;
    return {
      current: rows[i] ?? null,
      previous: i > 0 ? rows[i - 1] : null,
      next: i < rows.length - 1 ? rows[i + 1] : null,
    };
  }, [rows, selected]);

  const series = useAsync<MetricSeries>(
    () => api.metricsSeries(runId, 400),
    [runId],
    runId ? undefined : 6000,
  );

  const rewardStep = useMemo(() => {
    if (!current || !rewardExplain.data?.steps) return undefined;
    return rewardExplain.data.steps.find((s) => s.step === current.step);
  }, [current, rewardExplain.data]);

  if (decisions.loading && !decisions.data) {
    return <Card title="Decision Inspector"><Skeleton rows={8} /></Card>;
  }
  if (decisions.error && !decisions.data) {
    return (
      <Card title="Decision Inspector">
        <ErrorBox message={decisions.error} onRetry={decisions.refresh} />
      </Card>
    );
  }
  if (!rows.length) {
    return (
      <Card title="Decision Inspector" right={<SourceBadge source="unavailable" />}>
        <Empty
          title="No decisions recorded"
          detail={
            decisions.data?.reason ??
            "The agent records one decision per polling interval. Start an experiment, or open a completed run from History."
          }
        />
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <Card accent="rl">
        <p className="t-body text-balance" style={{ color: "var(--text-2)" }}>
          Follow one decision through its full life cycle below: what the agent{" "}
          <strong>observed</strong>, what it <strong>chose</strong> and why
          (greedy or exploring), whether the controller actually{" "}
          <strong>applied</strong> it to the switches, what the network{" "}
          <strong>measured</strong> afterward, how that became a{" "}
          <strong>reward</strong>, how the agent's knowledge{" "}
          <strong>updated</strong>, and what that means for the{" "}
          <strong>next</strong> time this situation comes up.
        </p>
      </Card>

      <DecisionTimeline
        decisions={rows}
        series={series.data}
        selectedStep={current?.step ?? null}
        onSelect={setSelected}
      />

      {current && (
        <DecisionJourney
          decision={current}
          previousDecision={previous}
          nextDecision={next}
          decoded={states[current.state]}
          rewardStep={rewardStep}
          runId={runId}
          agentConfig={agentConfig}
        />
      )}

      <Card
        title="All decisions in this run"
        subtitle={`${rows.length} intervals · click any row to follow its journey`}
        right={<SourceBadge source={decisions.data?.source as never} />}
        bodyClassName="p-0"
      >
        <div className="overflow-auto" style={{ maxHeight: 420 }}>
          <table className="w-full text-[11.5px]">
            <thead className="sticky top-0 z-10" style={{ background: "var(--surface-2)" }}>
              <tr style={{ color: "var(--text-4)" }}>
                <Th>Time</Th><Th>Step</Th><Th>Path</Th><Th right>Idle</Th>
                <Th>Policy</Th><Th right>Reward</Th>
              </tr>
            </thead>
            <tbody>
              {[...rows].reverse().map((d) => {
                const active = current?.step === d.step;
                return (
                  <tr
                    key={d.step}
                    onClick={() => setSelected(d.step)}
                    className="border-t cursor-pointer transition-colors"
                    style={{
                      borderColor: "var(--border-1)",
                      background: active ? "var(--rl-dim)" : undefined,
                    }}
                  >
                    <td className="px-3 py-1.5 num">{fmtTime(d.t)}</td>
                    <td className="px-3 py-1.5 num">{d.step}</td>
                    <td className="px-3 py-1.5">
                      <Pill color={d.path === "A" ? "var(--variant-a)" : "var(--variant-c)"}>
                        {d.path || "—"}
                      </Pill>
                    </td>
                    <td className="px-3 py-1.5 num text-right">
                      {d.idle_timeout !== null ? `${d.idle_timeout}s` : "—"}
                    </td>
                    <td className="px-3 py-1.5">
                      {d.exploratory === null ? (
                        <span style={{ color: "var(--text-4)" }}>n/a</span>
                      ) : d.exploratory ? (
                        <span style={{ color: "var(--warn)" }}>explore</span>
                      ) : (
                        <span style={{ color: "var(--text-2)" }}>greedy</span>
                      )}
                    </td>
                    <td className="px-3 py-1.5 num text-right"
                        style={{ color: (d.reward ?? 0) >= 0 ? "var(--ok)" : "var(--bad)" }}>
                      {fmtNumber(d.reward, 3)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

const PATH_COLOR: Record<string, string> = {
  A: "var(--variant-a)",
  B: "var(--variant-c)",
};

/** The run on one clock: which path the agent selected, against what the
 * network was measured doing at the same moment.
 *
 * This is the join the console was missing. Decisions lived on this page,
 * throughput and utilisation lived on the Metrics page, and nothing put them
 * on a shared axis — so the obvious question ("the agent switched path here;
 * what happened?") could not be asked of the data at all.
 *
 * The tinted bands are the agent's *selection*, which is an intention. They
 * are not evidence that traffic took that path; per-decision confirmation
 * against the switches' own flow tables is in the journey above. And because
 * the workload and the network move on their own, alignment in time is not
 * causation — the caption says so rather than letting the picture imply it.
 */
function DecisionTimeline({
  decisions, series, selectedStep, onSelect,
}: {
  decisions: Decision[];
  series?: MetricSeries | null;
  selectedStep: number | null;
  onSelect: (step: number) => void;
}) {
  const { points, bands, marks, hasUtil } = useMemo(() => {
    const raw = (series?.points ?? []).filter(
      (p) => (p as { valid?: boolean }).valid !== false,
    ) as unknown as Record<string, number | null>[];

    const withT = decisions.filter((d) => typeof d.t === "number");
    const times = [
      ...raw.map((p) => Number(p.t)),
      ...withT.map((d) => d.t as number),
    ].filter((n) => Number.isFinite(n));

    if (!raw.length || !times.length) {
      return { points: [], bands: [], marks: [], hasUtil: false };
    }
    const t0 = Math.min(...times);

    const pts = raw.map((p) => ({
      at: Number(p.t) - t0,
      throughput: p.delivered_bps != null ? Number(p.delivered_bps) / 1e6 : null,
      util: p.max_link_util != null ? Number(p.max_link_util) * 100 : null,
    }));

    // One band per contiguous run of the same selected path.
    const ordered = [...withT].sort((a, b) => (a.t as number) - (b.t as number));
    const end = Math.max(...pts.map((p) => p.at));
    const segs: { from: number; to: number; path: string }[] = [];
    ordered.forEach((d, i) => {
      const from = (d.t as number) - t0;
      const to = i + 1 < ordered.length
        ? (ordered[i + 1].t as number) - t0
        : end;
      if (!d.path) return;
      const last = segs[segs.length - 1];
      if (last && last.path === d.path) last.to = to;
      else segs.push({ from, to, path: d.path });
    });

    return {
      points: pts,
      bands: segs,
      marks: ordered.map((d) => ({ step: d.step, at: (d.t as number) - t0, path: d.path })),
      hasUtil: pts.some((p) => p.util !== null),
    };
  }, [decisions, series]);

  if (!points.length) {
    return (
      <Card title="Decisions against the network" accent="net">
        <Empty
          title="No aligned telemetry for this run"
          detail="This view needs both a decision log and an interval metric series recorded on the same clock."
        />
      </Card>
    );
  }

  const selected = marks.find((m) => m.step === selectedStep);
  const paths = [...new Set(bands.map((b) => b.path))];

  return (
    <Card
      title="Decisions against the network"
      subtitle="Each band is the path the agent selected; the curves are what was measured at the same moment"
      accent="net"
      bodyClassName="p-3"
      right={<SourceBadge source={series?.source as never} />}
    >
      <div style={{ height: 224 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={points}
            margin={{ top: 8, right: 6, bottom: 2, left: -16 }}
            onClick={(e) => {
              // Clicking the run jumps the journey to the nearest decision.
              const at = Number(
                (e as { activeLabel?: number | string } | undefined)?.activeLabel,
              );
              if (!Number.isFinite(at) || !marks.length) return;
              const nearest = marks.reduce((best, m) =>
                Math.abs(m.at - at) < Math.abs(best.at - at) ? m : best,
              );
              onSelect(nearest.step);
            }}
          >
            <CartesianGrid stroke="var(--border-1)" strokeDasharray="2 5" vertical={false} />
            {bands.map((b, i) => (
              <ReferenceArea
                key={i} x1={b.from} x2={b.to} yAxisId="tp"
                fill={PATH_COLOR[b.path] ?? "var(--idle)"} fillOpacity={0.11}
                stroke="none" ifOverflow="extendDomain"
              />
            ))}
            <XAxis
              dataKey="at" type="number" tick={{ fontSize: 9.5 }} tickLine={false}
              axisLine={{ stroke: "var(--border-1)" }}
              domain={["dataMin", "dataMax"]} minTickGap={30}
              tickFormatter={(v: number) => `${Math.round(v)}s`}
            />
            <YAxis
              yAxisId="tp" tick={{ fontSize: 9.5 }} tickLine={false} axisLine={false}
              width={42}
            />
            {hasUtil && (
              <YAxis
                yAxisId="u" orientation="right" domain={[0, 100]} width={32}
                tick={{ fontSize: 9.5, fill: "var(--text-4)" }} tickLine={false}
                axisLine={false}
              />
            )}
            <Tooltip
              contentStyle={tooltipStyle}
              labelFormatter={(v: number) => `${fmtNumber(v, 1)} s into the run`}
              formatter={(value: number, name: string) => [
                fmtNumber(value, name === "throughput" ? 2 : 1),
                name === "throughput" ? "throughput (Mbit/s)" : "max link utilisation (%)",
              ]}
            />
            <Area
              yAxisId="tp" type="monotone" dataKey="throughput"
              stroke="var(--accent)" strokeWidth={2} fill="var(--accent)"
              fillOpacity={0.14} dot={false} isAnimationActive={false} connectNulls
            />
            {hasUtil && (
              <Line
                yAxisId="u" type="monotone" dataKey="util" stroke="var(--warn)"
                strokeWidth={1.5} strokeDasharray="4 4" dot={false}
                isAnimationActive={false} connectNulls
              />
            )}
            {selected && (
              <ReferenceLine
                yAxisId="tp" x={selected.at} stroke="var(--rl)" strokeWidth={1.5}
                label={{
                  value: `step ${selected.step}`, position: "top",
                  fontSize: 9, fill: "var(--rl)",
                }}
              />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <ChartLegend
        items={[
          ...paths.map((p) => ({
            label: `path ${p} selected`,
            color: PATH_COLOR[p] ?? "var(--idle)",
            shape: "band" as const,
            hint: "The agent's choice for that interval — an intention, not confirmed forwarding.",
          })),
          {
            label: "throughput, left axis", color: "var(--accent)", shape: "area" as const,
            hint: "Delivered to hosts, from edge-switch egress counters (Mbit/s).",
          },
          ...(hasUtil
            ? [{
                label: "max link utilisation, right axis",
                color: "var(--warn)", shape: "dashed" as const,
                hint: "The busiest link's utilisation at that moment (%).",
              }]
            : []),
        ]}
        note="click anywhere to inspect the nearest decision"
      />

      <Details summary="What this shows, and what it cannot">
        <ul className="space-y-1.5 leading-relaxed">
          <li>
            <strong>The bands are intent.</strong> They mark the path the agent
            selected for each interval. Whether the switches actually carried
            traffic that way is a separate question, answered per decision in
            the journey above from the switches&rsquo; own flow tables.
          </li>
          <li>
            <strong>Alignment is not causation.</strong> The workload and the
            network change on their own, so throughput moving after a path
            change is not evidence that the path change caused it. Use the
            A/B/C Comparison page, which tests variants against a baseline
            across seeds, for claims about effect.
          </li>
          <li>
            <strong>Both series are measured.</strong> Throughput comes from the
            edge switches&rsquo; egress counters and utilisation from per-link
            counters, both on the same polling clock as the decisions.
          </li>
        </ul>
      </Details>
    </Card>
  );
}

function Th({ children, right }: { children: ReactNode; right?: boolean }) {
  return (
    <th className={cx(
      "px-3 py-2 font-medium text-[9.5px] uppercase tracking-wider whitespace-nowrap",
      right ? "text-right" : "text-left",
    )}>
      {children}
    </th>
  );
}
