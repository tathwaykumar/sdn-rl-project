/** Application shell: sidebar navigation, page header, status rail.
 *
 * The sidebar collapses to a rail on narrow screens and to a slide-over on
 * phones, so the console is usable on a laptop and on mobile without a second
 * layout to maintain.
 *
 * Connection state and data provenance live in the header and are always
 * visible. A console that keeps rendering the last frame after its stream
 * dies is worse than one that shows nothing, because stale numbers look
 * exactly like current ones.
 */
import { useEffect, useState, type ReactNode } from "react";
import type { ConnectionState } from "../hooks/useLive";
import { StatusDot, cx, fmtAge } from "./ui";

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
    hint: "System state at a glance",
    icon: icon(<><rect x="3" y="3" width="7" height="9" rx="1.5" /><rect x="14" y="3" width="7" height="5" rx="1.5" /><rect x="14" y="12" width="7" height="9" rx="1.5" /><rect x="3" y="16" width="7" height="5" rx="1.5" /></>),
  },
  {
    id: "topology", label: "Live Topology", group: "Operations",
    hint: "Switches, links and confirmed paths",
    icon: icon(<><circle cx="5" cy="12" r="2.4" /><circle cx="19" cy="6" r="2.4" /><circle cx="19" cy="18" r="2.4" /><path d="M7.2 11 16.8 6.8M7.2 13l9.6 4.2" /></>),
  },
  {
    id: "metrics", label: "Metrics & Analytics", group: "Operations",
    hint: "Throughput, latency, loss, flow tables",
    icon: icon(<><path d="M3 20h18" /><rect x="5" y="11" width="3.4" height="6" rx="1" /><rect x="10.3" y="7" width="3.4" height="10" rx="1" /><rect x="15.6" y="13" width="3.4" height="4" rx="1" /></>),
  },
  {
    id: "reward", label: "Reward Explorer", group: "Reinforcement learning",
    hint: "How the agent's score is built",
    icon: icon(<><circle cx="12" cy="9" r="5" /><path d="M8.5 13.5 7 21l5-2.6L17 21l-1.5-7.5" /></>),
  },
  {
    id: "decisions", label: "Decision Inspector", group: "Reinforcement learning",
    hint: "What the agent chose, and whether switches agreed",
    icon: icon(<><path d="M12 3v6M12 15v6M5.6 7.6l4.2 4.2M14.2 12.2l4.2 4.2" /><circle cx="12" cy="12" r="2.6" /></>),
  },
  {
    id: "experiments", label: "Experiments", group: "Experiments",
    hint: "Start, stop and monitor runs",
    icon: icon(<><path d="M9 3h6M10 3v6.2L5.3 18a2 2 0 0 0 1.7 3h10a2 2 0 0 0 1.7-3L14 9.2V3" /></>),
  },
  {
    id: "comparison", label: "A/B/C Comparison", group: "Experiments",
    hint: "Variant results with uncertainty",
    icon: icon(<><path d="M4 20V9M12 20V4M20 20v-7" /><path d="M2 20h20" /></>),
  },
  {
    id: "history", label: "History", group: "Experiments",
    hint: "Every run, with provenance and exports",
    icon: icon(<><path d="M3 12a9 9 0 1 0 2.6-6.4" /><path d="M3 4v5h5" /><path d="M12 7.5V12l3 1.8" /></>),
  },
];

const GROUPS: NavItem["group"][] = [
  "Operations", "Reinforcement learning", "Experiments",
];

export function Shell({
  page, onNavigate, connection, lastUpdate, connectionError, onReconnect,
  mock, runBadge, children,
}: {
  page: PageId;
  onNavigate: (page: PageId) => void;
  connection: ConnectionState;
  lastUpdate: number | null;
  connectionError: string | null;
  onReconnect: () => void;
  mock: boolean;
  runBadge: ReactNode;
  children: ReactNode;
}) {
  const [menuOpen, setMenuOpen] = useState(false);
  const active = NAV_ITEMS.find((i) => i.id === page);

  // Close the slide-over on navigation and on Escape.
  useEffect(() => setMenuOpen(false), [page]);
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  return (
    <div className="h-full flex flex-col">
      {mock && (
        <div
          role="status"
          className="px-4 py-1.5 text-[11px] font-semibold text-center tracking-wide"
          style={{ background: "var(--warn-dim)", color: "var(--warn)" }}
        >
          MOCK MODE — every value shown is synthetic demo data, not a
          measurement of any network.
        </div>
      )}

      <div className="flex-1 flex min-h-0">
        {/* Sidebar: rail on md, full on xl, slide-over on mobile */}
        <aside
          className={cx(
            "shrink-0 flex-col border-r z-30",
            "hidden md:flex md:w-[68px] xl:w-[244px]",
          )}
          style={{ background: "var(--surface-1)", borderColor: "var(--border-1)" }}
        >
          <Brand compact />
          <Nav page={page} onNavigate={onNavigate} />
          <SidebarFooter />
        </aside>

        {menuOpen && (
          <>
            <div
              className="fixed inset-0 z-40 md:hidden"
              style={{ background: "rgba(3,7,12,0.7)" }}
              onClick={() => setMenuOpen(false)}
            />
            <aside
              className="fixed inset-y-0 left-0 z-50 w-[252px] flex flex-col border-r md:hidden rise"
              style={{ background: "var(--surface-1)", borderColor: "var(--border-1)" }}
            >
              <Brand />
              <Nav page={page} onNavigate={onNavigate} expanded />
              <SidebarFooter />
            </aside>
          </>
        )}

        <div className="flex-1 flex flex-col min-w-0">
          <header
            className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5
                       border-b shrink-0"
            style={{ background: "var(--surface-1)", borderColor: "var(--border-1)" }}
          >
            <button
              className="md:hidden p-1.5 rounded-lg border"
              style={{ borderColor: "var(--border-2)", color: "var(--text-2)" }}
              onClick={() => setMenuOpen(true)}
              aria-label="Open navigation"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none"
                   stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M4 7h16M4 12h16M4 17h16" />
              </svg>
            </button>

            <div className="min-w-0">
              <h1 className="text-[14px] font-semibold leading-tight truncate">
                {active?.label ?? "Dashboard"}
              </h1>
              <p className="text-[11px] truncate" style={{ color: "var(--text-3)" }}>
                {active?.hint}
              </p>
            </div>

            <div className="flex-1 min-w-[1rem]" />
            {/* Rendered once; the header wraps on narrow screens rather than
                duplicating this for mobile, which would repeat the content
                for screen readers. */}
            {runBadge}
            <ConnectionChip
              state={connection} lastUpdate={lastUpdate}
              error={connectionError} onReconnect={onReconnect}
            />
          </header>

          <main className="flex-1 overflow-auto p-4 md:p-5 min-h-0">
            <div className="mx-auto w-full max-w-[1600px]">{children}</div>
          </main>
        </div>
      </div>
    </div>
  );
}

function Brand({ compact }: { compact?: boolean }) {
  return (
    <div
      className="flex items-center gap-2.5 px-4 h-[52px] border-b shrink-0"
      style={{ borderColor: "var(--border-1)" }}
    >
      <div
        className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0"
        style={{
          background: "linear-gradient(135deg, var(--accent), var(--accent-strong))",
          boxShadow: "0 2px 10px rgba(34,211,238,0.3)",
        }}
        aria-hidden="true"
      >
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none"
             stroke="#06141c" strokeWidth="2.4" strokeLinecap="round"
             strokeLinejoin="round">
          <circle cx="5" cy="12" r="2" /><circle cx="19" cy="6" r="2" />
          <circle cx="19" cy="18" r="2" />
          <path d="M7 11.2 17 6.9M7 12.9l10 4.2" />
        </svg>
      </div>
      <div className={cx("min-w-0", compact && "hidden xl:block")}>
        <p className="text-[12.5px] font-semibold leading-tight truncate">
          SDN &middot; RL Console
        </p>
        <p className="text-[10px] leading-tight truncate" style={{ color: "var(--text-4)" }}>
          Network ops &amp; RL analytics
        </p>
      </div>
    </div>
  );
}

function Nav({
  page, onNavigate, expanded,
}: {
  page: PageId;
  onNavigate: (page: PageId) => void;
  expanded?: boolean;
}) {
  return (
    <nav className="flex-1 overflow-y-auto py-3" aria-label="Sections">
      {GROUPS.map((group) => (
        <div key={group} className="mb-4">
          <p
            className={cx(
              "px-4 mb-1.5 text-[9.5px] font-semibold uppercase tracking-[0.09em]",
              expanded ? "block" : "hidden xl:block",
            )}
            style={{ color: "var(--text-4)" }}
          >
            {group}
          </p>
          <ul className="space-y-0.5 px-2">
            {NAV_ITEMS.filter((i) => i.group === group).map((item) => {
              const isActive = item.id === page;
              return (
                <li key={item.id}>
                  <button
                    onClick={() => onNavigate(item.id)}
                    aria-current={isActive ? "page" : undefined}
                    title={item.label}
                    className={cx(
                      "w-full flex items-center gap-2.5 rounded-lg px-2.5 py-2",
                      "text-[12.5px] font-medium transition-colors relative",
                      expanded ? "justify-start" : "justify-center xl:justify-start",
                    )}
                    style={{
                      background: isActive ? "var(--accent-dim)" : "transparent",
                      color: isActive ? "var(--accent)" : "var(--text-2)",
                    }}
                  >
                    {isActive && (
                      <span
                        className="absolute left-0 top-1.5 bottom-1.5 w-[2.5px] rounded-r"
                        style={{ background: "var(--accent)" }}
                        aria-hidden="true"
                      />
                    )}
                    <span className="shrink-0">{item.icon}</span>
                    <span className={cx("truncate", expanded ? "block" : "hidden xl:block")}>
                      {item.label}
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </div>
      ))}
    </nav>
  );
}

function SidebarFooter() {
  return (
    <div
      className="px-4 py-3 border-t text-[10px] leading-relaxed hidden xl:block shrink-0"
      style={{ borderColor: "var(--border-1)", color: "var(--text-4)" }}
    >
      Telemetry is read from the running experiment&rsquo;s own files. The
      console issues no OpenFlow requests, so it cannot affect the
      controller-load measurement.
    </div>
  );
}

function ConnectionChip({
  state, lastUpdate, error, onReconnect,
}: {
  state: ConnectionState;
  lastUpdate: number | null;
  error: string | null;
  onReconnect: () => void;
}) {
  const map: Record<ConnectionState, { tone: "ok" | "warn" | "idle"; label: string }> = {
    open: { tone: "ok", label: "Live" },
    polling: { tone: "warn", label: "Polling" },
    connecting: { tone: "idle", label: "Connecting" },
    reconnecting: { tone: "warn", label: "Reconnecting" },
    closed: { tone: "idle", label: "Offline" },
  };
  const s = map[state];
  const stale = lastUpdate !== null && Date.now() - lastUpdate > 15000;

  return (
    <div
      className="flex items-center gap-2 px-2.5 py-1.5 rounded-lg border text-[11px] shrink-0"
      style={{ background: "var(--surface-2)", borderColor: "var(--border-1)" }}
      title={error ?? `Data stream: ${s.label}`}
    >
      <StatusDot tone={stale ? "warn" : s.tone} pulse={state === "open" && !stale} />
      <span style={{ color: "var(--text-2)" }} className="hidden sm:inline">
        {s.label}
      </span>
      {lastUpdate && (
        <span className="num hidden md:inline" style={{ color: stale ? "var(--warn)" : "var(--text-4)" }}>
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
