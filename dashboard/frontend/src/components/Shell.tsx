/** Console chrome: icon rail, live telemetry strip, event ticker.
 *
 * This is deliberately not a web page layout. There is no document column and
 * no header-and-sidebar furniture: the rail is a fixed instrument panel, the
 * top strip is a continuously updating readout, and the bottom strip is a
 * running event ticker. Content fills everything in between, edge to edge.
 *
 * Connection state and data provenance are always visible. A console that
 * keeps rendering the last frame after its stream dies is worse than one that
 * shows nothing, because stale numbers look exactly like current ones.
 *
 * Every moving element in here is either ambient (the beam under the top
 * strip, which carries no information) or driven by real telemetry (the
 * stream equaliser, the readout flash, the ticker). Nothing animates to
 * suggest activity that the data does not show.
 */
import { useEffect, useState, type ReactNode } from "react";
import type { ConnectionState } from "../hooks/useLive";
import type { LiveSnapshot } from "../api/types";
import { StatusDot, cx, fmtAge, fmtNumber } from "./ui";

export type PageId =
  | "overview" | "topology" | "metrics" | "reward"
  | "decisions" | "experiments" | "comparison" | "history";

export interface NavItem {
  id: PageId;
  label: string;
  group: "Operations" | "Reinforcement learning" | "Experiments";
  icon: ReactNode;
  hint: string;
}

/** Inline SVG icons: no icon-font dependency, and they inherit currentColor. */
const icon = (path: ReactNode) => (
  <svg
    width="18" height="18" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"
    strokeLinejoin="round" aria-hidden="true"
  >
    {path}
  </svg>
);

export const NAV_ITEMS: NavItem[] = [
  {
    id: "overview", label: "Overview", group: "Operations",
    hint: "What is the network doing right now?",
    icon: icon(<><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>),
  },
  {
    id: "topology", label: "Live Topology", group: "Operations",
    hint: "Where is traffic actually forwarded, and on whose evidence?",
    icon: icon(<><circle cx="5" cy="12" r="2.4" /><circle cx="19" cy="6" r="2.4" /><circle cx="19" cy="18" r="2.4" /><path d="M7.2 11 16.8 6.8M7.2 13l9.6 4.2" /></>),
  },
  {
    id: "metrics", label: "Metrics & Analytics", group: "Operations",
    hint: "How is the network performing, and how is each figure measured?",
    icon: icon(<><path d="M3 20h18" /><rect x="5" y="11" width="3.4" height="6" rx="1" /><rect x="10.3" y="7" width="3.4" height="10" rx="1" /><rect x="15.6" y="13" width="3.4" height="4" rx="1" /></>),
  },
  {
    id: "reward", label: "Reward Explorer", group: "Reinforcement learning",
    hint: "What score did the agent earn, and which term produced it?",
    icon: icon(<><circle cx="12" cy="9" r="5" /><path d="M8.5 13.5 7 21l5-2.6L17 21l-1.5-7.5" /></>),
  },
  {
    id: "decisions", label: "Decision Inspector", group: "Reinforcement learning",
    hint: "What did the agent decide, and did the switches carry it out?",
    icon: icon(<><path d="M12 3v6M12 15v6M5.6 7.6l4.2 4.2M14.2 12.2l4.2 4.2" /><circle cx="12" cy="12" r="2.6" /></>),
  },
  {
    id: "experiments", label: "Experiments", group: "Experiments",
    hint: "Start, stop and monitor runs",
    icon: icon(<><path d="M9 3h6M10 3v6.2L5.3 18a2 2 0 0 0 1.7 3h10a2 2 0 0 0 1.7-3L14 9.2V3" /></>),
  },
  {
    id: "comparison", label: "A/B/C Comparison", group: "Experiments",
    hint: "Did the agent beat fixed routing, and is the difference real?",
    icon: icon(<><path d="M4 20V9M12 20V4M20 20v-7" /><path d="M2 20h20" /></>),
  },
  {
    id: "history", label: "History", group: "Experiments",
    hint: "Which runs produced these findings, and can they be traced?",
    icon: icon(<><path d="M3 12a9 9 0 1 0 2.6-6.4" /><path d="M3 4v5h5" /><path d="M12 7.5V12l3 1.8" /></>),
  },
];

const GROUPS: NavItem["group"][] = [
  "Operations", "Reinforcement learning", "Experiments",
];

/** Three-letter rail tag per group, so the rail stays narrow. */
const GROUP_TAG: Record<NavItem["group"], string> = {
  "Operations": "OPS",
  "Reinforcement learning": "RL",
  "Experiments": "EXP",
};

export function Shell({
  page, onNavigate, connection, lastUpdate, connectionError, onReconnect,
  mock, runBadge, snapshot, children,
}: {
  page: PageId;
  onNavigate: (page: PageId) => void;
  connection: ConnectionState;
  lastUpdate: number | null;
  connectionError: string | null;
  onReconnect: () => void;
  mock: boolean;
  runBadge: ReactNode;
  snapshot?: LiveSnapshot | null;
  children: ReactNode;
}) {
  const active = NAV_ITEMS.find((i) => i.id === page);
  const streaming = connection === "open"
    && lastUpdate !== null && Date.now() - lastUpdate < 15000;

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {mock && (
        <div
          role="status"
          className="px-4 py-1 t-micro font-bold text-center tracking-[0.18em] shrink-0"
          style={{
            background: "var(--warn-dim)",
            color: "var(--warn)",
            borderBottom: "1px solid rgba(255,197,61,0.3)",
          }}
        >
          MOCK MODE — every value shown is synthetic demo data, not a
          measurement of any network.
        </div>
      )}

      <div className="flex-1 flex min-h-0">
        <Rail page={page} onNavigate={onNavigate} />

        <div className="flex-1 flex flex-col min-w-0">
          {/* Top strip: identity, live readouts, stream health. */}
          <header
            className="chrome beam shrink-0 flex items-center gap-3 px-3 h-[52px]
                       border-b overflow-x-auto no-sb"
          >
            <div className="flex items-center gap-2 shrink-0">
              <ReactorMark live={streaming} />
              <div className="leading-none hidden sm:block">
                <p
                  className="t-h2 whitespace-nowrap"
                  style={{ color: "var(--text-1)" }}
                >
                  SDN · RL Console
                </p>
                <p className="t-micro mt-0.5" style={{ color: "var(--text-4)" }}>
                  NETWORK OPS &amp; RL ANALYTICS
                </p>
              </div>
            </div>

            <div
              className="h-7 w-px shrink-0 hidden md:block"
              style={{ background: "var(--border-1)" }}
            />

            <Readouts snapshot={snapshot} />

            <div className="flex-1 min-w-[0.5rem]" />

            <div className="flex items-center gap-2 shrink-0">
              {runBadge}
              <StreamChip
                state={connection} lastUpdate={lastUpdate} streaming={streaming}
                error={connectionError} onReconnect={onReconnect}
              />
            </div>
          </header>

          {/* Content: full bleed. The page title lives here, not in the chrome,
              so the chrome stays a thin instrument strip. */}
          <main className="flex-1 overflow-auto min-h-0 px-3 md:px-4 pb-4">
            <div className="flex items-end gap-3 pt-4 pb-3">
              <div
                className="w-[3px] self-stretch rounded-full shrink-0"
                style={{
                  background: `linear-gradient(180deg, ${
                    active?.group === "Reinforcement learning"
                      ? "var(--rl)" : "var(--accent)"
                  }, transparent)`,
                  boxShadow: `0 0 14px ${
                    active?.group === "Reinforcement learning"
                      ? "var(--rl-glow)" : "var(--accent-glow)"
                  }`,
                }}
                aria-hidden="true"
              />
              <div className="min-w-0">
                <h1
                  className="t-display uppercase truncate"
                  style={{ letterSpacing: "0.01em" }}
                >
                  {active?.label ?? "Console"}
                </h1>
                <p className="t-small truncate" style={{ color: "var(--text-3)" }}>
                  {active?.hint}
                </p>
              </div>
            </div>
            {children}
          </main>

          <Ticker snapshot={snapshot} />
        </div>
      </div>
    </div>
  );
}

/** The instrument rail. Icon-only at every width — it is a panel, not a menu
    that expands into a column of links. */
function Rail({
  page, onNavigate,
}: {
  page: PageId;
  onNavigate: (page: PageId) => void;
}) {
  return (
    <aside
      className="chrome shrink-0 w-[60px] flex flex-col items-center border-r z-30 gap-1 py-2"
      aria-label="Sections"
    >
      {GROUPS.map((group, gi) => (
        <div key={group} className="w-full flex flex-col items-center">
          {gi > 0 && (
            <div
              className="w-5 h-px my-1.5"
              style={{ background: "var(--border-1)" }}
              aria-hidden="true"
            />
          )}
          <p
            className="t-micro font-bold mb-1 select-none"
            style={{ color: "var(--text-4)", letterSpacing: "0.1em" }}
            title={group}
          >
            {GROUP_TAG[group]}
          </p>
          {/* The full group name stays in the DOM for assistive tech and for
              anyone widening the rail; the three-letter tag above is the
              visible form. */}
          <p className="sr-only">{group}</p>
          <ul className="w-full space-y-1 px-1.5">
            {NAV_ITEMS.filter((i) => i.group === group).map((item) => {
              const isActive = item.id === page;
              const tint = group === "Reinforcement learning"
                ? "var(--rl)" : "var(--accent)";
              return (
                <li key={item.id} className="relative group">
                  <button
                    onClick={() => onNavigate(item.id)}
                    aria-current={isActive ? "page" : undefined}
                    aria-label={item.label}
                    className={cx(
                      "w-full h-[38px] rounded-[10px] flex items-center justify-center",
                      "relative transition-all duration-200",
                    )}
                    style={{
                      background: isActive
                        ? (group === "Reinforcement learning"
                            ? "var(--rl-dim)" : "var(--accent-dim)")
                        : "transparent",
                      color: isActive ? tint : "var(--text-3)",
                      border: `1px solid ${isActive ? tint : "transparent"}`,
                      boxShadow: isActive
                        ? `0 0 20px -6px ${
                            group === "Reinforcement learning"
                              ? "var(--rl-glow)" : "var(--accent-glow)"
                          }`
                        : "none",
                    }}
                  >
                    {isActive && (
                      <span
                        className="absolute -left-[7px] top-2 bottom-2 w-[2px] rounded-full"
                        style={{ background: tint, boxShadow: `0 0 10px ${tint}` }}
                        aria-hidden="true"
                      />
                    )}
                    {item.icon}
                  </button>

                  {/* Hover label: a floating chip, so the rail never widens. */}
                  <span
                    className="pointer-events-none absolute left-[46px] top-1/2 -translate-y-1/2
                               whitespace-nowrap rounded-lg px-2.5 py-1.5 t-small z-50
                               opacity-0 group-hover:opacity-100 transition-opacity duration-150"
                    style={{
                      background: "var(--glass-strong)",
                      border: "1px solid var(--border-2)",
                      color: "var(--text-1)",
                      backdropFilter: "blur(10px)",
                      boxShadow: "var(--e3)",
                    }}
                    aria-hidden="true"
                  >
                    {item.label}
                  </span>
                </li>
              );
            })}
          </ul>
        </div>
      ))}

      <div className="flex-1" />
      <p
        className="t-micro text-center px-1 pb-1 select-none"
        style={{ color: "var(--text-4)" }}
        title="Telemetry is read from the running experiment's own files. The console issues no OpenFlow requests, so it cannot affect the controller-load measurement."
      >
        READ
        <br />
        ONLY
      </p>
    </aside>
  );
}

/** Brand glyph: concentric rings that spin only while frames are arriving. */
function ReactorMark({ live }: { live: boolean }) {
  return (
    <div className="relative w-8 h-8 shrink-0 grid place-items-center">
      <div
        className="absolute inset-0 rounded-full"
        style={{
          border: "1px solid var(--border-2)",
          background:
            "radial-gradient(circle at 50% 50%, rgba(34,228,255,0.18), transparent 70%)",
        }}
      />
      {live && (
        <div
          className="absolute inset-0 rounded-full"
          style={{
            border: "1px solid transparent",
            borderTopColor: "var(--accent)",
            borderRightColor: "var(--rl)",
            animation: "reactor-spin 2.8s linear infinite",
          }}
        />
      )}
      <svg
        width="15" height="15" viewBox="0 0 24 24" fill="none"
        stroke={live ? "var(--accent)" : "var(--text-3)"} strokeWidth="2.2"
        strokeLinecap="round" strokeLinejoin="round"
        style={{ filter: live ? "drop-shadow(0 0 6px var(--accent-glow))" : "none" }}
        aria-hidden="true"
      >
        <circle cx="5" cy="12" r="2" /><circle cx="19" cy="6" r="2" />
        <circle cx="19" cy="18" r="2" />
        <path d="M7 11.2 17 6.9M7 12.9l10 4.2" />
      </svg>
    </div>
  );
}

/** Live readouts in the top strip. Every value is measured telemetry from the
    running experiment; with nothing running they read as unavailable rather
    than zero, because zero is itself a measurement. */
function Readouts({ snapshot }: { snapshot?: LiveSnapshot | null }) {
  const active = Boolean(snapshot?.live?.active);
  const values = snapshot?.metrics?.values ?? {};
  const latency = snapshot?.latency;
  const decisions = snapshot?.decisions ?? [];
  const lastReward = decisions.length
    ? decisions[decisions.length - 1]?.reward
    : null;

  const items: { label: string; value: string; unit?: string; tone?: string }[] = [
    {
      label: "THRPT",
      value: active && values.delivered_bps != null
        ? fmtNumber((values.delivered_bps as number) / 1e6, 2) : "—",
      unit: "Mb/s",
    },
    {
      label: "RTT",
      value: latency?.available ? fmtNumber(latency.median_ms, 1) : "—",
      unit: "ms",
    },
    {
      label: "LOSS",
      value: latency?.available && latency.loss_fraction != null
        ? fmtNumber(latency.loss_fraction * 100, 1) : "—",
      unit: "%",
      tone: (latency?.loss_fraction ?? 0) > 0.05
        ? "var(--bad)"
        : (latency?.loss_fraction ?? 0) > 0.01 ? "var(--warn)" : undefined,
    },
    {
      label: "RULES",
      value: active && values.total_rules != null
        ? fmtNumber(values.total_rules as number, 0) : "—",
    },
    {
      label: "REWARD",
      value: lastReward != null ? fmtNumber(lastReward, 3) : "—",
      tone: lastReward != null
        ? (lastReward >= 0 ? "var(--ok)" : "var(--bad)")
        : undefined,
    },
  ];

  return (
    <div className="flex items-center gap-2.5 shrink-0">
      {items.map((it) => (
        <Readout key={it.label} {...it} />
      ))}
    </div>
  );
}

function Readout({
  label, value, unit, tone,
}: {
  label: string;
  value: string;
  unit?: string;
  tone?: string;
}) {
  // Remounting on change replays the flash, which marks the arrival of a new
  // measurement rather than running on a timer.
  const [tick, setTick] = useState(0);
  useEffect(() => setTick((t) => t + 1), [value]);
  const dim = value === "—";

  return (
    <div
      className="px-2 py-1 rounded-lg shrink-0 hidden md:block"
      style={{
        background: "rgba(10,18,31,0.55)",
        border: "1px solid var(--border-1)",
        minWidth: 72,
      }}
      title={label}
    >
      <p className="t-micro font-bold" style={{ color: "var(--text-4)" }}>
        {label}
      </p>
      <p className="readout text-[13px] leading-tight">
        <span
          key={tick}
          className={dim ? undefined : "value-flash"}
          style={{ color: dim ? "var(--text-4)" : (tone ?? "var(--text-1)") }}
        >
          {value}
        </span>
        {unit && !dim && (
          <span className="t-micro ml-1" style={{ color: "var(--text-4)" }}>
            {unit}
          </span>
        )}
      </p>
    </div>
  );
}

/** Stream health. The equaliser animates only while frames are genuinely
    arriving, so a frozen stream is visibly frozen. */
function StreamChip({
  state, lastUpdate, streaming, error, onReconnect,
}: {
  state: ConnectionState;
  lastUpdate: number | null;
  streaming: boolean;
  error: string | null;
  onReconnect: () => void;
}) {
  const map: Record<ConnectionState, { tone: "ok" | "warn" | "idle"; label: string }> = {
    open: { tone: "ok", label: "Live stream" },
    polling: { tone: "warn", label: "Polling" },
    connecting: { tone: "idle", label: "Connecting" },
    reconnecting: { tone: "warn", label: "Reconnecting" },
    closed: { tone: "idle", label: "Offline" },
  };
  const s = map[state];
  const stale = lastUpdate !== null && Date.now() - lastUpdate > 15000;

  return (
    <div
      className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg t-small shrink-0"
      style={{
        background: streaming ? "var(--ok-dim)" : "rgba(10,18,31,0.55)",
        border: `1px solid ${streaming ? "rgba(47,230,168,0.3)" : "var(--border-1)"}`,
        boxShadow: streaming ? "var(--glow-ok)" : "none",
      }}
      title={error ?? `Data stream: ${s.label}`}
    >
      {streaming ? (
        <span className="flex items-end gap-[2px] h-3" aria-hidden="true">
          {[0, 1, 2, 3].map((i) => (
            <span
              key={i}
              className="eq-bar w-[2px] rounded-full"
              style={{
                height: "100%",
                background: "var(--ok)",
                animationDelay: `${i * 140}ms`,
              }}
            />
          ))}
        </span>
      ) : (
        <StatusDot tone={stale ? "warn" : s.tone} />
      )}
      <span
        className="hidden sm:inline font-medium"
        style={{ color: streaming ? "var(--ok)" : "var(--text-2)" }}
      >
        {s.label}
      </span>
      {lastUpdate && (
        <span
          className="num hidden lg:inline t-micro"
          style={{ color: stale ? "var(--warn)" : "var(--text-4)" }}
        >
          {fmtAge((Date.now() - lastUpdate) / 1000)}
        </span>
      )}
      {(state === "reconnecting" || state === "closed") && (
        <button onClick={onReconnect} className="underline" style={{ color: "var(--accent)" }}>
          retry
        </button>
      )}
    </div>
  );
}

/** Bottom strip: the run's own event log, newest first. With no events it
    states the console's read-only guarantee instead of inventing activity. */
function Ticker({ snapshot }: { snapshot?: LiveSnapshot | null }) {
  const events = (snapshot?.events ?? []).slice(-14).reverse();

  return (
    <div
      className="chrome shrink-0 h-[26px] border-t flex items-center gap-3 px-3 overflow-hidden"
    >
      <span
        className="t-micro font-bold shrink-0"
        style={{ color: "var(--accent)" }}
      >
        ◂ FEED
      </span>
      <div className="flex-1 min-w-0 overflow-hidden">
        {events.length ? (
          <div className="flex items-center gap-5 whitespace-nowrap overflow-x-auto no-sb">
            {events.map((e, i) => (
              <span key={`${e.t}-${i}`} className="t-micro flex items-center gap-1.5">
                <span
                  className="w-1 h-1 rounded-full shrink-0"
                  style={{ background: "var(--accent)" }}
                />
                <span className="mono" style={{ color: "var(--text-4)" }}>
                  {e.kind}
                </span>
                <span style={{ color: "var(--text-2)" }}>{e.detail}</span>
              </span>
            ))}
          </div>
        ) : (
          <span className="t-micro" style={{ color: "var(--text-4)" }}>
            No events recorded. Telemetry is read from the running
            experiment&rsquo;s own files — the console issues no OpenFlow
            requests, so it cannot affect the controller-load measurement.
          </span>
        )}
      </div>
    </div>
  );
}
