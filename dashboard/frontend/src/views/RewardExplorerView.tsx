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
  Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ReferenceLine,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../api/client";
import { useAsync } from "../hooks/useLive";
import type { RewardExplain, RewardStep } from "../api/types";
import {
  Card, Details, Empty, ErrorBox, Pill, Skeleton, SourceBadge, StatusDot,
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

  if (!data?.available || steps.length === 0) {
    return (
      <div className="space-y-4">
        <Card title="Reward Explorer" right={<SourceBadge source="unavailable" />}>
          <Empty
            title="No reward data yet"
            detail={
              data?.reason ??
              "The agent records a reward once per polling interval. Start an experiment, or open a completed run from History."
            }
          />
        </Card>
        {model && <FormulaCard model={model} />}
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <IntroCard model={model!} source={data.source} />

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_minmax(0,420px)] gap-4">
        <div className="space-y-4">
          <RewardHistory
            steps={steps}
            selectedStep={current?.step ?? null}
            onSelect={(s) => setSelected(s)}
          />
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

      <FormulaCard model={model!} />
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
      <div style={{ height: 210 }}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart
            data={chart}
            margin={{ top: 6, right: 10, bottom: 0, left: -14 }}
            onClick={(e) => {
              const point = e?.activePayload?.[0]?.payload as { step: number } | undefined;
              if (point) onSelect(point.step);
            }}
          >
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
              contentStyle={{ background: "var(--surface-2)", border: "1px solid var(--border-2)",
                              borderRadius: 8, fontSize: 11 }}
              labelFormatter={(v) => `Step ${v}`}
              formatter={(v: number) => [fmtNumber(v, 4), "reward"]}
            />
            <Line
              type="monotone" dataKey="reward" stroke="var(--rl)" strokeWidth={2}
              dot={false} isAnimationActive={false} activeDot={{ r: 4, fill: "var(--rl)" }}
            />
            {selectedStep !== null && (
              <ReferenceLine x={selectedStep} stroke="var(--accent)" strokeDasharray="3 3" />
            )}
          </LineChart>
        </ResponsiveContainer>
      </div>
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
      <div style={{ height: 190 }}>
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
              cursor={{ fill: "rgba(255,255,255,0.03)" }}
              contentStyle={{ background: "var(--surface-2)", border: "1px solid var(--border-2)",
                              borderRadius: 8, fontSize: 11 }}
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

function FormulaCard({ model }: { model: RewardExplain["model"] }) {
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
            <Details summary="How it is measured and scaled">
              <p className="mb-1"><strong>Measured:</strong> {term.measured}</p>
              <p><strong>Scaled:</strong> {term.normalisation}</p>
            </Details>
          </div>
        ))}
      </div>

      <Details summary="The formula, exactly as implemented">
        <code className="block mono text-[11px] rounded-lg border px-3 py-2 mb-2 break-words"
              style={{ background: "var(--surface-0)", borderColor: "var(--border-1)" }}>
          reward = {model.expression}
        </code>
        <p className="mb-2">
          Every term is first clamped to 0–1, then weighted and summed, and the
          total is clamped to [{model.clip_range[0]}, {model.clip_range[1]}].
        </p>
        <p className="mb-1"><strong>Normalisation references</strong></p>
        <ul className="list-disc pl-5 space-y-0.5">
          {Object.entries(model.normalisers).map(([k, v]) => (
            <li key={k}><span className="mono">{k}</span> = {String(v)}</li>
          ))}
        </ul>
      </Details>

      <Details summary="Limitations you should know about">
        <ul className="list-disc pl-5 space-y-1">
          {model.caveats.map((c, i) => <li key={i}>{c}</li>)}
        </ul>
      </Details>
    </Card>
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
