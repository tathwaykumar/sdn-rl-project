/** The Decision Journey: Observe → Choose Action → Apply → Measure → Reward →
 * Learn → Next — for one specific, actually-recorded decision.
 *
 * Every number on this page comes from decisions.csv or the reward/
 * verification endpoints built on top of it. Nothing is simulated to fill a
 * stage: where a run predates the Q-value instrumentation (the whole 60-run
 * matrix, and variant A always, since it has no agent), the "Choose Action"
 * stage says so explicitly instead of inventing alternatives.
 *
 * Two things this component is careful never to do:
 *   - Claim a switch-confirmed route before a flow dump taken *after* this
 *     decision has actually shown it (see useStepVerification).
 *   - Claim the agent "chose X because of metric Y" beyond what greedy/
 *     exploratory + the Q-value row actually support. The reward is reported
 *     as a *consequence*, measured after the action, never as a cause.
 */
import type { ReactNode } from "react";
import {
  Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api } from "../api/client";
import { useAsync } from "../hooks/useLive";
import type {
  Decision, DecodedState, QValueRow, RewardStep, StepVerification,
} from "../api/types";
import {
  Card, ChartLegend, Pill, StatusDot, fmtNumber, tooltipStyle,
} from "../components/ui";

const STAGES = [
  { id: "observe", label: "Observe", icon: "👁" },
  { id: "action", label: "Choose Action", icon: "🎯" },
  { id: "apply", label: "Apply Decision", icon: "⚡" },
  { id: "outcome", label: "Measure Outcome", icon: "📊" },
  { id: "reward", label: "Calculate Reward", icon: "⚖" },
  { id: "learn", label: "Update Learning", icon: "🧠" },
  { id: "next", label: "Next Decision", icon: "→" },
] as const;

const CONFIRM: Record<string, { tone: "ok" | "warn" | "bad" | "idle"; label: string }> = {
  confirmed: { tone: "ok", label: "Confirmed by switches" },
  not_observed: { tone: "bad", label: "Not observed on switches" },
  no_later_dump: { tone: "warn", label: "No dump taken yet" },
  unverified: { tone: "idle", label: "Cannot verify" },
};

export function DecisionJourney({
  decision, previousDecision, nextDecision, decoded, rewardStep, runId,
  agentConfig,
}: {
  decision: Decision;
  previousDecision: Decision | null;
  nextDecision: Decision | null;
  decoded?: DecodedState;
  rewardStep?: RewardStep;
  runId?: string;
  agentConfig?: { alpha?: number; gamma?: number } | null;
}) {
  const verification = useAsync<StepVerification>(
    () => api.stepVerification(decision.step, runId),
    [decision.step, runId],
  );

  const hasAgent = decision.exploratory !== null;
  const qValues: QValueRow | null | undefined = rewardStep?.q_values ?? decision.q_values;

  return (
    <div className="space-y-3">
      <p className="t-small leading-relaxed px-1" style={{ color: "var(--text-3)" }}>
        Below is the complete, step-by-step record of <strong>one specific
        decision</strong> (step {decision.step}) — the same 7-stage cycle
        summarised on the Overview page, but every number here is the real
        value logged for this exact moment in this exact run, not an
        illustration. Read it left-to-right, top-to-bottom: the agent first
        looks at the network (1), picks an action (2), the controller tries
        to carry that out on the real switches (3), the network reports what
        actually happened (4), that outcome is scored (5), the agent's
        internal value table is nudged accordingly (6), and the cycle
        continues (7).
      </p>
      <JourneyRail current="action" />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
        <StageObserve decoded={decoded} decision={decision} />
        <StageAction decision={decision} qValues={qValues} hasAgent={hasAgent} />
        <StageApply verification={verification.data} loading={verification.loading}
                   chosenPath={decision.path} />
        <StageOutcome rewardStep={rewardStep} />
        <StageReward rewardStep={rewardStep} className="lg:col-span-2" />
        <StageLearn decision={decision} agentConfig={agentConfig} hasAgent={hasAgent} />
        <StageNext
          nextDecision={nextDecision}
          currentDecision={decision}
          previousDecision={previousDecision}
        />
      </div>
    </div>
  );
}

/** A compact progress rail across the top, purely orientational. */
function JourneyRail({ current }: { current: string }) {
  return (
    <div className="panel px-3 py-2.5 overflow-x-auto no-sb">
      <div className="flex items-center gap-1 min-w-max">
        {STAGES.map((s, i) => (
          <div key={s.id} className="flex items-center gap-1">
            <span
              className="flex items-center gap-1.5 px-2 py-1 rounded-lg t-small"
              style={{
                color: s.id === current ? "var(--rl)" : "var(--text-3)",
                background: s.id === current ? "var(--rl-dim)" : "transparent",
              }}
            >
              <span aria-hidden="true">{s.icon}</span>
              <span className="hidden sm:inline">{s.label}</span>
            </span>
            {i < STAGES.length - 1 && (
              <span style={{ color: "var(--text-4)" }} aria-hidden="true">→</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function StageCard({
  n, title, tone, children, className,
}: {
  n: number;
  title: string;
  tone: "net" | "rl";
  children: ReactNode;
  className?: string;
}) {
  return (
    <Card
      accent={tone}
      className={className}
      title={
        <span className="flex items-center gap-2">
          <span
            className="w-5 h-5 rounded-full flex items-center justify-center t-micro font-bold shrink-0"
            style={{
              background: tone === "rl" ? "var(--rl-dim)" : "var(--accent-dim)",
              color: tone === "rl" ? "var(--rl)" : "var(--accent)",
            }}
          >
            {n}
          </span>
          {title}
        </span>
      }
    >
      {children}
    </Card>
  );
}

// ---------------------------------------------------------------- 1. Observe
function StageObserve({ decoded, decision }: { decoded?: DecodedState; decision: Decision }) {
  return (
    <StageCard n={1} title="Observe" tone="net">
      {decoded?.decoded ? (
        <>
          <p className="t-small mb-2" style={{ color: "var(--text-2)" }}>
            Before deciding, the agent reads the network as four coarse bins —
            not raw numbers, to keep the state space small enough to learn.
          </p>
          <div className="grid grid-cols-2 gap-1.5">
            <Bin label="Path A utilisation" value={decoded.util_a?.label} />
            <Bin label="Path B utilisation" value={decoded.util_b?.label} />
            <Bin label="Flow-table occupancy" value={decoded.occupancy?.label} />
            <Bin label="Table-miss rate" value={decoded.miss_rate?.label} />
          </div>
          <div className="mt-2 pt-2 border-t t-micro leading-relaxed"
               style={{ borderColor: "var(--hairline)", color: "var(--text-4)" }}>
            <p>
              Encoded state key: <span className="mono">{decoded.raw}</span>
            </p>
            <p className="mt-1">
              This exact 4-digit key is how the agent's Q-table is indexed —
              every state with this key shares one row of learned values, no
              matter which run or step produced it.
            </p>
          </div>
        </>
      ) : (
        <p className="t-small" style={{ color: "var(--text-3)" }}>
          State <span className="mono">{decision.state || "—"}</span> could not be decoded.
        </p>
      )}
    </StageCard>
  );
}

function Bin({ label, value }: { label: string; value?: string }) {
  return (
    <div className="rounded-lg px-2 py-1.5" style={{ background: "var(--surface-2)" }}>
      <p className="t-micro" style={{ color: "var(--text-4)" }}>{label}</p>
      <p className="t-small">{value ?? "—"}</p>
    </div>
  );
}

// ------------------------------------------------------------ 2. Choose Action
function StageAction({
  decision, qValues, hasAgent,
}: {
  decision: Decision;
  qValues: QValueRow | null | undefined;
  hasAgent: boolean;
}) {
  if (!hasAgent) {
    return (
      <StageCard n={2} title="Choose Action" tone="rl">
        <p className="t-small" style={{ color: "var(--text-2)" }}>
          This variant has no learning agent — it always takes the shortest
          available path by hop count. No Q-values exist to show.
        </p>
      </StageCard>
    );
  }

  const chosenKey = `path=${decision.path},idle=${decision.idle_timeout}`;
  const rows = qValues
    ? qValues.allowed.map((key) => ({
        key,
        label: key.replace("path=", "").replace(",idle=", " · "),
        value: qValues.values[key] ?? 0,
        chosen: key === chosenKey || key === decision.action,
        greedy: key === decision.greedy_action,
      }))
    : [];

  return (
    <StageCard n={2} title="Choose Action" tone="rl">
      <div className="flex items-center gap-2 mb-2">
        <span
          className="px-2 py-0.5 rounded t-small font-medium"
          style={{
            background: decision.exploratory ? "var(--warn-dim)" : "var(--ok-dim)",
            color: decision.exploratory ? "var(--warn)" : "var(--ok)",
          }}
        >
          {decision.exploratory ? "Exploratory (ε-greedy)" : "Greedy (best known)"}
        </span>
        {decision.epsilon !== null && (
          <span className="t-micro num" style={{ color: "var(--text-4)" }}>
            ε = {fmtNumber(decision.epsilon, 3)}
          </span>
        )}
      </div>

      <p className="t-small mb-2" style={{ color: "var(--text-2)" }}>
        Selected: <strong>path {decision.path}</strong>
        {decision.idle_timeout !== null && <>, idle timeout <strong>{decision.idle_timeout}s</strong></>}.
        {decision.exploratory && decision.greedy_action && (
          <> This overrode the greedy choice <span className="mono">{decision.greedy_action}</span>.</>
        )}
      </p>

      {rows.length > 0 ? (
        <div style={{ height: Math.max(90, rows.length * 24) }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={rows} layout="vertical" margin={{ left: 4, right: 36, top: 2, bottom: 2 }}>
              <XAxis type="number" hide />
              <YAxis type="category" dataKey="label" width={88}
                     tick={{ fontSize: 10 }} tickLine={false} axisLine={false} />
              <Tooltip
                contentStyle={tooltipStyle}
                formatter={(v: number) => [fmtNumber(v, 4), "Q-value"]}
              />
              <Bar dataKey="value" radius={[0, 4, 4, 0]} isAnimationActive={false}>
                {rows.map((r, i) => (
                  <Cell key={i}
                        fill={r.chosen ? "var(--rl)" : r.greedy ? "var(--accent)" : "var(--surface-4)"} />
                ))}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </div>
      ) : (
        <p className="t-micro leading-relaxed" style={{ color: "var(--text-4)" }}>
          Q-values for the alternative actions were not recorded for this run
          (it predates the instrumentation that logs the full row before each
          decision). The before/after value of the chosen action is still
          available in the Update Learning stage below.
        </p>
      )}
      {rows.length > 0 && (
        <ChartLegend
          className="mt-1"
          items={[
            {
              label: "chosen", color: "var(--rl)", shape: "band",
              hint: "The action the controller actually recorded taking.",
            },
            {
              label: "greedy, if different", color: "var(--accent)", shape: "band",
              hint: "The highest-valued allowed action — what a non-exploring step would have taken.",
            },
            {
              label: "other allowed actions", color: "var(--surface-5)", shape: "band",
              hint: "Available this interval but not selected.",
            },
          ]}
          note="bar length is the Q-value before this decision"
        />
      )}
      {rows.length > 0 && (
        <p className="t-micro leading-relaxed mt-1.5" style={{ color: "var(--text-4)" }}>
          <strong>Vertical axis:</strong> every action allowed from this state.{" "}
          <strong>Bar length:</strong> its Q-value — the agent's current estimate
          of that action&rsquo;s long-run value, before this decision updates it.
        </p>
      )}
    </StageCard>
  );
}

/** Minimal h1→s1→(s2→s4 | s3→s5→s4)→h3 sketch. The agent's chosen path is an
 * intention (dashed sage); confirmed is a fact (solid emerald), drawn only
 * when switch evidence actually says so. Never the same stroke style. */
function MiniPathDiagram({ chosenPath, confirmedPath }: { chosenPath: string; confirmedPath?: string | null }) {
  const isA = (p: string) => p === "A";
  const chosenA = isA(chosenPath), chosenB = !chosenA;
  const confirmedA = confirmedPath === "A", confirmedB = confirmedPath === "B";
  const edge = (on: boolean, confirmed: boolean) => ({
    stroke: confirmed ? "var(--accent)" : on ? "var(--rl)" : "var(--border-2)",
    strokeWidth: confirmed || on ? 2.5 : 1.5,
    strokeDasharray: on && !confirmed ? "4 3" : undefined,
  });
  return (
    <svg viewBox="0 0 220 70" className="w-full" style={{ maxHeight: 70 }} aria-label="Selected path">
      <text x="2" y="38" fontSize="9" fill="var(--text-3)">h1</text>
      <text x="200" y="12" fontSize="9" fill="var(--text-3)">h3</text>
      <circle cx="30" cy="35" r="3" fill="var(--text-4)" />
      <circle cx="190" cy="35" r="3" fill="var(--text-4)" />
      {/* path A: s1 -> s2 -> s4 (top) */}
      <path d="M30,35 L70,12 L150,12 L190,35" fill="none" {...edge(chosenA, confirmedA)} />
      {/* path B: s1 -> s3 -> s5 -> s4 (bottom) */}
      <path d="M30,35 L70,58 L150,58 L190,35" fill="none" {...edge(chosenB, confirmedB)} />
      <text x="95" y="8" fontSize="8" fill={chosenA ? "var(--rl)" : "var(--text-4)"}>A</text>
      <text x="95" y="68" fontSize="8" fill={chosenB ? "var(--rl)" : "var(--text-4)"}>B</text>
    </svg>
  );
}

// ----------------------------------------------------------- 3. Apply Decision
function StageApply({
  verification, loading, chosenPath,
}: {
  verification?: StepVerification | null;
  loading: boolean;
  chosenPath: string;
}) {
  const status = verification?.status ? CONFIRM[verification.status] : null;
  const confirmedPath = verification?.status === "confirmed" ? chosenPath : null;
  return (
    <StageCard n={3} title="Apply Decision" tone="net">
      <p className="t-small mb-2" style={{ color: "var(--text-2)" }}>
        The network has exactly two physical routes from h1 to h3: path A
        (top, the shorter 2-hop route) and path B (bottom, the longer but
        higher-capacity 3-hop route). The diagram below highlights whichever
        one this decision selected.
      </p>
      <MiniPathDiagram chosenPath={chosenPath} confirmedPath={confirmedPath} />
      <div className="flex gap-3 t-micro mb-2" style={{ color: "var(--text-4)" }}>
        <span><span style={{ color: "var(--rl)" }}>┄┄</span> agent's intention</span>
        <span><span style={{ color: "var(--accent)" }}>──</span> confirmed by switches</span>
      </div>
      <p className="t-micro leading-relaxed mb-2" style={{ color: "var(--text-4)" }}>
        A dashed line is what the agent <em>wanted</em> — only a claim until
        checked. A solid line means a flow-table dump taken on the real
        switches, after this decision, actually shows traffic routed that way.
        Until that dump exists, the choice is shown dashed even if it will
        later be confirmed.
      </p>
      {loading && !verification ? (
        <p className="t-small" style={{ color: "var(--text-4)" }}>Checking switches…</p>
      ) : (
        <>
          <div className="flex items-center gap-2 mb-1.5">
            <StatusDot tone={status?.tone ?? "idle"} pulse={verification?.status === "no_later_dump"} />
            <span className="t-small font-medium">{status?.label ?? "Unknown"}</span>
          </div>
          <p className="t-small leading-relaxed" style={{ color: "var(--text-2)" }}>
            {verification?.detail ?? "No verification data available."}
          </p>
          {verification?.rule_counts && (
            <div className="flex gap-3 mt-2 t-small num">
              <span>Path A: <strong>{verification.rule_counts.A ?? 0}</strong> rules</span>
              <span>Path B: <strong>{verification.rule_counts.B ?? 0}</strong> rules</span>
            </div>
          )}
          <p className="t-micro mt-2" style={{ color: "var(--text-4)" }}>
            This is a fact about the switches, checked against a flow-table
            dump taken after this specific decision — not an assumption from
            the agent's intention.
          </p>
        </>
      )}
    </StageCard>
  );
}

// ------------------------------------------------------------- 4. Measure Outcome
function StageOutcome({ rewardStep }: { rewardStep?: RewardStep }) {
  if (!rewardStep) {
    return (
      <StageCard n={4} title="Measure Outcome" tone="net">
        <p className="t-small" style={{ color: "var(--text-3)" }}>Not available for this step.</p>
      </StageCard>
    );
  }
  const items = rewardStep.contributions.filter((c) => c.term_value !== null);
  return (
    <StageCard n={4} title="Measure Outcome" tone="net">
      <p className="t-small mb-2" style={{ color: "var(--text-2)" }}>
        This is what actually happened in the interval right after the
        decision above — measured from switch counters, independent of what
        the agent intended. Each figure is scaled to a common 0–1 range so
        the five very different quantities below (bits/s, a percentage, a
        rule count...) can be combined by the reward formula next door; the
        scaling doesn't change what moved, only the units it's expressed in.
      </p>
      <div className="grid grid-cols-2 gap-1.5">
        {items.map((c) => (
          <div key={c.key} className="rounded-lg px-2 py-1.5" style={{ background: "var(--surface-2)" }}>
            <p className="t-micro truncate" style={{ color: "var(--text-4)" }}>{c.label}</p>
            <p className="t-small num">{fmtNumber(c.term_value, 3)}</p>
          </div>
        ))}
      </div>
      <p className="t-micro mt-2" style={{ color: "var(--text-4)" }}>
        Normalised values, not raw units — see Metrics &amp; Analytics for the
        measured throughput/latency/loss for this run in their actual units
        (Mbit/s, ms, etc).
      </p>
    </StageCard>
  );
}

// -------------------------------------------------------------- 5. Reward (waterfall)
function StageReward({ rewardStep, className }: { rewardStep?: RewardStep; className?: string }) {
  if (!rewardStep) {
    return (
      <StageCard n={5} title="Calculate Reward" tone="rl" className={className}>
        <p className="t-small" style={{ color: "var(--text-3)" }}>Not available for this step.</p>
      </StageCard>
    );
  }

  // Waterfall: each bar's "base" is invisible, "delta" is the visible segment.
  let running = 0;
  const bars = rewardStep.contributions
    .filter((c) => c.contribution !== null)
    .map((c) => {
      const v = c.contribution as number;
      const base = v >= 0 ? running : running + v;
      running += v;
      return { key: c.key, label: c.label, base, delta: Math.abs(v), signed: v, weight: c.weight, term: c.term_value };
    });
  const finalRow = { key: "total", label: "Total reward", base: 0, delta: Math.abs(running), signed: running, isTotal: true };

  return (
    <StageCard n={5} title="Calculate Reward" tone="rl" className={className}>
      <p className="t-small mb-2" style={{ color: "var(--text-2)" }}>
        Throughput adds to the score; congestion, loss, flow-table occupancy
        and controller load subtract from it. Each bar below is one term's
        weighted contribution.
      </p>
      <div style={{ height: 196 }}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={[...bars, finalRow]} margin={{ left: -10, right: 8, top: 4, bottom: 4 }}>
            <XAxis dataKey="label" tick={{ fontSize: 9 }} tickLine={false}
                   axisLine={{ stroke: "var(--border-1)" }} interval={0}
                   angle={-18} textAnchor="end" height={46} />
            <YAxis tick={{ fontSize: 10 }} tickLine={false} axisLine={false} width={36}
                   domain={[-1, 1]} />
            <Tooltip
              contentStyle={tooltipStyle}
              formatter={(_v: number, _n, p) => {
                const d = p.payload as typeof bars[number] & { isTotal?: boolean };
                return [fmtNumber(d.signed, 4), d.isTotal ? "final reward" : "contribution"];
              }}
            />
            <Bar dataKey="base" stackId="w" fill="transparent" isAnimationActive={false} />
            <Bar dataKey="delta" stackId="w" radius={[3, 3, 0, 0]} isAnimationActive={false}>
              {[...bars, finalRow].map((r, i) => (
                <Cell key={i}
                      fill={"isTotal" in r && r.isTotal ? "var(--rl)"
                        : r.signed >= 0 ? "var(--ok)" : "var(--bad)"} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="t-micro leading-relaxed" style={{ color: "var(--text-4)" }}>
        <strong>Horizontal axis:</strong> each term in the formula, applied in
        order, ending in the final reward. <strong>Vertical axis:</strong> the
        running total as each term is added — green raises it, red lowers it,
        and the last bar is the result.
      </p>
      <div className="flex items-center justify-between mt-1 pt-2 border-t t-small"
           style={{ borderColor: "var(--border-1)" }}>
        <span style={{ color: "var(--text-3)" }}>
          {rewardStep.band === "positive" ? "Net positive" : rewardStep.band === "negative" ? "Net negative" : "Near zero"}
        </span>
        <span className="num font-semibold"
              style={{ color: (rewardStep.reward ?? 0) >= 0 ? "var(--ok)" : "var(--bad)" }}>
          {fmtNumber(rewardStep.reward, 4)}
        </span>
      </div>
      {rewardStep.reconciles ? (
        <p className="t-micro mt-1 flex items-center gap-1" style={{ color: "var(--text-4)" }}>
          <Pill color="var(--ok)">verified</Pill> components sum to the recorded reward
        </p>
      ) : (
        <p className="t-micro mt-1" style={{ color: "var(--warn)" }}>
          {rewardStep.clipped
            ? "This interval hit the ±1 clip; components do not sum exactly, as expected."
            : "Components do not reconcile with the recorded reward for this step."}
        </p>
      )}
    </StageCard>
  );
}

// ------------------------------------------------------------ 6. Update Learning
function StageLearn({
  decision, agentConfig, hasAgent,
}: {
  decision: Decision;
  agentConfig?: { alpha?: number; gamma?: number } | null;
  hasAgent: boolean;
}) {
  if (!hasAgent) {
    return (
      <StageCard n={6} title="Update Learning" tone="rl">
        <p className="t-small" style={{ color: "var(--text-2)" }}>
          No agent, no Q-table, nothing to update.
        </p>
      </StageCard>
    );
  }
  const before = decision.q_before;
  const after = decision.q_after;
  const delta = before !== null && after !== null ? after - before : null;

  return (
    <StageCard n={6} title="Update Learning" tone="rl">
      <div className="flex items-center gap-4 mb-2">
        <div>
          <p className="t-micro" style={{ color: "var(--text-4)" }}>Q-value before</p>
          <p className="readout text-[17px]">{fmtNumber(before, 4)}</p>
        </div>
        <span style={{ color: "var(--text-4)" }} aria-hidden="true">→</span>
        <div>
          <p className="t-micro" style={{ color: "var(--text-4)" }}>Q-value after</p>
          <p className="readout text-[17px]" style={{ color: delta !== null && delta >= 0 ? "var(--ok)" : "var(--bad)" }}>
            {fmtNumber(after, 4)}
          </p>
        </div>
        {delta !== null && (
          <Pill color={delta >= 0 ? "var(--ok)" : "var(--bad)"}>
            {delta >= 0 ? "+" : ""}{fmtNumber(delta, 4)}
          </Pill>
        )}
      </div>
      <p className="t-small leading-relaxed" style={{ color: "var(--text-2)" }}>
        The value of the action taken in this state moved toward the reward
        actually received{decision.td_error !== null && (
          <> (TD error {fmtNumber(decision.td_error, 4)})</>
        )}. A positive error raises the value for next time this state is
        seen; a negative one lowers it.
      </p>
      <div className="mt-2 pt-2 border-t t-small leading-relaxed"
           style={{ borderColor: "var(--hairline)", color: "var(--text-2)" }}>
        <p className="t-label mb-1" style={{ color: "var(--text-4)" }}>
          Exact update rule
        </p>
        <code className="block mono text-[10.5px] rounded-lg border px-2.5 py-2 mb-1.5"
              style={{ background: "var(--surface-0)", borderColor: "var(--border-1)" }}>
          Q(s,a) ← Q(s,a) + α · (r + γ·max Q(s′,·) − Q(s,a))
        </code>
        <p className="t-micro" style={{ color: "var(--text-4)" }}>
          s = the state observed, a = the action taken, r = the reward just
          received, s′ = the state observed next, max Q(s′,·) = the best
          value this state&rsquo;s own next row currently holds. α and γ are
          fixed per run, not learned.
        </p>
        {agentConfig?.alpha !== undefined && before !== null && after !== null
          && decision.td_error !== null && (
          <p className="mono t-micro mt-2 pt-2 border-t" style={{ borderColor: "var(--hairline)", color: "var(--text-3)" }}>
            For this step: Q(s,a) = {fmtNumber(before, 4)} +{" "}
            {fmtNumber(agentConfig.alpha, 2)} × ({fmtNumber(decision.td_error, 4)}){" "}
            = {fmtNumber(after, 4)}
            <span className="block mt-0.5" style={{ color: "var(--text-4)" }}>
              (r + γ·max Q(s′,·) − Q(s,a)) was recorded directly as the
              TD error above; α (learning rate) = {fmtNumber(agentConfig.alpha, 3)},{" "}
              γ (discount) = {fmtNumber(agentConfig.gamma, 3)} for this run.
            </span>
          </p>
        )}
        <p className="mt-1.5">
          This updates only the one (state, action) cell that was just taken —
          not the whole table, and not the agent's future exploration beyond
          what this one value now implies.
        </p>
      </div>
    </StageCard>
  );
}

// --------------------------------------------------------------- 7. Next Decision
function StageNext({
  nextDecision, currentDecision, previousDecision,
}: {
  nextDecision: Decision | null;
  currentDecision: Decision;
  previousDecision: Decision | null;
}) {
  return (
    <StageCard n={7} title="Next Decision" tone="rl">
      {!nextDecision ? (
        <p className="t-small" style={{ color: "var(--text-3)" }}>
          This was the last recorded decision in this run.
        </p>
      ) : nextDecision.state === currentDecision.state ? (
        <p className="t-small leading-relaxed" style={{ color: "var(--text-2)" }}>
          The very next decision (step {nextDecision.step}) revisited the{" "}
          <strong>same observed state</strong>, now with the updated Q-value
          from this step already in the table. It chose{" "}
          <strong>path {nextDecision.path}</strong>
          {nextDecision.idle_timeout !== null && <>, idle {nextDecision.idle_timeout}s</>}.
        </p>
      ) : (
        <p className="t-small leading-relaxed" style={{ color: "var(--text-2)" }}>
          The next decision (step {nextDecision.step}) occurred in a{" "}
          <strong>different observed state</strong> (<span className="mono">{nextDecision.state}</span>{" "}
          vs <span className="mono">{currentDecision.state}</span>) — network
          conditions changed, so this update's effect cannot be observed yet.
          It will show up whenever state <span className="mono">{currentDecision.state}</span> recurs.
        </p>
      )}
      {previousDecision && (
        <p className="t-micro mt-2 pt-2 border-t" style={{ borderColor: "var(--border-1)", color: "var(--text-4)" }}>
          Previous decision (step {previousDecision.step}) chose path {previousDecision.path}.
        </p>
      )}
    </StageCard>
  );
}
