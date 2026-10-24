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
import { api } from "../api/client";
import { useAsync } from "../hooks/useLive";
import type { Decision, LiveSnapshot, RewardExplain } from "../api/types";
import {
  Card, Empty, ErrorBox, Pill, Skeleton, SourceBadge, cx,
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
