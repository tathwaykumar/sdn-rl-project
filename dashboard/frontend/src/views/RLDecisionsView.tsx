/** RL decision monitor.
 *
 * Shows what the agent decided and whether the switches actually reflect it.
 * Two rules this view follows strictly:
 *
 * 1. No invented explanations. The agent produces a state, an action, Q-values
 *    and a reward -- not a rationale. The "why" column states the policy
 *    mechanism (greedy or exploratory) and the Q-values behind it, and nothing
 *    more.
 * 2. Installation is a claim about the switches, so it is answered from the
 *    flow dumps. A decision stays "awaiting confirmation" until rules for it
 *    are observed.
 */
import { api } from "../api/client";
import { useAsync } from "../hooks/useLive";
import type { Decision, DecodedState, LiveSnapshot, Verification } from "../api/types";
import {
  Card, Empty, ErrorBox, Pill, Skeleton, SourceBadge, StatusDot,
  cx, fmtNumber, fmtTime,
} from "../components/ui";

const STATUS_STYLE: Record<string, { tone: "ok" | "warn" | "bad" | "idle"; label: string }> = {
  confirmed: { tone: "ok", label: "Confirmed on switches" },
  awaiting_confirmation: { tone: "warn", label: "Awaiting switch confirmation" },
  not_observed: { tone: "bad", label: "Not observed on switches" },
  unverified: { tone: "idle", label: "Cannot verify yet" },
};

export function RLDecisionsView({
  runId,
  snapshot,
}: {
  runId?: string;
  snapshot: LiveSnapshot | null;
}) {
  const { data, loading, error, refresh } = useAsync(
    () => api.decisions(runId, 80),
    [runId],
    runId ? undefined : 4000,
  );
  const verification = useAsync<Verification>(
    () => api.verification(runId),
    [runId],
    runId ? undefined : 4000,
  );

  // Prefer the websocket's fresher view when watching a live run.
  const liveDecisions = !runId ? snapshot?.decisions : undefined;
  const decisions: Decision[] = (liveDecisions?.length ? liveDecisions : data?.decisions) ?? [];
  const states = data?.states ?? {};
  const verify = (!runId ? snapshot?.verification : undefined) ?? verification.data ?? undefined;

  if (loading && !data) return <Card title="RL decisions"><Skeleton rows={8} /></Card>;
  if (error && !data) {
    return <Card title="RL decisions"><ErrorBox message={error} onRetry={refresh} /></Card>;
  }

  if (!data?.available || decisions.length === 0) {
    return (
      <Card title="RL decisions" right={<SourceBadge source="unavailable" />}>
        <Empty
          title="No agent decisions"
          detail={
            data?.reason ??
            "Decisions appear once an experiment is running, or when you open a completed run."
          }
        />
      </Card>
    );
  }

  const latest = decisions[decisions.length - 1];
  const previous = decisions.length > 1 ? decisions[decisions.length - 2] : null;
  const isVariantA = latest.variant === "A";

  return (
    <div className="flex flex-col gap-4 min-h-0">
      <div className="grid grid-cols-1 lg:grid-cols-[360px_1fr] gap-4">
        <CurrentDecision
          latest={latest}
          previous={previous}
          verify={verify}
          decoded={states[latest.state]}
          isVariantA={isVariantA}
        />
        <RewardBreakdown latest={latest} decisions={decisions} />
      </div>

      <Card
        title="Decision history"
        subtitle={data.note}
        right={<SourceBadge source={data.source ?? "live"} />}
        bodyClassName="p-0"
      >
        <div className="overflow-auto" style={{ maxHeight: 420 }}>
          <table className="w-full text-[11px]">
            <thead className="sticky top-0" style={{ background: "var(--surface-2)" }}>
              <tr style={{ color: "var(--text-3)" }}>
                <Th>Time</Th><Th>Step</Th><Th>State</Th><Th>Path</Th>
                <Th right>Idle</Th><Th>Policy</Th><Th right>ε</Th>
                <Th right>Q before→after</Th><Th right>TD err</Th><Th right>Reward</Th>
              </tr>
            </thead>
            <tbody>
              {[...decisions].reverse().map((d, i) => (
                <tr key={i} className="border-t hover:bg-white/[0.02]"
                    style={{ borderColor: "var(--border-1)" }}>
                  <td className="px-3 py-1.5 num">{fmtTime(d.t)}</td>
                  <td className="px-3 py-1.5 num">{d.step}</td>
                  <td className="px-3 py-1.5 mono" title={describeState(states[d.state])}>
                    {d.state}
                  </td>
                  <td className="px-3 py-1.5">
                    <Pill color={d.path === "A" ? "var(--variant-a)" : "var(--variant-c)"}>
                      {d.path}
                    </Pill>
                  </td>
                  <td className="px-3 py-1.5 num text-right">
                    {d.idle_timeout !== null ? `${d.idle_timeout}s` : "—"}
                  </td>
                  <td className="px-3 py-1.5">
                    {d.exploratory === null ? (
                      <span style={{ color: "var(--text-3)" }}>n/a</span>
                    ) : d.exploratory ? (
                      <Pill color="var(--warn)" title="Exploratory: ε-greedy overrode the greedy action.">
                        explore
                      </Pill>
                    ) : (
                      <Pill color="var(--ok)" title="Greedy: highest Q-value among allowed actions.">
                        exploit
                      </Pill>
                    )}
                  </td>
                  <td className="px-3 py-1.5 num text-right">
                    {d.epsilon !== null ? fmtNumber(d.epsilon, 3) : "—"}
                  </td>
                  <td className="px-3 py-1.5 num text-right">
                    {d.q_before !== null && d.q_after !== null
                      ? `${fmtNumber(d.q_before, 3)} → ${fmtNumber(d.q_after, 3)}`
                      : "—"}
                  </td>
                  <td className="px-3 py-1.5 num text-right">
                    {d.td_error !== null ? fmtNumber(d.td_error, 3) : "—"}
                  </td>
                  <td className="px-3 py-1.5 num text-right"
                      style={{ color: (d.reward ?? 0) >= 0 ? "var(--ok)" : "var(--bad)" }}>
                    {d.reward !== null ? fmtNumber(d.reward, 3) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {isVariantA && (
          <p className="px-4 py-2 text-[10px] border-t leading-relaxed"
             style={{ borderColor: "var(--border-1)", color: "var(--text-3)" }}>
            Variant A has no learning agent. It still records a state, an action
            and a reward so it can be compared on equal terms, but Q-values,
            ε and exploration do not exist for it and are shown as n/a rather
            than zero.
          </p>
        )}
      </Card>
    </div>
  );
}

function describeState(decoded?: DecodedState): string {
  if (!decoded?.decoded) return "state bins";
  return [
    `path A utilisation: ${decoded.util_a?.label}`,
    `path B utilisation: ${decoded.util_b?.label}`,
    `flow-table occupancy: ${decoded.occupancy?.label}`,
    `table-miss rate: ${decoded.miss_rate?.label}`,
  ].join("\n");
}

function CurrentDecision({
  latest, previous, verify, decoded, isVariantA,
}: {
  latest: Decision;
  previous: Decision | null;
  verify?: Verification;
  decoded?: DecodedState;
  isVariantA: boolean;
}) {
  const status = verify?.status ? STATUS_STYLE[verify.status] : null;
  const changed = previous && previous.path !== latest.path;

  return (
    <Card
      title="Latest decision"
      subtitle={`Variant ${latest.variant} · step ${latest.step}`}
      right={<SourceBadge source={verify?.source ?? "live"} />}
    >
      <div className="space-y-3">
        <div className="flex items-center gap-3">
          <div className="flex-1">
            <p className="text-[10px]" style={{ color: "var(--text-3)" }}>Previous route</p>
            <p className="text-[15px] font-semibold">
              {previous ? `Path ${previous.path}` : "—"}
            </p>
          </div>
          <span style={{ color: changed ? "var(--warn)" : "var(--text-3)" }}>→</span>
          <div className="flex-1">
            <p className="text-[10px]" style={{ color: "var(--text-3)" }}>Selected route</p>
            <p className="text-[15px] font-semibold"
               style={{ color: changed ? "var(--warn)" : "var(--text-1)" }}>
              Path {latest.path}
            </p>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-3 text-[12px]">
          <div>
            <p className="text-[10px]" style={{ color: "var(--text-3)" }}>Selected idle timeout</p>
            <p className="num font-medium">
              {latest.idle_timeout !== null ? `${latest.idle_timeout} s` : "—"}
            </p>
          </div>
          <div>
            <p className="text-[10px]" style={{ color: "var(--text-3)" }}>Reward (last interval)</p>
            <p className="num font-medium"
               style={{ color: (latest.reward ?? 0) >= 0 ? "var(--ok)" : "var(--bad)" }}>
              {latest.reward !== null ? fmtNumber(latest.reward, 4) : "—"}
            </p>
          </div>
        </div>

        <div>
          <p className="text-[10px] mb-1" style={{ color: "var(--text-3)" }}>
            Policy basis
          </p>
          <p className="text-[12px] leading-relaxed">
            {isVariantA ? (
              <>Fixed policy: shortest available path by hop count. No learning.</>
            ) : latest.exploratory ? (
              <>
                Exploratory step under ε-greedy
                {latest.epsilon !== null && <> (ε = {fmtNumber(latest.epsilon, 3)})</>}
                {latest.greedy_action && (
                  <> — overrode the greedy action <span className="mono">{latest.greedy_action}</span>.</>
                )}
              </>
            ) : (
              <>
                Greedy step: highest Q-value among allowed actions
                {latest.q_after !== null && <> (Q = {fmtNumber(latest.q_after, 4)})</>}.
              </>
            )}
          </p>
        </div>

        {decoded?.decoded && (
          <div>
            <p className="text-[10px] mb-1" style={{ color: "var(--text-3)" }}>
              Observed state <span className="mono">{decoded.raw}</span>
            </p>
            <div className="grid grid-cols-2 gap-1.5 text-[11px]">
              <StateBin label="Path A util" value={decoded.util_a?.label} />
              <StateBin label="Path B util" value={decoded.util_b?.label} />
              <StateBin label="Occupancy" value={decoded.occupancy?.label} />
              <StateBin label="Miss rate" value={decoded.miss_rate?.label} />
            </div>
          </div>
        )}

        <div
          className="rounded-lg border px-3 py-2"
          style={{ borderColor: "var(--border-1)", background: "var(--surface-2)" }}
        >
          <div className="flex items-center gap-2 mb-1">
            <StatusDot tone={status?.tone ?? "idle"} pulse={verify?.status === "awaiting_confirmation"} />
            <span className="text-[12px] font-medium">
              {status?.label ?? "Installation status unknown"}
            </span>
          </div>
          <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-3)" }}>
            {verify?.detail ??
              "No flow-table dump is available to confirm this decision."}
          </p>
          {verify?.installed?.rule_counts && (
            <p className="text-[11px] mt-1 num" style={{ color: "var(--text-2)" }}>
              Installed at edge switches — path A: {verify.installed.rule_counts.A ?? 0},
              {" "}path B: {verify.installed.rule_counts.B ?? 0}
            </p>
          )}
        </div>
      </div>
    </Card>
  );
}

function StateBin({ label, value }: { label: string; value?: string }) {
  return (
    <div
      className="rounded px-2 py-1"
      style={{ background: "var(--surface-2)" }}
    >
      <p className="text-[9px]" style={{ color: "var(--text-3)" }}>{label}</p>
      <p className="text-[11px]">{value ?? "—"}</p>
    </div>
  );
}

function RewardBreakdown({
  latest, decisions,
}: {
  latest: Decision;
  decisions: Decision[];
}) {
  const terms = latest.reward_terms ?? {};
  // Weights are the project's configured reward weights; the sign shows which
  // way each term pushes the total.
  const WEIGHTS: Record<string, { w: number; sign: 1 | -1; label: string }> = {
    throughput: { w: 1.0, sign: 1, label: "Throughput" },
    utilisation: { w: 0.4, sign: -1, label: "Max link utilisation" },
    loss: { w: 0.6, sign: -1, label: "Packet loss" },
    occupancy: { w: 0.25, sign: -1, label: "Flow-table occupancy" },
    controller_msgs: { w: 0.15, sign: -1, label: "Reactive controller msgs" },
  };
  const rewards = decisions.map((d) => d.reward ?? 0);
  const max = Math.max(...rewards.map(Math.abs), 0.1);

  return (
    <Card
      title="Reward breakdown"
      subtitle="Computed only from OpenFlow counters — the agent never sees ping or iperf3"
    >
      <div className="space-y-2">
        {Object.entries(WEIGHTS).map(([key, meta]) => {
          const value = terms[key];
          const contribution = value === null || value === undefined ? null : value * meta.w * meta.sign;
          return (
            <div key={key}>
              <div className="flex justify-between text-[11px] mb-0.5">
                <span>
                  {meta.label}
                  <span className="ml-1" style={{ color: "var(--text-3)" }}>
                    ×{meta.w}{meta.sign < 0 ? " (penalty)" : ""}
                  </span>
                </span>
                <span className="num" style={{ color: "var(--text-2)" }}>
                  {value === null || value === undefined ? "—" : fmtNumber(value, 3)}
                  {contribution !== null && (
                    <span style={{ color: contribution >= 0 ? "var(--ok)" : "var(--bad)" }}>
                      {" "}({contribution >= 0 ? "+" : ""}{fmtNumber(contribution, 3)})
                    </span>
                  )}
                </span>
              </div>
              <div className="h-1.5 rounded-full" style={{ background: "var(--surface-3)" }}>
                <div
                  className="h-full rounded-full"
                  style={{
                    width: `${Math.min(100, Math.abs(value ?? 0) * 100)}%`,
                    background: meta.sign > 0 ? "var(--ok)" : "var(--bad)",
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>

      <div className="mt-4">
        <p className="text-[10px] mb-1" style={{ color: "var(--text-3)" }}>
          Reward over the last {rewards.length} steps
        </p>
        <svg width="100%" height="56" viewBox={`0 0 ${Math.max(rewards.length, 2)} 2`}
             preserveAspectRatio="none">
          <line x1="0" y1="1" x2={rewards.length} y2="1"
                stroke="var(--border-2)" strokeWidth="0.02" />
          <polyline
            points={rewards.map((r, i) => `${i},${1 - (r / max)}`).join(" ")}
            fill="none" stroke="var(--variant-c)" strokeWidth="0.04"
            vectorEffect="non-scaling-stroke"
          />
        </svg>
      </div>
    </Card>
  );
}

function Th({ children, right }: { children: React.ReactNode; right?: boolean }) {
  return (
    <th className={cx(
      "px-3 py-2 font-medium text-[10px] uppercase tracking-wider whitespace-nowrap",
      right ? "text-right" : "text-left",
    )}>
      {children}
    </th>
  );
}
