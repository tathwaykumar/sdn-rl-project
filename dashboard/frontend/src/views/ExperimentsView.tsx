/** Experiment control.
 *
 * The form is built from the backend's own option list, which comes from the
 * runner's tables, so the UI cannot offer a variant or workload the runner
 * does not implement.
 *
 * Starting is blocked whenever anything is already running -- including a
 * matrix sweep started from a terminal -- because Mininet, Open vSwitch and
 * TCP 6653 are exclusive. When the API is unprivileged (the normal case, since
 * a web service should not run as root), the exact command is shown instead of
 * a button that would fail.
 */
import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAsync } from "../hooks/useLive";
import type { ExperimentOptions, ExperimentStatus } from "../api/types";
import {
  AboutPanel, Button, Card, Empty, ErrorBox, Field, Pill, Skeleton, SourceBadge,
  StatusDot, VARIANT_COLOR, cx, fmtDuration, inputClass, inputStyle,
} from "../components/ui";

/** What each workload actually offers and why the project includes it —
    verified against `experiments/workloads.py`, which is where the traffic
    each one generates is implemented. The dashboard's own one-line hints
    (shown under the Workload dropdown below) come from the backend's
    `_WORKLOAD_HELP`; this panel is the fuller version of the same facts. */
const WORKLOAD_EXPLAIN: {
  id: string; offers: string; tests: string; lookAt: string;
}[] = [
  {
    id: "light",
    offers: "One flow at 1 Mbit/s. Path A (the short, 5 Mbit path) is "
      + "genuinely the better choice here — it is never close to full.",
    tests: "Whether a policy over-reacts: does it needlessly move traffic "
      + "to path B when the small path is already enough?",
    lookAt: "Throughput should be fully delivered on any variant; a policy "
      + "that still spreads load here is paying a cost for nothing.",
  },
  {
    id: "heavy",
    offers: "Two saturating TCP flows started together — more combined "
      + "load than path A's 5 Mbit capacity can carry.",
    tests: "Whether an adaptive policy spills the second flow onto path B "
      + "instead of overloading path A and dropping or queueing traffic.",
    lookAt: "Throughput and packet loss. A/B/C Comparison is where this "
      + "gets tested for real, across seeds.",
  },
  {
    id: "churn",
    offers: "24 short flows (4s each), distinct source/destination/port "
      + "combinations, arriving on a seeded schedule.",
    tests: "Flow-table management: each new flow costs a table miss and a "
      + "controller round trip. This is the workload where variant C's "
      + "idle-timeout choice has something real to gain or lose.",
    lookAt: "Flow-table occupancy and controller load, on the Metrics page "
      + "or the flow-table panels below it.",
  },
  {
    id: "failure",
    offers: "The heavy workload, plus a scripted cut of the link s1–s2 "
      + "partway through the run, then its repair.",
    tests: "Reconvergence: how quickly and correctly a policy reacts once "
      + "the path it was using goes down.",
    lookAt: "The event feed and Live Topology around the failure time, and "
      + "link-failure recovery time where it is reported.",
  },
];

export function ExperimentsView() {
  const options = useAsync<ExperimentOptions>(() => api.experimentOptions(), []);
  const status = useAsync<ExperimentStatus>(() => api.experimentStatus(), [], 3000);
  const log = useAsync(() => api.experimentLog(300), [], 3000);

  const [variant, setVariant] = useState("C");
  const [workload, setWorkload] = useState("heavy");
  const [seed, setSeed] = useState(1);
  const [duration, setDuration] = useState(90);
  const [idleTimeout, setIdleTimeout] = useState<number | "">("");
  const [command, setCommand] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  useEffect(() => {
    if (!options.data) return;
    api
      .experimentCommand({
        variant, workload, seed, duration_s: duration,
        idle_timeout: idleTimeout === "" ? null : Number(idleTimeout),
      })
      .then((r) => setCommand(r.command))
      .catch(() => setCommand(null));
  }, [variant, workload, seed, duration, idleTimeout, options.data]);

  if (options.loading && !options.data) {
    return <Card title="Experiment control"><Skeleton rows={6} /></Card>;
  }
  if (options.error) {
    return <Card title="Experiment control"><ErrorBox message={options.error} onRetry={options.refresh} /></Card>;
  }

  const opts = options.data!;
  const st = status.data;
  const selectsOwnTimeout = opts.variants.find((v) => v.id === variant)?.selects_own_timeout;
  const canStart = st?.can_start ?? false;
  const launched = st?.launched_by_dashboard as Record<string, unknown> | null;
  const launcherCurrent = st?.launcher?.current ?? null;
  const dashboardRunning = Boolean(launcherCurrent?.running) || Boolean(launched && launched.running);

  const start = async () => {
    setBusy(true);
    setMessage(null);
    try {
      await api.experimentStart({
        variant, workload, seed, duration_s: duration,
        idle_timeout: idleTimeout === "" ? null : Number(idleTimeout),
      });
      setMessage({ kind: "ok", text: "Experiment started." });
      void status.refresh();
    } catch (cause) {
      setMessage({ kind: "err", text: cause instanceof Error ? cause.message : String(cause) });
    } finally {
      setBusy(false);
    }
  };

  const stop = async () => {
    setBusy(true);
    setMessage(null);
    try {
      // Pass the id explicitly: the server refuses a mismatch, so a stale tab
      // cannot stop an experiment it is not actually looking at.
      const result = await api.experimentStop(launcherCurrent?.experiment_id);
      setMessage({ kind: "ok", text: String(result.note ?? "Experiment stopped.") });
      void status.refresh();
    } catch (cause) {
      setMessage({ kind: "err", text: cause instanceof Error ? cause.message : String(cause) });
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <AboutPanel
        summary={
          <>
            Start, stop and monitor a single run. A, B and C are the three
            fixed routing strategies the project compares — this page launches
            one of them; <strong>A/B/C Comparison</strong> is where results
            from many runs are aggregated into a verdict.
          </>
        }
        terms={[
          { term: "Variant A", def: "Conventional routing, fixed timeouts — the baseline." },
          { term: "Variant B", def: "RL chooses the path; timeouts stay fixed." },
          { term: "Variant C", def: "RL chooses the path and the flow-rule timeout." },
          { term: "Seed", def: "Fixes randomness so a run can be reproduced exactly." },
        ]}
      />

      <Card title="What each workload tests" className="mb-4">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {WORKLOAD_EXPLAIN.map((w) => (
            <div key={w.id} className="rounded-lg px-3 py-2.5" style={{ background: "var(--surface-2)" }}>
              <p className="t-small font-semibold mb-1">{w.id}</p>
              <p className="t-small leading-relaxed mb-1.5" style={{ color: "var(--text-2)" }}>
                {w.offers}
              </p>
              <p className="t-micro leading-relaxed" style={{ color: "var(--text-3)" }}>
                <strong style={{ color: "var(--text-2)" }}>Tests:</strong> {w.tests}
              </p>
              <p className="t-micro leading-relaxed mt-1" style={{ color: "var(--text-4)" }}>
                <strong>Look at:</strong> {w.lookAt}
              </p>
            </div>
          ))}
        </div>
        <p className="t-micro leading-relaxed mt-3" style={{ color: "var(--text-4)" }}>
          Every workload is driven by a seeded random generator, so the same
          (workload, seed) pair offers identical traffic to every variant —
          any difference in the results comes from the controller's routing
          choices, not from different traffic being offered.
        </p>
      </Card>

      <div className="grid grid-cols-1 xl:grid-cols-[400px_1fr] gap-4">
      <div className="flex flex-col gap-4">
        <Card title="Start a run" subtitle="Invokes experiments/run_experiment.py directly">
          <div className="space-y-3">
            <Field label="Variant">
              <div className="grid grid-cols-3 gap-1.5 min-w-0">
                {opts.variants.map((v) => (
                  <button
                    key={v.id}
                    onClick={() => setVariant(v.id)}
                    title={v.label}
                    className={cx(
                      "rounded-lg border px-2 py-2 text-[12px] font-semibold transition-colors",
                      variant === v.id && "ring-1",
                    )}
                    style={{
                      background: variant === v.id ? "var(--surface-3)" : "var(--surface-2)",
                      borderColor: variant === v.id ? VARIANT_COLOR[v.id] : "var(--border-2)",
                      color: variant === v.id ? VARIANT_COLOR[v.id] : "var(--text-2)",
                    }}
                  >
                    {v.id}
                  </button>
                ))}
              </div>
              <p className="text-[10px] mt-1" style={{ color: "var(--text-3)" }}>
                {opts.variants.find((v) => v.id === variant)?.label}
              </p>
            </Field>

            <Field
              label="Workload"
              hint={opts.workloads.find((w) => w.id === workload)?.description}
            >
              <select
                value={workload}
                onChange={(e) => setWorkload(e.target.value)}
                className={inputClass}
                style={inputStyle}
              >
                {opts.workloads.map((w) => (
                  <option key={w.id} value={w.id}>{w.id}</option>
                ))}
              </select>
            </Field>

            <div className="grid grid-cols-2 gap-3">
              <Field label="Seed">
                <input
                  type="number" value={seed}
                  min={opts.seed.min} max={opts.seed.max}
                  onChange={(e) => setSeed(Number(e.target.value))}
                  className={inputClass} style={inputStyle}
                />
              </Field>
              <Field label={`Duration (${opts.duration.unit})`}>
                <input
                  type="number" value={duration}
                  min={opts.duration.min} max={opts.duration.max}
                  onChange={(e) => setDuration(Number(e.target.value))}
                  className={inputClass} style={inputStyle}
                />
              </Field>
            </div>

            <Field
              label="Fixed idle timeout"
              hint={
                selectsOwnTimeout
                  ? "Variant C selects its own idle timeout, so this control does not apply."
                  : "Applied to every rule for the whole run."
              }
            >
              <select
                value={idleTimeout}
                disabled={selectsOwnTimeout}
                onChange={(e) =>
                  setIdleTimeout(e.target.value === "" ? "" : Number(e.target.value))
                }
                className={cx(inputClass, selectsOwnTimeout && "opacity-40")}
                style={inputStyle}
              >
                <option value="">runner default (15 s)</option>
                {opts.idle_timeouts.map((t) => (
                  <option key={t} value={t}>{t} s</option>
                ))}
              </select>
            </Field>

            <div className="flex gap-2 pt-1">
              <Button variant="primary" onClick={start} disabled={!canStart || busy}>
                {busy ? "Working…" : "Start experiment"}
              </Button>
              <Button variant="danger" onClick={stop} disabled={!dashboardRunning || busy}>
                Stop
              </Button>
            </div>

            {st?.blocked_reason && (
              <div
                className="rounded-lg border px-3 py-2 text-[11px] leading-relaxed"
                style={{
                  borderColor: "color-mix(in srgb, var(--warn) 35%, transparent)",
                  background: "var(--warn-dim)",
                  color: "var(--warn)",
                }}
              >
                {st.blocked_reason}
              </div>
            )}

            {message && (
              <div
                className="rounded-lg border px-3 py-2 text-[11px] leading-relaxed"
                style={{
                  borderColor: `color-mix(in srgb, var(${
                    message.kind === "ok" ? "--ok" : "--bad"
                  }) 35%, transparent)`,
                  background: message.kind === "ok" ? "var(--ok-dim)" : "var(--bad-dim)",
                  color: message.kind === "ok" ? "var(--ok)" : "var(--bad)",
                }}
              >
                {message.text}
              </div>
            )}

            {command && (
              <div>
                <p className="text-[10px] mb-1" style={{ color: "var(--text-3)" }}>
                  Equivalent command (run from the project root)
                </p>
                <code
                  className="block mono text-[10px] rounded-lg border px-2.5 py-2 break-all"
                  style={{ background: "var(--surface-0)", borderColor: "var(--border-1)" }}
                >
                  {command}
                </code>
              </div>
            )}
          </div>
        </Card>

        <LinkInjectionPanel
          experimentId={launcherCurrent?.experiment_id ?? null}
          enabled={Boolean(st?.can_inject_link)}
          onDone={() => void status.refresh()}
        />
      </div>

      <div className="flex flex-col gap-4">
        <LauncherStatusCard status={st} />

        <Card
          title="Current activity"
          right={<SourceBadge source="live" />}
        >
          {!st ? (
            <Skeleton rows={3} />
          ) : st.live_run.active ? (
            <div className="space-y-2">
              <div className="flex items-center gap-2">
                <StatusDot tone="ok" pulse />
                <span className="text-[13px] font-semibold">
                  {st.live_run.variant} · {st.live_run.workload} · seed {st.live_run.seed}
                </span>
                {st.live_run.matrix_running && <Pill color="var(--warn)">matrix sweep</Pill>}
              </div>
              <p className="text-[11px] mono" style={{ color: "var(--text-3)" }}>
                {st.live_run.run_id}
              </p>
              {st.live_run.elapsed_s !== null && st.live_run.duration_s && (
                <div>
                  <div className="flex justify-between text-[10px] mb-1"
                       style={{ color: "var(--text-3)" }}>
                    <span>{fmtDuration(st.live_run.elapsed_s)} elapsed</span>
                    <span>of {fmtDuration(st.live_run.duration_s)}</span>
                  </div>
                  <div className="h-1.5 rounded-full" style={{ background: "var(--surface-3)" }}>
                    <div
                      className="h-full rounded-full transition-all"
                      style={{
                        width: `${Math.min(100, (st.live_run.elapsed_s / st.live_run.duration_s) * 100)}%`,
                        background: "var(--ok)",
                      }}
                    />
                  </div>
                </div>
              )}
              <p className="text-[11px]" style={{ color: "var(--text-3)" }}>
                {st.live_run.detail}
              </p>
              {st.external_experiment_running && (
                <p className="text-[11px] leading-relaxed pt-1" style={{ color: "var(--warn)" }}>
                  This run was started outside the dashboard, so it cannot be
                  stopped from here. Stop it in the terminal that owns it.
                </p>
              )}
            </div>
          ) : (
            <Empty title="Nothing running" detail="No experiment process is active." />
          )}
        </Card>

        <Card
          title="Runner output"
          subtitle="Only for experiments started from this dashboard"
          bodyClassName="p-0"
        >
          {log.data?.available ? (
            <pre
              className="mono text-[10px] p-3 overflow-auto leading-relaxed"
              style={{ maxHeight: 320, color: "var(--text-2)" }}
            >
              {(log.data.lines ?? []).join("\n") || "(no output yet)"}
            </pre>
          ) : (
            <Empty
              title="No dashboard-launched run"
              detail={log.data?.reason ?? "Start an experiment here to see its output."}
            />
          )}
        </Card>

        {st?.external_processes && st.external_processes.length > 0 && (
          <Card title="Detected experiment processes" subtitle="Protected from the dashboard">
            <div className="space-y-1.5">
              {st.external_processes.map((p) => (
                <div key={p.pid} className="text-[10px] mono flex gap-2">
                  <span style={{ color: "var(--text-3)" }}>pid {p.pid}</span>
                  <span className="truncate flex-1" title={p.cmd}>{p.cmd}</span>
                  <span style={{ color: "var(--text-3)" }}>{fmtDuration(p.elapsed_s)}</span>
                </div>
              ))}
            </div>
          </Card>
        )}
      </div>
      </div>
    </div>
  );
}

/** Privileged-launcher status.
 *
 * Shows how a start request would actually be served, because "Start" being
 * greyed out with no explanation is the problem this whole feature exists to
 * fix.
 */
function LauncherStatusCard({ status }: { status?: ExperimentStatus | null }) {
  if (!status) return null;
  const mechanism = status.start_mechanism;
  const tone =
    mechanism === "launcher" ? "ok" : mechanism === "root_api" ? "warn" : "idle";
  const label =
    mechanism === "launcher"
      ? "Privileged launcher connected"
      : mechanism === "root_api"
        ? "API is running as root"
        : "No privileged launcher";

  return (
    <Card title="Experiment launching" right={<SourceBadge source="live" />}>
      <div className="flex items-center gap-2 mb-2">
        <StatusDot tone={tone as "ok" | "warn" | "idle"} />
        <span className="text-[12px] font-medium">{label}</span>
      </div>
      <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-3)" }}>
        {mechanism === "launcher" ? (
          <>
            This API is unprivileged and asks a small root daemon over a Unix
            socket to start and stop experiments. Nothing you type becomes part
            of a command: the daemon builds the command line itself from a
            fixed allowlist.
          </>
        ) : mechanism === "root_api" ? (
          <>
            The API itself has root, so it launches the runner directly. This
            works, but a root-capable web service is a larger attack surface
            than necessary; running the launcher daemon and keeping the API
            unprivileged is the recommended setup.
          </>
        ) : (
          <>{status.launcher_detail || "Experiments cannot be started from the browser."}</>
        )}
      </p>
      {status.launcher_error && (
        <p className="text-[11px] mt-2" style={{ color: "var(--warn)" }}>
          Launcher error: {status.launcher_error}
        </p>
      )}
    </Card>
  );
}

/** Inject a link failure into the experiment that is running now.
 *
 * This is a real action applied to the live Mininet session by the runner,
 * not an animation. It is only offered while an experiment the dashboard
 * started is running, because nothing else can safely own the change.
 */
function LinkInjectionPanel({
  experimentId,
  enabled,
  onDone,
}: {
  experimentId: string | null;
  enabled: boolean;
  onDone: () => void;
}) {
  const links = useAsync(() => api.experimentLinks(), []);
  const [link, setLink] = useState("s1-s2");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ kind: "ok" | "err"; text: string } | null>(null);

  const send = async (action: "link_down" | "link_up") => {
    if (!experimentId) return;
    setBusy(true);
    setNote(null);
    try {
      const result = await api.experimentInjectLink({
        experiment_id: experimentId,
        action,
        link,
      });
      setNote({ kind: "ok", text: String(result.note ?? "Queued.") });
      onDone();
    } catch (cause) {
      setNote({
        kind: "err",
        text: cause instanceof Error ? cause.message : String(cause),
      });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="Inject a link failure"
      subtitle="Applied to the live Mininet session by the runner"
    >
      {!enabled ? (
        <p className="text-[11px] leading-relaxed" style={{ color: "var(--text-3)" }}>
          Available while an experiment started from this dashboard is running.
          The runner owns the Mininet session, so only it can change a link —
          the dashboard asks, it acts, and the controller observes the port
          going down exactly as it would for any real failure.
          {" "}The <span className="mono">failure</span> workload also schedules
          its own cut of <span className="mono">s1-s2</span> partway through.
        </p>
      ) : (
        <div className="space-y-3">
          <Field label="Link" hint={links.data?.note}>
            <select
              value={link}
              onChange={(e) => setLink(e.target.value)}
              className={inputClass}
              style={inputStyle}
            >
              {(links.data?.links ?? []).map((l) => (
                <option key={l.id} value={l.id}>
                  {l.id} — {l.bandwidth_mbit} Mbit
                  {l.on_paths.length > 0 ? ` (path ${l.on_paths.join("/")})` : ""}
                </option>
              ))}
            </select>
          </Field>
          <div className="flex gap-2">
            <Button variant="danger" onClick={() => send("link_down")} disabled={busy}>
              Bring link down
            </Button>
            <Button onClick={() => send("link_up")} disabled={busy}>
              Restore link
            </Button>
          </div>
          {note && (
            <p
              className="text-[11px] leading-relaxed"
              style={{ color: note.kind === "ok" ? "var(--ok)" : "var(--bad)" }}
            >
              {note.text}
            </p>
          )}
          <p className="text-[10px] leading-relaxed" style={{ color: "var(--text-3)" }}>
            The topology view will not redraw until the switches confirm the
            change in a flow dump, so a brief delay is expected and correct.
          </p>
        </div>
      )}
    </Card>
  );
}
