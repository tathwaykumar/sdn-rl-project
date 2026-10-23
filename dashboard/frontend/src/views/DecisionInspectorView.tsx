/** Decision Inspector.
 *
 * Shows what the agent chose and — separately — what the switches are actually
 * holding. Keeping those two apart is the point of this page: an agent
 * decision is an intention, and a switch-confirmed action is a fact. The UI
 * never collapses one into the other.
 *
 * It also avoids inventing causation. The agent produces a state, an action,
 * Q-values and a reward; it does not produce a reason. So this page reports
 * the policy mechanism (greedy or exploratory) and the values behind it, and
 * says explicitly that the reward is measured after the fact.
 */
import { useMemo, useState } from "react";
import { api } from "../api/client";
import { useAsync } from "../hooks/useLive";
import type { Decision, DecodedState, LiveSnapshot, Verification } from "../api/types";
import {
  Card, Details, Empty, ErrorBox, Pill, Skeleton, SourceBadge, StatusDot,
  cx, fmtNumber, fmtTime,
} from "../components/ui";

const CONFIRM: Record<string, { tone: "ok" | "warn" | "bad" | "idle"; label: string; text: string }> = {
  confirmed: {
    tone: "ok", label: "Confirmed by the switches",
    text: "Rules for the chosen path were observed in a flow-table dump taken after this decision.",
  },
  awaiting_confirmation: {
    tone: "warn", label: "Awaiting switch confirmation",
    text: "The newest flow dump is older than this decision, so the switches have not been asked yet. Dumps are taken every 5 seconds.",
  },
  not_observed: {
    tone: "bad", label: "Not observed on the switches",
    text: "A dump taken after this decision contained no rules for the chosen path.",
  },
  unverified: {
    tone: "idle", label: "Cannot verify",
    text: "No flow-table dump is available for this run yet.",
  },
};

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
  const verification = useAsync<Verification>(
    () => api.verification(runId),
    [runId],
    runId ? undefined : 4000,
  );
  const [selected, setSelected] = useState<number | null>(null);

  const live = !runId ? snapshot?.decisions : undefined;
  const rows: Decision[] = (live?.length ? live : decisions.data?.decisions) ?? [];
  const states = decisions.data?.states ?? {};
  const verify =
    (!runId ? snapshot?.verification : undefined) ?? verification.data ?? undefined;

  const current = useMemo(() => {
    if (!rows.length) return null;
    if (selected === null) return rows[rows.length - 1];
    return rows.find((r) => r.step === selected) ?? rows[rows.length - 1];
  }, [rows, selected]);

  const isLatest = current !== null && rows.length > 0 &&
    current.step === rows[rows.length - 1].step;

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
      <Card title="Intention versus fact">
        <p className="text-[12.5px] leading-relaxed text-balance"
           style={{ color: "var(--text-2)" }}>
          An <strong>agent decision</strong> is what the policy selected for the
          next interval. A <strong>switch-confirmed action</strong> is a rule
          actually present in a flow table. They are not the same thing, and
          they can disagree — briefly, while rules are being installed, or
          persistently if something went wrong. This page shows both and never
          treats one as evidence of the other.
        </p>
      </Card>

      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,420px)_minmax(0,1fr)] gap-4">
        {current && (
          <DecisionCard
            decision={current}
            decoded={states[current.state]}
            verify={isLatest ? verify : undefined}
            isLatest={isLatest}
            runId={runId}
          />
        )}

        <Card
          title="Decision history"
          subtitle={`${rows.length} intervals · click a row to inspect it`}
          right={<SourceBadge source={decisions.data?.source as never} />}
          bodyClassName="p-0"
        >
          <div className="overflow-auto" style={{ maxHeight: 520 }}>
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
                        background: active ? "var(--accent-dim)" : undefined,
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
    </div>
  );
}

function DecisionCard({
  decision, decoded, verify, isLatest, runId,
}: {
  decision: Decision;
  decoded?: DecodedState;
  verify?: Verification;
  isLatest: boolean;
  runId?: string;
}) {
  const status = verify?.status ? CONFIRM[verify.status] : null;

  return (
    <div className="space-y-4">
      <Card
        title={`Decision at step ${decision.step}`}
        subtitle={runId ? `Run ${runId}` : "Live experiment"}
        right={<Pill color="var(--rl)">variant {decision.variant || "?"}</Pill>}
      >
        <div className="grid grid-cols-2 gap-3 mb-3">
          <Field label="Selected route" value={decision.path ? `Path ${decision.path}` : "—"} />
          <Field
            label="Selected idle timeout"
            value={decision.idle_timeout !== null ? `${decision.idle_timeout} s` : "—"}
          />
          <Field
            label="Reward for the interval"
            value={fmtNumber(decision.reward, 4)}
            tone={(decision.reward ?? 0) >= 0 ? "ok" : "bad"}
          />
          <Field label="Timestamp" value={fmtTime(decision.t)} />
        </div>

        <div>
          <p className="text-[11px] mb-1" style={{ color: "var(--text-4)" }}>
            What the agent could choose from
          </p>
          <div className="flex flex-wrap gap-1.5">
            {(decision.usable_paths.length ? decision.usable_paths : ["—"]).map((p) => (
              <Pill
                key={p}
                color={p === decision.path ? "var(--accent)" : undefined}
                title={p === decision.path ? "selected" : "available but not selected"}
              >
                Path {p}{p === decision.path ? " ✓" : ""}
              </Pill>
            ))}
          </div>
        </div>
      </Card>

      <Card
        title="Switch confirmation"
        right={
          isLatest ? undefined : (
            <Pill title="Confirmation is computed for the most recent decision only.">
              latest only
            </Pill>
          )
        }
      >
        {!isLatest ? (
          <p className="text-[11.5px] leading-relaxed" style={{ color: "var(--text-3)" }}>
            Switch confirmation is evaluated against the newest flow-table dump,
            so it is reported for the most recent decision. Select the latest
            step to see it.
          </p>
        ) : (
          <>
            <div className="flex items-center gap-2 mb-1.5">
              <StatusDot
                tone={status?.tone ?? "idle"}
                pulse={verify?.status === "awaiting_confirmation"}
              />
              <span className="text-[12.5px] font-medium">
                {status?.label ?? "Unknown"}
              </span>
            </div>
            <p className="text-[11.5px] leading-relaxed mb-2" style={{ color: "var(--text-2)" }}>
              {status?.text}
            </p>
            {verify?.detail && (
              <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-3)" }}>
                {verify.detail}
              </p>
            )}
            {verify?.installed?.rule_counts && (
              <div className="mt-2 pt-2 border-t" style={{ borderColor: "var(--border-1)" }}>
                <p className="text-[11px] mb-1" style={{ color: "var(--text-4)" }}>
                  Rules currently installed at the edge switches
                </p>
                <div className="flex gap-3 text-[12px] num">
                  <span>Path A: <strong>{verify.installed.rule_counts.A ?? 0}</strong></span>
                  <span>Path B: <strong>{verify.installed.rule_counts.B ?? 0}</strong></span>
                </div>
              </div>
            )}
          </>
        )}
      </Card>

      <Card title="Observed state">
        {decoded?.decoded ? (
          <>
            <p className="text-[11px] mb-2" style={{ color: "var(--text-4)" }}>
              The agent sees the network as four coarse bins, not raw numbers.
              Encoded as <span className="mono">{decoded.raw}</span>.
            </p>
            <div className="grid grid-cols-2 gap-2">
              <Bin label="Path A utilisation" value={decoded.util_a?.label} />
              <Bin label="Path B utilisation" value={decoded.util_b?.label} />
              <Bin label="Flow-table occupancy" value={decoded.occupancy?.label} />
              <Bin label="Table-miss rate" value={decoded.miss_rate?.label} />
            </div>
          </>
        ) : (
          <p className="text-[11.5px]" style={{ color: "var(--text-3)" }}>
            State <span className="mono">{decision.state || "—"}</span> could not
            be decoded.
          </p>
        )}

        <Details summary="How the action was selected">
          {decision.exploratory === null ? (
            <p>
              This variant has no learning agent. It follows a fixed rule
              (shortest available path by hop count) and records a state and
              reward only so it can be compared on equal terms. Q-values and ε
              do not exist for it.
            </p>
          ) : decision.exploratory ? (
            <>
              <p className="mb-1">
                <strong>Exploratory step.</strong> ε-greedy selected a random
                action instead of the current best one
                {decision.epsilon !== null && <> (ε = {fmtNumber(decision.epsilon, 3)})</>}.
              </p>
              {decision.greedy_action && (
                <p>
                  It overrode the greedy choice{" "}
                  <span className="mono">{decision.greedy_action}</span>.
                </p>
              )}
            </>
          ) : (
            <>
              <p className="mb-1">
                <strong>Greedy step.</strong> The action with the highest
                Q-value among those available in this state was taken
                {decision.q_after !== null && <> (Q = {fmtNumber(decision.q_after, 4)})</>}.
              </p>
              {decision.epsilon !== null && (
                <p>Exploration rate at this step: ε = {fmtNumber(decision.epsilon, 3)}.</p>
              )}
            </>
          )}
          <p className="mt-2" style={{ color: "var(--text-3)" }}>
            The reward shown above was measured over the interval that
            <em> followed</em> this action. It is a consequence, not the reason
            the action was chosen.
          </p>
        </Details>
      </Card>
    </div>
  );
}

function Field({
  label, value, tone,
}: {
  label: string;
  value: React.ReactNode;
  tone?: "ok" | "bad";
}) {
  return (
    <div>
      <p className="text-[10.5px]" style={{ color: "var(--text-4)" }}>{label}</p>
      <p className="text-[14px] font-semibold num"
         style={{ color: tone ? `var(--${tone})` : "var(--text-1)" }}>
        {value}
      </p>
    </div>
  );
}

function Bin({ label, value }: { label: string; value?: string }) {
  return (
    <div className="rounded-lg px-2.5 py-1.5" style={{ background: "var(--surface-2)" }}>
      <p className="text-[10px]" style={{ color: "var(--text-4)" }}>{label}</p>
      <p className="text-[11.5px]">{value ?? "—"}</p>
    </div>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th className={cx(
      "px-3 py-2 font-medium text-[9.5px] uppercase tracking-wider whitespace-nowrap",
      right ? "text-right" : "text-left",
    )}>
      {children}
    </th>
  );
}
