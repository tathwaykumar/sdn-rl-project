/** Console chrome: a conventional top app bar and horizontal section nav.
 *
 * The console used to be framed like an instrument panel: a fixed icon rail
 * down the left edge, a continuously-updating strip across the top, and a
 * running ticker along the bottom. That cost every page a rail's worth of
 * width and put navigation in an unfamiliar place. This is an ordinary web
 * app layout instead: a header (brand, live readouts, stream health) and a
 * horizontal row of section tabs beneath it, both full width; the page below
 * gets the entire window, capped at `--measure` for readability.
 *
 * Connection state and data provenance are still always visible. A console
 * that keeps rendering the last frame after its stream dies is worse than one
 * that shows nothing, because stale numbers look exactly like current ones.
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
  group: "Operations" | "Reinforcement learning" | "Runs";
  icon: ReactNode;
  hint: string;
}

/** Inline SVG icons: no icon-font dependency, and they inherit currentColor. */
const icon = (path: ReactNode) => (
  <svg
    width="16" height="16" viewBox="0 0 24 24" fill="none"
    stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"
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
    id: "experiments", label: "Experiments", group: "Runs",
    hint: "Start, stop and monitor runs",
    icon: icon(<><path d="M9 3h6M10 3v6.2L5.3 18a2 2 0 0 0 1.7 3h10a2 2 0 0 0 1.7-3L14 9.2V3" /></>),
  },
  {
    id: "comparison", label: "A/B/C Comparison", group: "Runs",
    hint: "Did the agent beat fixed routing, and is the difference real?",
    icon: icon(<><path d="M4 20V9M12 20V4M20 20v-7" /><path d="M2 20h20" /></>),
  },
  {
    id: "history", label: "History", group: "Runs",
    hint: "Which runs produced these findings, and can they be traced?",
    icon: icon(<><path d="M3 12a9 9 0 1 0 2.6-6.4" /><path d="M3 4v5h5" /><path d="M12 7.5V12l3 1.8" /></>),
  },
];

const GROUPS: NavItem["group"][] = [
  "Operations", "Reinforcement learning", "Runs",
];

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
            borderBottom: "1px solid color-mix(in srgb, var(--warn) 32%, transparent)",
          }}
        >
          MOCK MODE — every value shown is synthetic demo data, not a
          measurement of any network.
        </div>
      )}

      {/* App bar: brand, live readouts, provenance and stream health. */}
      <header
        className="chrome shrink-0 flex items-center gap-4 px-4 md:px-7 h-[60px]
                   border-b overflow-x-auto no-sb"
      >
        <div className="flex items-center gap-2.5 shrink-0">
          <ReactorMark live={streaming} />
          <div className="leading-none hidden sm:block">
            <p className="t-h1 whitespace-nowrap" style={{ color: "var(--text-1)" }}>
              SDN · RL Console
            </p>
            <p className="t-micro mt-1" style={{ color: "var(--text-4)" }}>
              NETWORK OPS &amp; RL ANALYTICS
            </p>
          </div>
        </div>

        <div
          className="h-8 w-px shrink-0 hidden md:block"
          style={{ background: "var(--border-1)" }}
        />

        <Readouts snapshot={snapshot} />

        <div className="flex-1 min-w-[0.5rem]" />

        <ReadOnlyBadge />
        <div className="flex items-center gap-2 shrink-0">
          {runBadge}
          <StreamChip
            state={connection} lastUpdate={lastUpdate} streaming={streaming}
            error={connectionError} onReconnect={onReconnect}
          />
        </div>
      </header>

      {/* Section nav: a conventional horizontal tab row, grouped, in place of
          the old left-hand rail. Every page lives in exactly one group, shown
          as a caption in front of its tabs rather than as a separate heading
          — the groups sit close enough together that a disconnected label
          above them would read as a fourth group. */}
      <nav
        className="chrome shrink-0 flex items-center gap-5 px-4 md:px-7 h-[46px]
                   border-b overflow-x-auto no-sb"
        aria-label="Sections"
      >
        {GROUPS.map((group, gi) => (
          <div key={group} className="flex items-center gap-2 shrink-0">
            {gi > 0 && (
              <span
                className="h-5 w-px shrink-0 mr-3"
                style={{ background: "var(--border-1)" }}
                aria-hidden="true"
              />
            )}
            <span
              className="t-micro font-bold shrink-0 select-none"
              style={{ color: "var(--text-4)", letterSpacing: "0.14em" }}
              aria-hidden="true"
            >
              {group.toUpperCase()}
            </span>
            <ul className="flex items-center gap-1" aria-label={group}>
              {NAV_ITEMS.filter((i) => i.group === group).map((item) => {
                const isActive = item.id === page;
                const tint = group === "Reinforcement learning"
                  ? "var(--rl)" : "var(--accent)";
                return (
                  <li key={item.id}>
                    <button
                      onClick={() => onNavigate(item.id)}
                      aria-current={isActive ? "page" : undefined}
                      className={cx(
                        "h-8 px-3 rounded-[7px] flex items-center gap-1.5 whitespace-nowrap",
                        "t-small font-medium transition-colors duration-150",
                        !isActive && "hover:bg-[var(--hover-wash)]",
                      )}
                      style={{
                        background: isActive
                          ? (group === "Reinforcement learning"
                              ? "var(--rl-dim)" : "var(--accent-dim)")
                          : "transparent",
                        color: isActive ? tint : "var(--text-2)",
                        fontWeight: isActive ? 600 : 500,
                      }}
                    >
                      <span className="shrink-0">{item.icon}</span>
                      {item.label}
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </nav>

      {/* Content: an ordinary padded page column, capped at `--measure` so a
          table row never puts its label and its value a hand-span apart on a
          wide display. */}
      <main className="flex-1 overflow-auto min-h-0 px-4 md:px-7 pb-10">
        <div className="measure">
          <div className="pt-7 pb-6">
            <p
              className="t-label mb-1.5"
              style={{
                color: active?.group === "Reinforcement learning"
                  ? "var(--rl)" : "var(--accent)",
              }}
            >
              {active?.group ?? "Console"}
            </p>
            <h1 className="t-hero truncate">
              {active?.label ?? "Console"}
            </h1>
            <p className="t-body mt-2 max-w-2xl" style={{ color: "var(--text-3)" }}>
              {active?.hint}
            </p>
          </div>
          {children}
        </div>
      </main>
    </div>
  );
}

/** Trust signal that used to anchor the bottom of the rail. Compact chip in
    the app bar now that there is no rail to anchor it to. */
function ReadOnlyBadge() {
  return (
    <span
      className="hidden lg:inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg shrink-0"
      style={{ background: "var(--surface-2)", border: "1px solid var(--border-1)" }}
      title="Telemetry is read from the running experiment's own files. The console issues no OpenFlow requests, so it cannot affect the controller-load measurement."
    >
      <svg
        width="13" height="13" viewBox="0 0 24 24" fill="none"
        stroke="var(--ok)" strokeWidth="1.8" strokeLinecap="round"
        strokeLinejoin="round" className="shrink-0" aria-hidden="true"
      >
        <path d="M12 3 4 6.2v5.1c0 4.5 3.4 8.7 8 9.7 4.6-1 8-5.2 8-9.7V6.2Z" />
        <path d="m9 12 2 2 4-4" />
      </svg>
      <span className="t-micro font-bold" style={{ color: "var(--ok)" }}>
        READ ONLY
      </span>
    </span>
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
            "radial-gradient(circle at 50% 50%, var(--accent-dim), transparent 70%)",
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
        aria-hidden="true"
      >
        <circle cx="5" cy="12" r="2" /><circle cx="19" cy="6" r="2" />
        <circle cx="19" cy="18" r="2" />
        <path d="M7 11.2 17 6.9M7 12.9l10 4.2" />
      </svg>
    </div>
  );
}

/** Live readouts in the app bar. Every value is measured telemetry from the
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
      className="px-2.5 py-1.5 rounded-[10px] shrink-0 hidden md:block"
      style={{
        background: "var(--surface-2)",
        border: "1px solid var(--border-1)",
        minWidth: 78,
      }}
      title={label}
    >
      <p className="t-micro font-bold" style={{ color: "var(--text-4)" }}>
        {label}
      </p>
      <p className="readout text-[15px] leading-tight mt-0.5">
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
        background: streaming ? "var(--ok-dim)" : "var(--surface-2)",
        border: `1px solid ${
          streaming ? "color-mix(in srgb, var(--ok) 32%, transparent)" : "var(--border-1)"
        }`,
        boxShadow: streaming ? "var(--ring-ok)" : "none",
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
