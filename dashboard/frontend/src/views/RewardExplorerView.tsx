/** Reward Explorer.
 *
 * Explains the agent's score using the *actual* reward implementation: the
 * formula, weights and normalisers come from the backend, which reads them
 * from `agent/reward.py` and from the run's own `config.json`. The per-step
 * component values are the ones the controller recorded.
 *
 * Every breakdown shown here is reconciled against the recorded total on the
 * server, and the reconciliation result is displayed rather than assumed. If
 * the components did not sum to the recorded score, this page would say so.
 */
import { useMemo, useState } from "react";
import {
  Area, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Line,
  ReferenceLine, ResponsiveContainer, Scatter, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../api/client";
import { useAsync } from "../hooks/useLive";
import type { RewardExplain, RewardStep } from "../api/types";
import {
  AboutPanel, Card, ChartLegend, Empty, ErrorBox, Pill, Skeleton,
  SourceBadge, StatusDot, tooltipStyle,
  cx, fmtNumber, fmtTime, inputClass, inputStyle,
} from "../components/ui";

const BAND_STYLE: Record<string, { tone: "ok" | "warn" | "bad"; label: string; text: string }> = {
  positive: {
    tone: "ok", label: "Positive",
    text: "This interval delivered more value than it cost.",
  },
  near_zero: {
    tone: "warn", label: "Near zero",
    text: "Gains and costs roughly cancelled, or very little traffic moved.",
  },
  negative: {
    tone: "bad", label: "Negative",
    text: "The costs outweighed what the network delivered.",
  },
};

export function RewardExplorerView({ runId }: { runId?: string }) {
  const { data, loading, error, refresh } = useAsync<RewardExplain>(
    () => api.rewardExplain(runId, 200),
    [runId],
    runId ? undefined : 4000,
  );
  const [selected, setSelected] = useState<number | null>(null);

  const steps = data?.steps ?? [];
  const current = useMemo(() => {
    if (!steps.length) return null;
    if (selected === null) return steps[steps.length - 1];
    return steps.find((s) => s.step === selected) ?? steps[steps.length - 1];
  }, [steps, selected]);

  if (loading && !data) return <Card title="Reward Explorer"><Skeleton rows={8} /></Card>;
  if (error && !data) {
    return <Card title="Reward Explorer"><ErrorBox message={error} onRetry={refresh} /></Card>;
  }

  const model = data?.model;

  const about = (
    <AboutPanel
      summary={
        <>
          How the agent scored every decision it made, and why. The formula,
          weights and per-term values are read from the controller's own
          reward implementation and the run's recorded config — nothing here
          is recomputed or estimated.
        </>
      }
      terms={[
        { term: "Term value", def: "One measured quantity (e.g. throughput), scaled to 0–1. Not yet weighted." },
        { term: "Contribution", def: "Term value × its weight × sign — what that term actually adds or subtracts." },
        { term: "Reconciled", def: "The contributions were summed and checked against the recorded score." },
        { term: "ε (epsilon)", def: "Chance of a random action instead of the current best-known one." },
      ]}
    />
  );

  if (!data?.available || steps.length === 0) {
    // A line, not a plate. There is nothing to plot, so a full-height panel
    // here would reserve the space a chart would have taken and fill it with
    // the words "no data" — the scoring breakdown below is the useful thing
    // on this page until a run is open.
    return (
      <div className="space-y-6">
        {about}
        <div className="panel px-5 py-3.5 flex flex-wrap items-center gap-x-3 gap-y-1.5">
          <StatusDot tone="idle" />
          <span className="t-body" style={{ color: "var(--text-2)" }}>
            No reward data yet
          </span>
          <span className="t-small" style={{ color: "var(--text-3)" }}>
            {data?.reason ??
              "The agent records a reward once per polling interval. Start an experiment, or open a completed run from History."}
          </span>
          <span className="ml-auto shrink-0">
            <SourceBadge source="unavailable" />
          </span>
        </div>
        {model && <FormulaCard model={model} />}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {about}
      <IntroCard model={model!} source={data.source} />

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_minmax(0,420px)] gap-5">
        <div className="space-y-4">
          <RewardHistory
            steps={steps}
            selectedStep={current?.step ?? null}
            onSelect={(s) => setSelected(s)}
          />
          <LearningDynamics steps={steps} />
          {current && <RewardWaterfall step={current} />}
          {current && <ContributionChart step={current} />}
        </div>

        <div className="space-y-4">
          {current && (
            <StepInspector
              step={current}
              steps={steps}
              onSelect={setSelected}
            />
          )}
          <ReconciliationCard data={data} />
        </div>
      </div>

      <FormulaCard model={model!} step={current} />
    </div>
  );
}

function IntroCard({ model, source }: { model: RewardExplain["model"]; source?: string }) {
  return (
    <Card
      title="How the agent scores itself"
      right={<SourceBadge source={source as never} />}
    >
      <p className="text-[12.5px] leading-relaxed text-balance" style={{ color: "var(--text-2)" }}>
        {model.plain_summary}
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {model.scale_guide.map((band) => {
          const style = BAND_STYLE[band.band];
          return (
            <div
              key={band.band}
              className="flex items-start gap-2 rounded-lg border px-3 py-2 flex-1 min-w-[180px]"
              style={{ borderColor: "var(--border-1)", background: "var(--surface-2)" }}
            >
              <StatusDot tone={style?.tone ?? "idle"} />
              <div className="min-w-0">
                <p className="text-[11.5px] font-medium">
                  {style?.label ?? band.band}
                  <span className="ml-1.5 num" style={{ color: "var(--text-4)" }}>
                    {band.range}
                  </span>
                </p>
                <p className="text-[11px] leading-snug" style={{ color: "var(--text-3)" }}>
                  {band.meaning}
                </p>
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function RewardHistory({
  steps, selectedStep, onSelect,
}: {
  steps: RewardStep[];
  selectedStep: number | null;
  onSelect: (step: number) => void;
}) {
  const chart = steps.map((s) => ({
    step: s.step,
    reward: s.reward ?? 0,
    t: s.t,
  }));
  const rewards = chart.map((c) => c.reward);
  const mean = rewards.length
    ? rewards.reduce((a, b) => a + b, 0) / rewards.length
    : 0;

  return (
    <Card
      title="Reward over the run"
      subtitle={`${steps.length} decision intervals · click a point to inspect it`}
      right={
        <span className="text-[11px] num" style={{ color: "var(--text-3)" }}>
          mean {fmtNumber(mean, 3)}
        </span>
      }
    >
      <div style={{ height: 252 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart
            data={chart}
            margin={{ top: 6, right: 10, bottom: 0, left: -14 }}
            onClick={(e) => {
              const point = e?.activePayload?.[0]?.payload as { step: number } | undefined;
              if (point) onSelect(point.step);
            }}
          >
            <defs>
              <linearGradient id="reward-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--rl)" stopOpacity={0.38} />
                <stop offset="100%" stopColor="var(--rl)" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid stroke="var(--border-1)" strokeDasharray="2 5" vertical={false} />
            <XAxis
              dataKey="step" tick={{ fontSize: 10 }} tickLine={false}
              axisLine={{ stroke: "var(--border-1)" }} minTickGap={28}
              label={{ value: "decision step", position: "insideBottom", offset: -2,
                       fontSize: 10, fill: "var(--text-4)" }}
            />
            <YAxis
              tick={{ fontSize: 10 }} tickLine={false} axisLine={false} width={46}
              domain={[-1, 1]}
              label={{ value: "reward", angle: -90, position: "insideLeft",
                       fontSize: 10, fill: "var(--text-4)", offset: 18 }}
            />
            <ReferenceLine y={0} stroke="var(--border-3)" strokeWidth={1} />
            <Tooltip
              contentStyle={tooltipStyle}
              labelFormatter={(v) => `Step ${v}`}
              formatter={(v: number) => [fmtNumber(v, 4), "reward"]}
            />
            {/* Filled from the zero line rather than from the axis floor, so
                the shaded area is the reward actually earned above (or lost
                below) break-even, not an artefact of the clamp at -1. */}
            <Area
              type="monotone" dataKey="reward" name="reward" baseValue={0}
              stroke="var(--rl)" strokeWidth={2} fill="url(#reward-fill)"
              dot={false} isAnimationActive={false}
              activeDot={{ r: 4, fill: "var(--rl)" }}
            />
            {selectedStep !== null && (
              <ReferenceLine x={selectedStep} stroke="var(--accent)" strokeDasharray="3 3" />
            )}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <p className="t-small leading-relaxed mt-2" style={{ color: "var(--text-3)" }}>
        <strong>Horizontal axis:</strong> decision step — one per polling
        interval, in order. <strong>Vertical axis:</strong> the recorded
        reward for that interval, from -1 (worst) to +1 (best); the dotted
        line at 0 is break-even. The filled area is shaded from 0, so it
        shows reward actually earned above break-even or lost below it, not
        the distance down to -1. The dashed green line marks whichever step
        is selected below.
      </p>
    </Card>
  );
}

/** Learning dynamics over the run.
 *
 * The question an RL project is always asked — "is the agent actually
 * learning?" — had no chart anywhere in this console. Reward was plotted raw,
 * per step, which is far too noisy to read a trend from, and the three signals
 * that bear on the question were recorded but never drawn.
 *
 * Three series, each from the recorded decision log:
 *   - reward, raw and as a rolling mean, so a trend is visible through noise;
 *   - epsilon, the exploration rate, which *declines by configuration* and is
 *     therefore context for the reward trend, not a result of it;
 *   - |TD error|, how far each update moved its value estimate. This shrinking
 *     is evidence the estimates are settling, which is a statement about the
 *     table converging and not about the policy being good.
 *
 * What this deliberately does not say: that a rising reward proves a better
 * policy. Reward also moves with the workload and the network, and the
 * exploration rate is falling on a schedule the whole time, so the caption
 * states the trend and stops there.
 */
function LearningDynamics({ steps }: { steps: RewardStep[] }) {
  const WINDOW = 10;

  const { rows, stats } = useMemo(() => {
    const usable = steps.filter((s) => s.reward !== null);
    const rewards = usable.map((s) => s.reward as number);

    const rollingAt = (values: (number | null)[], i: number) => {
      const from = Math.max(0, i - WINDOW + 1);
      const slice = values.slice(from, i + 1).filter((v): v is number => v !== null);
      if (!slice.length) return null;
      return slice.reduce((a, b) => a + b, 0) / slice.length;
    };

    const absTd = usable.map((s) =>
      s.td_error === null ? null : Math.abs(s.td_error),
    );

    const built = usable.map((s, i) => ({
      step: s.step,
      reward: s.reward as number,
      rewardTrend: rollingAt(rewards, i),
      epsilon: s.epsilon,
      absTd: absTd[i],
      absTdTrend: rollingAt(absTd, i),
      // Plotted as its own series so exploratory steps are visible as points
      // rather than inferred from the line.
      exploreReward: s.exploratory ? (s.reward as number) : null,
    }));

    const explored = usable.filter((s) => s.exploratory === true).length;
    const knownExplore = usable.filter((s) => s.exploratory !== null).length;

    // First half vs second half of |TD error|, as a plain descriptive
    // comparison of recorded values. No test is implied at this sample size.
    const td = absTd.filter((v): v is number => v !== null);
    const mid = Math.floor(td.length / 2);
    const meanOf = (xs: number[]) =>
      xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : null;

    return {
      rows: built,
      stats: {
        n: usable.length,
        explored,
        knownExplore,
        epsFirst: built.find((r) => r.epsilon !== null)?.epsilon ?? null,
        epsLast: [...built].reverse().find((r) => r.epsilon !== null)?.epsilon ?? null,
        tdFirstHalf: meanOf(td.slice(0, mid)),
        tdSecondHalf: meanOf(td.slice(mid)),
      },
    };
  }, [steps]);

  if (rows.length < 2) {
    return (
      <Card title="Learning dynamics" accent="rl">
        <Empty
          title="Not enough decisions recorded to show a trend"
          detail="A trend needs at least two intervals with a recorded reward."
        />
      </Card>
    );
  }

  const hasEpsilon = rows.some((r) => r.epsilon !== null);
  const hasTd = rows.some((r) => r.absTd !== null);

  return (
    <Card
      title="Learning dynamics"
      subtitle={`${stats.n} decision intervals · reward with a ${WINDOW}-step rolling mean`}
      accent="rl"
      right={
        stats.knownExplore > 0 ? (
          <Pill color="var(--warn)" title="Steps where the agent took a random action instead of its current best.">
            {fmtNumber((stats.explored / stats.knownExplore) * 100, 0)}% exploratory
          </Pill>
        ) : undefined
      }
    >
      <div style={{ height: 254 }}>
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 6, right: 4, bottom: 0, left: -16 }}>
            <CartesianGrid stroke="var(--border-1)" strokeDasharray="2 5" vertical={false} />
            <XAxis
              dataKey="step" tick={{ fontSize: 9.5 }} tickLine={false}
              axisLine={{ stroke: "var(--border-1)" }} minTickGap={26}
            />
            <YAxis
              yAxisId="r" tick={{ fontSize: 9.5 }} tickLine={false} axisLine={false}
              width={42} domain={[-1, 1]}
            />
            {hasEpsilon && (
              <YAxis
                yAxisId="e" orientation="right" domain={[0, 1]} width={30}
                tick={{ fontSize: 9.5, fill: "var(--text-4)" }} tickLine={false}
                axisLine={false}
              />
            )}
            <ReferenceLine yAxisId="r" y={0} stroke="var(--border-3)" />
            <Tooltip
              contentStyle={tooltipStyle}
              labelFormatter={(v) => `Step ${v}`}
              formatter={(value: number, name: string) => {
                const labels: Record<string, string> = {
                  reward: "reward (recorded)",
                  rewardTrend: `reward, ${WINDOW}-step mean`,
                  epsilon: "ε exploration rate (scheduled)",
                  exploreReward: "exploratory step",
                };
                return [fmtNumber(value, 4), labels[name] ?? name];
              }}
            />
            {/* Raw reward, kept faint: the trend line is what should be read. */}
            <Line
              yAxisId="r" type="linear" dataKey="reward" stroke="var(--rl)"
              strokeWidth={1} strokeOpacity={0.3} dot={false} isAnimationActive={false}
            />
            <Area
              yAxisId="r" type="monotone" dataKey="rewardTrend" stroke="var(--rl)"
              strokeWidth={2.2} fill="var(--rl)" fillOpacity={0.12}
              dot={false} isAnimationActive={false} connectNulls
            />
            {hasEpsilon && (
              <Line
                yAxisId="e" type="monotone" dataKey="epsilon" stroke="var(--warn)"
                strokeWidth={1.6} strokeDasharray="4 4" dot={false}
                isAnimationActive={false} connectNulls
              />
            )}
            <Scatter
              yAxisId="r" dataKey="exploreReward" fill="var(--warn)"
              shape="circle" isAnimationActive={false} legendType="none"
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>

      <ChartLegend
        items={[
          {
            label: `reward, ${WINDOW}-step mean`, color: "var(--rl)", shape: "area",
            hint: "A rolling mean of the recorded rewards — a smoothing, not a model.",
          },
          {
            label: "reward, per step", color: "var(--rl)", shape: "line", faint: true,
            hint: "The value recorded for each individual interval.",
          },
          ...(hasEpsilon
            ? [{
                label: "ε, right axis", color: "var(--warn)", shape: "dashed" as const,
                hint: "Exploration rate. Decays on a fixed schedule set by configuration.",
              }]
            : []),
          {
            label: "exploratory step", color: "var(--warn)", shape: "dot",
            hint: "The agent took a random action instead of its current best.",
          },
        ]}
        note="left axis: reward, clipped to ±1"
      />

      {hasTd && (
        <div className="mt-3 pt-3 border-t" style={{ borderColor: "var(--border-1)" }}>
          <div className="flex items-baseline justify-between gap-3 mb-1">
            <p className="t-label" style={{ color: "var(--text-3)" }}>
              Value-estimate movement
            </p>
            <p className="t-small" style={{ color: "var(--text-4)" }}>
              mean |TD error| {fmtNumber(stats.tdFirstHalf, 4)} → {fmtNumber(stats.tdSecondHalf, 4)}
              <span className="ml-1">(first half → second half)</span>
            </p>
          </div>
          <div style={{ height: 96 }}>
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={rows} margin={{ top: 4, right: 4, bottom: 0, left: -16 }}>
                <CartesianGrid stroke="var(--border-1)" strokeDasharray="2 5" vertical={false} />
                <XAxis dataKey="step" tick={{ fontSize: 9 }} tickLine={false}
                       axisLine={{ stroke: "var(--border-1)" }} minTickGap={26} />
                <YAxis tick={{ fontSize: 9 }} tickLine={false} axisLine={false} width={42} />
                <Tooltip
                  contentStyle={tooltipStyle}
                  labelFormatter={(v) => `Step ${v}`}
                  formatter={(value: number, name: string) => [
                    fmtNumber(value, 4),
                    name === "absTdTrend" ? `|TD error|, ${WINDOW}-step mean` : "|TD error|",
                  ]}
                />
                <Line type="linear" dataKey="absTd" stroke="var(--accent)" strokeWidth={1}
                      strokeOpacity={0.28} dot={false} isAnimationActive={false} />
                <Area type="monotone" dataKey="absTdTrend" stroke="var(--accent)"
                      strokeWidth={2} fill="var(--accent)" fillOpacity={0.12}
                      dot={false} isAnimationActive={false} connectNulls />
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      <div className="mt-3 pt-3 border-t" style={{ borderColor: "var(--hairline)" }}>
        <p className="t-label mb-1.5" style={{ color: "var(--text-4)" }}>
          How to read this, and what it does not show
        </p>
        <ul className="space-y-1.5 leading-relaxed t-small" style={{ color: "var(--text-2)" }}>
          <li>
            <strong>Reward trend</strong> (solid filled line, left axis): a{" "}
            {WINDOW}-step rolling mean of the rewards actually recorded — the
            average of this step and the {WINDOW - 1} before it. The faint
            thin line underneath is the raw, un-averaged per-step value; it is
            noisy on its own, which is why the mean is the one to read.
          </li>
          {hasEpsilon && (
            <li>
              <strong>ε, exploration rate</strong> (dashed line, right axis):
              falls from {fmtNumber(stats.epsFirst, 3)} to{" "}
              {fmtNumber(stats.epsLast, 3)} across this run because the
              configuration decays it on a fixed timetable — it is a
              schedule, not a result. It is drawn here because exploration
              depresses reward early on, so the reward trend has to be read
              against it: a dip while ε is still high may just be the agent
              trying things, not the policy getting worse.
            </li>
          )}
          <li>
            <strong>Dots</strong> mark exploratory steps: the agent picked a
            random action instead of its current best, specifically to keep
            learning about options it might otherwise never revisit.
          </li>
          {hasTd && (
            <li>
              <strong>Value-estimate movement</strong> (the small chart below):
              |TD error| is how far each Q-learning update moved an action's
              value estimate — see the Decision Inspector for exactly how that
              update is computed. If it shrinks over the run, the Q-table is
              settling toward stable values. That is evidence the estimates
              are converging; it is not, by itself, evidence that the
              resulting policy routes traffic better.
            </li>
          )}
          <li>
            <strong>What this chart cannot show.</strong> Reward depends on
            the live network as well as the agent's choices, so a rising
            trend is not proof the agent learned a better policy — and this
            page has only one run to look at, so it cannot say whether this
            run beat another variant. The A/B/C Comparison page is where
            variants are tested against a baseline with confidence intervals
            across multiple seeds.
          </li>
        </ul>
      </div>
    </Card>
  );
}

/** Waterfall of the weighted reward terms.
 *
 * Reads left to right as the score is actually assembled: start at zero, add
 * the throughput credit, then subtract each cost term, and land on the
 * recorded reward. The bar chart below answers "how big is each term"; this
 * answers "how did the number get here", which is the question someone meeting
 * the reward function for the first time actually has.
 *
 * Built with the invisible-base technique: each row carries a transparent
 * `base` bar that lifts the visible `delta` bar to where the running total
 * sits, both in one stack.
 */
function RewardWaterfall({ step }: { step: RewardStep }) {
  const present = step.contributions.filter((c) => c.contribution !== null);

  if (!present.length) {
    return (
      <Card title="How this reward was built" accent="rl">
        <Empty
          title="No component values recorded for this interval"
          detail="The waterfall is drawn only from recorded per-term values; it is never reconstructed from the total."
        />
      </Card>
    );
  }

  let running = 0;
  const bars = present.map((c) => {
    const v = c.contribution as number;
    const base = v >= 0 ? running : running + v;
    running += v;
    return {
      label: c.label,
      base,
      delta: Math.abs(v),
      signed: v,
      term: c.term_value,
      weight: c.weight,
      isTotal: false,
    };
  });

  // The closing bar rises from zero so it reads as the resulting level, not as
  // one more increment.
  const total = {
    label: "Reward",
    base: Math.min(0, running),
    delta: Math.abs(running),
    signed: running,
    term: null as number | null,
    weight: null as number | null,
    isTotal: true,
  };

  const rows = [...bars, total];

  return (
    <Card
      title="How this reward was built"
      subtitle={`Step ${step.step} — start at zero, apply each weighted term in order, land on the recorded score`}
      accent="rl"
      right={
        step.reconciles ? (
          <Pill color="var(--ok)" title="Components sum to the recorded reward.">
            reconciled
          </Pill>
        ) : (
          <Pill color="var(--warn)" title="See the reconciliation panel.">
            check
          </Pill>
        )
      }
    >
      <div style={{ height: 272 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} margin={{ top: 8, right: 8, bottom: 4, left: -12 }}>
            <CartesianGrid stroke="var(--border-1)" strokeDasharray="2 5" vertical={false} />
            <XAxis
              dataKey="label" tick={{ fontSize: 9.5 }} tickLine={false}
              axisLine={{ stroke: "var(--border-1)" }} interval={0}
              angle={-18} textAnchor="end" height={54}
            />
            <YAxis tick={{ fontSize: 10 }} tickLine={false} axisLine={false} />
            <ReferenceLine y={0} stroke="var(--border-3)" />
            <Tooltip
              cursor={{ fill: "var(--accent-wash)" }}
              contentStyle={tooltipStyle}
              formatter={(_v: number, _n, p) => {
                const d = p.payload as {
                  signed: number; term: number | null; weight: number | null;
                  isTotal: boolean;
                };
                if (d.isTotal) {
                  return [fmtNumber(d.signed, 4), "running total"];
                }
                return [
                  `${d.signed >= 0 ? "+" : ""}${fmtNumber(d.signed, 4)}`
                  + `  (term ${fmtNumber(d.term, 3)} × weight ${d.weight})`,
                  "applied",
                ];
              }}
            />
            {/* The lift. Transparent, and excluded from the tooltip. */}
            <Bar dataKey="base" stackId="w" fill="transparent" isAnimationActive={false}
                 legendType="none" tooltipType="none" />
            <Bar dataKey="delta" stackId="w" radius={[3, 3, 0, 0]} isAnimationActive={false}>
              {rows.map((r, i) => (
                <Cell
                  key={i}
                  fill={
                    r.isTotal
                      ? "var(--rl)"
                      : r.signed >= 0 ? "var(--ok)" : "var(--bad)"
                  }
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="t-small leading-relaxed mt-1" style={{ color: "var(--text-3)" }}>
        <strong>Horizontal axis:</strong> each term in the formula, applied in
        order, ending in the final reward. <strong>Vertical axis:</strong> the
        running total as each term is added. Each bar's height is that term's
        contribution; green bars raise the score, red bars lower it, and the
        final sage bar is the result after every term has been applied —
        nothing here is inferred from the total backward, every bar is a
        recorded value.
      </p>
      {step.clipped && (
        <p className="t-small mt-1.5 leading-relaxed" style={{ color: "var(--warn)" }}>
          This interval hit the ±1 clip, so the final bar is the clipped value
          and the terms genuinely do not sum to it.
        </p>
      )}
    </Card>
  );
}

function ContributionChart({ step }: { step: RewardStep }) {
  const rows = step.contributions
    .filter((c) => c.contribution !== null)
    .map((c) => ({
      label: c.label,
      contribution: c.contribution as number,
      term: c.term_value,
      weight: c.weight,
    }));

  if (!rows.length) {
    return (
      <Card title="Contribution breakdown">
        <Empty title="No component values recorded for this interval" />
      </Card>
    );
  }

  return (
    <Card
      title={`Contribution breakdown — step ${step.step}`}
      subtitle="Each bar is term value × weight. Positive adds to the score, negative subtracts."
      right={
        step.reconciles ? (
          <Pill color="var(--ok)" title="Components sum to the recorded reward.">
            reconciled
          </Pill>
        ) : (
          <Pill color="var(--warn)" title="See the reconciliation panel.">
            check
          </Pill>
        )
      }
    >
      <div style={{ height: 232 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={rows} layout="vertical"
                    margin={{ top: 4, right: 40, bottom: 4, left: 8 }}>
            <CartesianGrid stroke="var(--border-1)" strokeDasharray="2 5" horizontal={false} />
            <XAxis type="number" tick={{ fontSize: 10 }} tickLine={false}
                   axisLine={{ stroke: "var(--border-1)" }} />
            <YAxis type="category" dataKey="label" width={132}
                   tick={{ fontSize: 10.5 }} tickLine={false} axisLine={false} />
            <ReferenceLine x={0} stroke="var(--border-3)" />
            <Tooltip
              cursor={{ fill: "var(--hover-wash)" }}
              contentStyle={tooltipStyle}
              formatter={(v: number, _n, p) => {
                const d = p.payload as { term: number; weight: number };
                return [
                  `${fmtNumber(v, 4)}  (term ${fmtNumber(d.term, 3)} × weight ${d.weight})`,
                  "contribution",
                ];
              }}
            />
            <Bar dataKey="contribution" radius={[0, 4, 4, 0]} isAnimationActive={false}>
              {rows.map((r, i) => (
                <Cell key={i} fill={r.contribution >= 0 ? "var(--ok)" : "var(--bad)"} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="t-small leading-relaxed mb-2" style={{ color: "var(--text-3)" }}>
        <strong>Horizontal axis:</strong> the contribution itself — term value
        × weight, in the score's own units. <strong>Vertical axis:</strong>{" "}
        one row per term. A bar to the right of the zero line added to the
        score; a bar to the left subtracted from it. This is the same five
        numbers as the waterfall above, read side by side instead of as steps.
      </p>
      <div className="flex items-center justify-between mt-2 pt-2 border-t text-[11.5px]"
           style={{ borderColor: "var(--border-1)" }}>
        <span style={{ color: "var(--text-3)" }}>Sum of contributions</span>
        <span className="num font-semibold">
          {fmtNumber(step.sum_of_contributions, 6)}
        </span>
      </div>
      <div className="flex items-center justify-between text-[11.5px]">
        <span style={{ color: "var(--text-3)" }}>Recorded reward</span>
        <span className="num font-semibold">{fmtNumber(step.reward, 6)}</span>
      </div>
      {step.clipped && (
        <p className="text-[11px] mt-2 leading-relaxed" style={{ color: "var(--warn)" }}>
          This interval hit the ±1 clip, so the components genuinely do not sum
          to the final value.
        </p>
      )}
    </Card>
  );
}

function StepInspector({
  step, steps, onSelect,
}: {
  step: RewardStep;
  steps: RewardStep[];
  onSelect: (s: number) => void;
}) {
  const band = step.band ? BAND_STYLE[step.band] : null;
  return (
    <Card
      title="Inspect an interval"
      right={
        <select
          value={step.step}
          onChange={(e) => onSelect(Number(e.target.value))}
          className={inputClass}
          style={inputStyle}
          aria-label="Choose a decision step"
        >
          {steps.map((s) => (
            <option key={s.step} value={s.step}>step {s.step}</option>
          ))}
        </select>
      }
    >
      <div className="flex items-baseline gap-2 mb-1">
        <span className="text-3xl font-semibold num"
              style={{ color: band ? `var(--${band.tone})` : "var(--text-1)" }}>
          {fmtNumber(step.reward, 4)}
        </span>
        <span className="text-[11px]" style={{ color: "var(--text-3)" }}>reward</span>
      </div>
      {band && (
        <p className="text-[12px] leading-relaxed mb-3" style={{ color: "var(--text-2)" }}>
          <span className="font-medium" style={{ color: `var(--${band.tone})` }}>
            {band.label}.
          </span>{" "}
          {band.text}
        </p>
      )}

      <dl className="space-y-1.5 text-[12px]">
        <Row k="Time" v={fmtTime(step.t)} />
        <Row k="Variant" v={step.variant || "—"} />
        <Row k="Chosen path" v={step.path || "—"} />
        <Row k="Idle timeout"
             v={step.idle_timeout !== null ? `${step.idle_timeout} s` : "—"} />
        <Row k="State (bins)" v={<span className="mono">{step.state || "—"}</span>} />
        <Row
          k="Policy"
          v={
            step.exploratory === null ? "n/a (no agent)"
              : step.exploratory ? "exploratory" : "greedy"
          }
        />
        {step.epsilon !== null && <Row k="ε" v={fmtNumber(step.epsilon, 3)} />}
        {step.q_before !== null && step.q_after !== null && (
          <Row k="Q before → after"
               v={`${fmtNumber(step.q_before, 4)} → ${fmtNumber(step.q_after, 4)}`} />
        )}
      </dl>

      <p className="text-[10.5px] mt-3 leading-relaxed" style={{ color: "var(--text-4)" }}>
        The reward is measured <em>after</em> the action was applied, over the
        interval that followed it. It is not a prediction, and it does not by
        itself explain why the action was chosen.
      </p>
    </Card>
  );
}

function ReconciliationCard({ data }: { data: RewardExplain }) {
  const r = data.reconciliation;
  const ok = r?.ok ?? false;
  return (
    <Card
      title="Does the breakdown add up?"
      right={
        <Pill color={ok ? "var(--ok)" : "var(--warn)"}>
          {ok ? "verified" : "check"}
        </Pill>
      }
    >
      <div className="grid grid-cols-2 gap-2 mb-3">
        <Stat label="Intervals checked" value={String(r?.checked ?? 0)} />
        <Stat label="Reconciled" value={String(r?.reconciled ?? 0)}
              tone={ok ? "ok" : undefined} />
        <Stat label="Mismatched" value={String(r?.unreconciled ?? 0)}
              tone={(r?.unreconciled ?? 0) > 0 ? "bad" : undefined} />
        <Stat label="Hit the ±1 clip" value={String(r?.clipped_rows ?? 0)} />
      </div>
      <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-3)" }}>
        {r?.note}
      </p>
      {r?.worst_residual !== undefined && (
        <p className="text-[11px] mt-2 num" style={{ color: "var(--text-4)" }}>
          Worst difference: {r.worst_residual.toExponential(2)} (tolerance{" "}
          {r.tolerance?.toExponential(1)})
        </p>
      )}
    </Card>
  );
}

function FormulaCard({
  model, step,
}: {
  model: RewardExplain["model"];
  /** The currently-inspected step, if any, so the worked example below the
      formula can use real recorded numbers instead of invented ones. */
  step?: RewardStep | null;
}) {
  return (
    <Card
      title="What goes into the score"
      subtitle={model.note}
      right={<SourceBadge source={model.source as never} />}
    >
      <div className="space-y-2.5">
        {model.terms.map((term) => (
          <div
            key={term.key}
            className="rounded-lg border px-3 py-2.5"
            style={{ borderColor: "var(--border-1)", background: "var(--surface-2)" }}
          >
            <div className="flex items-center justify-between gap-3 mb-1">
              <div className="flex items-center gap-2 min-w-0">
                <span
                  className="w-1.5 h-5 rounded-full shrink-0"
                  style={{ background: term.sign > 0 ? "var(--ok)" : "var(--bad)" }}
                  aria-hidden="true"
                />
                <span className="text-[12.5px] font-medium truncate">{term.label}</span>
              </div>
              <span className="text-[11px] num shrink-0"
                    style={{ color: term.sign > 0 ? "var(--ok)" : "var(--bad)" }}>
                {term.sign > 0 ? "+" : "−"}{term.weight} × value
              </span>
            </div>
            <p className="text-[11.5px] leading-relaxed" style={{ color: "var(--text-2)" }}>
              {term.plain}
            </p>
            <div className="mt-2 pt-2 border-t text-[11px] leading-relaxed"
                 style={{ borderColor: "var(--hairline)", color: "var(--text-3)" }}>
              <p className="mb-1"><strong style={{ color: "var(--text-2)" }}>Measured:</strong> {term.measured}</p>
              <p><strong style={{ color: "var(--text-2)" }}>Scaled:</strong> {term.normalisation}</p>
            </div>
          </div>
        ))}
      </div>

      {/* The formula itself, not behind a click: it is what this card is
          titled for, and a formula nobody opens is not documentation. */}
      <div className="mt-3 pt-3 border-t" style={{ borderColor: "var(--hairline)" }}>
        <p className="t-label mb-1.5" style={{ color: "var(--text-4)" }}>
          The formula, exactly as implemented
        </p>
        <code className="block mono text-[11px] rounded-lg border px-3 py-2 mb-2 break-words"
              style={{ background: "var(--surface-0)", borderColor: "var(--border-1)" }}>
          reward = {model.expression}
        </code>
        <p className="text-[11.5px] leading-relaxed" style={{ color: "var(--text-3)" }}>
          Every term is first clamped to 0–1, then weighted and summed, and the
          total is clamped to [{model.clip_range[0]}, {model.clip_range[1]}].
        </p>
      </div>

      {step && <WorkedExample step={step} />}

      <div className="mt-3 pt-3 border-t" style={{ borderColor: "var(--hairline)" }}>
        <p className="t-label mb-1.5" style={{ color: "var(--text-4)" }}>
          Normalisation references
        </p>
        <ul className="list-disc pl-5 space-y-0.5 mb-4 text-[11px] leading-relaxed"
            style={{ color: "var(--text-3)" }}>
          {Object.entries(model.normalisers).map(([k, v]) => (
            <li key={k}><span className="mono">{k}</span> = {String(v)}</li>
          ))}
        </ul>
        <p className="t-label mb-1.5" style={{ color: "var(--text-4)" }}>
          Limitations you should know about
        </p>
        <ul className="list-disc pl-5 space-y-1 text-[11px] leading-relaxed"
            style={{ color: "var(--text-3)" }}>
          {model.caveats.map((c, i) => <li key={i}>{c}</li>)}
        </ul>
      </div>
    </Card>
  );
}

/** A worked example, filled in with the actually-recorded numbers for the
    step currently selected above — so the formula is not just an abstract
    expression, it is shown producing the exact score already on screen. */
function WorkedExample({ step }: { step: RewardStep }) {
  const rows = step.contributions.filter((c) => c.contribution !== null);
  if (!rows.length) return null;

  return (
    <div
      className="mt-3 rounded-lg border px-3 py-2.5"
      style={{ borderColor: "var(--rl-ring)", background: "var(--rl-dim)" }}
    >
      <p className="t-label mb-1.5" style={{ color: "var(--rl)" }}>
        Worked example — step {step.step}, the interval shown above
      </p>
      <div className="mono text-[11px] leading-relaxed" style={{ color: "var(--text-2)" }}>
        {rows.map((c, i) => (
          <div key={c.key}>
            {i === 0 ? "reward = " : "       "}
            {c.sign > 0 ? "" : "- "}
            {fmtNumber(Math.abs(c.weight ?? 0), 2)} × {fmtNumber(c.term_value, 3)}
            <span style={{ color: "var(--text-4)" }}> ({c.label.toLowerCase()})</span>
          </div>
        ))}
        <div className="pt-1 mt-1 border-t" style={{ borderColor: "var(--hairline)" }}>
          {"     = "}
          {rows.map((c, i) => (
            <span key={c.key}>
              {i > 0 && (c.contribution ?? 0) >= 0 ? " + " : i > 0 ? " - " : ""}
              {fmtNumber(Math.abs(c.contribution ?? 0), 4)}
            </span>
          ))}
          {" = "}
          <strong style={{ color: step.reward != null && step.reward >= 0 ? "var(--ok)" : "var(--bad)" }}>
            {fmtNumber(step.reward, 4)}
          </strong>
          {step.clipped && (
            <span style={{ color: "var(--warn)" }}> (clamped to the [-1, 1] range)</span>
          )}
        </div>
      </div>
      <p className="t-micro leading-relaxed mt-2" style={{ color: "var(--text-4)" }}>
        Every number here is read from this step's recorded row, not
        recomputed — see &ldquo;Does the breakdown add up?&rdquo; above for
        the exact check. Pick a different step from &ldquo;Inspect an
        interval&rdquo; to see this worked example update to match it.
      </p>
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <dt style={{ color: "var(--text-3)" }}>{k}</dt>
      <dd className="text-right num">{v}</dd>
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: string; tone?: "ok" | "bad" }) {
  return (
    <div className="rounded-lg px-2.5 py-1.5" style={{ background: "var(--surface-2)" }}>
      <p className="text-[10px]" style={{ color: "var(--text-4)" }}>{label}</p>
      <p className={cx("text-[15px] font-semibold num")}
         style={{ color: tone ? `var(--${tone})` : "var(--text-1)" }}>
        {value}
      </p>
    </div>
  );
}
