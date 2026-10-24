/** Shared presentation primitives.
 *
 * `SourceBadge` is the most important thing in this file: every panel that
 * shows data must say where it came from. Mock data is rendered in a warning
 * colour with an explicit label so it can never be mistaken for a measurement.
 */
import type { ReactNode } from "react";
import type { DataSource } from "../api/types";

export const VARIANT_COLOR: Record<string, string> = {
  A: "var(--variant-a)",
  B: "var(--variant-b)",
  C: "var(--variant-c)",
};

export const VARIANT_LABEL: Record<string, string> = {
  A: "Conventional routing + fixed timeouts",
  B: "RL routing + fixed timeouts",
  C: "Joint RL routing + RL timeouts",
};

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

// ---------------------------------------------------------------- formatting
export function fmtNumber(
  value: number | null | undefined,
  digits = 2,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return value.toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function fmtBits(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  if (value >= 1e9) return `${fmtNumber(value / 1e9, 2)} Gbit/s`;
  if (value >= 1e6) return `${fmtNumber(value / 1e6, 2)} Mbit/s`;
  if (value >= 1e3) return `${fmtNumber(value / 1e3, 1)} kbit/s`;
  return `${fmtNumber(value, 0)} bit/s`;
}

export function fmtPercent(
  value: number | null | undefined,
  digits = 1,
): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${fmtNumber(value * 100, digits)}%`;
}

export function fmtBytes(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  const units = ["B", "KB", "MB", "GB"];
  let v = value;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${fmtNumber(v, i === 0 ? 0 : 1)} ${units[i]}`;
}

export function fmtTime(epoch: number | null | undefined): string {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleTimeString();
}

export function fmtDateTime(epoch: number | null | undefined): string {
  if (!epoch) return "—";
  return new Date(epoch * 1000).toLocaleString();
}

export function fmtAge(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "age unknown";
  if (seconds < 1.5) return "just now";
  if (seconds < 60) return `${seconds.toFixed(0)}s ago`;
  return `${(seconds / 60).toFixed(1)} min ago`;
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  return m > 0 ? `${m}m ${s}s` : `${s}s`;
}

// -------------------------------------------------------------------- layout
export function Card({
  title,
  subtitle,
  right,
  children,
  className,
  bodyClassName,
  accent,
  raised,
  icon,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
  /** Domain rail on the leading edge: networking or reinforcement learning. */
  accent?: "net" | "rl";
  raised?: boolean;
  icon?: ReactNode;
}) {
  return (
    <section
      className={cx(
        "panel overflow-hidden flex flex-col",
        raised && "panel-raised",
        accent === "net" && "rail-accent",
        accent === "rl" && "rail-rl",
        className,
      )}
    >
      {(title || right) && (
        <header
          className="flex items-start justify-between gap-3 px-4 py-3 border-b"
          style={{ borderColor: "var(--hairline)" }}
        >
          <div className="min-w-0 flex items-start gap-2.5">
            {icon && (
              <span
                className="shrink-0 mt-0.5"
                style={{ color: accent === "rl" ? "var(--rl)" : "var(--accent)" }}
                aria-hidden="true"
              >
                {icon}
              </span>
            )}
            <div className="min-w-0">
              {title && <h2 className="t-h2">{title}</h2>}
              {subtitle && (
                <p className="t-small mt-0.5" style={{ color: "var(--text-3)" }}>
                  {subtitle}
                </p>
              )}
            </div>
          </div>
          {right && <div className="shrink-0">{right}</div>}
        </header>
      )}
      <div className={cx("p-4 flex-1 min-h-0", bodyClassName)}>{children}</div>
    </section>
  );
}

/** A page-level section heading, for grouping panels without nesting cards. */
export function SectionTitle({
  children,
  right,
  hint,
}: {
  children: ReactNode;
  right?: ReactNode;
  hint?: string;
}) {
  return (
    <div className="flex items-end justify-between gap-3 mb-2.5 mt-1">
      <div className="min-w-0">
        <h2 className="t-label" style={{ color: "var(--text-3)" }}>
          {children}
        </h2>
        {hint && (
          <p className="t-small mt-0.5" style={{ color: "var(--text-4)" }}>
            {hint}
          </p>
        )}
      </div>
      {right}
    </div>
  );
}

/** Large headline metric for the Overview. Bigger type, more breathing room. */
export function HeroMetric({
  label,
  value,
  unit,
  sub,
  tone,
  direction,
  source,
  spark,
  explain,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  sub?: ReactNode;
  tone?: "ok" | "warn" | "bad";
  direction?: string;
  source?: DataSource | string | null;
  /** Normalised 0..1 values for a background sparkline. */
  spark?: number[];
  explain?: string;
}) {
  const unavailable = value === "N/A" || value === "—";
  const color = unavailable
    ? "var(--text-4)"
    : tone === "bad"
      ? "var(--bad)"
      : tone === "warn"
        ? "var(--warn)"
        : "var(--text-1)";

  return (
    <div
      className="panel panel-hover relative overflow-hidden px-4 pt-3.5 pb-3 flex flex-col gap-1"
      title={explain}
    >
      {spark && spark.length > 1 && (
        <Sparkline values={spark} tone={tone} />
      )}
      <div className="flex items-start justify-between gap-2 relative">
        <span className="t-small font-medium truncate" style={{ color: "var(--text-2)" }}>
          {label}
        </span>
        {source && <SourceBadge source={source} />}
      </div>
      <div className="flex items-baseline gap-1.5 relative">
        <span
          className="num leading-none"
          style={{ color, fontSize: 30, fontWeight: 640, letterSpacing: "-0.025em" }}
        >
          {value}
        </span>
        {unit && !unavailable && (
          <span className="t-small" style={{ color: "var(--text-3)" }}>
            {unit}
          </span>
        )}
      </div>
      <div className="flex items-center justify-between gap-2 relative mt-auto pt-1">
        <span className="t-small truncate" style={{ color: "var(--text-3)" }}>
          {sub}
        </span>
        <DirectionHint direction={direction} />
      </div>
    </div>
  );
}

/** Background sparkline. Purely supportive: the number carries the meaning. */
function Sparkline({ values, tone }: { values: number[]; tone?: string }) {
  const max = Math.max(...values, 0.0001);
  const pts = values
    .map((v, i) => `${(i / (values.length - 1)) * 100},${34 - (v / max) * 30}`)
    .join(" ");
  const stroke =
    tone === "bad" ? "var(--bad)" : tone === "warn" ? "var(--warn)" : "var(--accent)";
  return (
    <svg
      className="absolute inset-x-0 bottom-0 w-full pointer-events-none"
      height="38" viewBox="0 0 100 38" preserveAspectRatio="none"
      aria-hidden="true"
    >
      <polyline points={pts} fill="none" stroke={stroke} strokeWidth="1.2"
                opacity="0.32" vectorEffect="non-scaling-stroke" />
      <polyline points={`0,38 ${pts} 100,38`} fill={stroke} opacity="0.06"
                stroke="none" />
    </svg>
  );
}

// -------------------------------------------------------------- source badge
const SOURCE_STYLE: Record<
  DataSource,
  { label: string; bg: string; fg: string; title: string }
> = {
  live: {
    label: "LIVE",
    bg: "rgba(46,163,107,0.15)",
    fg: "#43c98a",
    title: "Measured from the experiment running right now.",
  },
  historical: {
    label: "RECORDED",
    bg: "rgba(57,135,229,0.15)",
    fg: "#5f9fe8",
    title: "Read from a completed run's stored measurements.",
  },
  config: {
    label: "CONFIG",
    bg: "rgba(163,176,191,0.15)",
    fg: "#a3b0bf",
    title: "Static project configuration, not a measurement.",
  },
  mock: {
    label: "MOCK DATA",
    bg: "rgba(201,133,0,0.2)",
    fg: "#e0a93a",
    title: "Synthetic demo data. NOT a measurement of any network.",
  },
  unavailable: {
    label: "UNAVAILABLE",
    bg: "rgba(111,127,144,0.15)",
    fg: "#8b9aab",
    title: "Not measurable right now.",
  },
};

export function SourceBadge({
  source,
  age,
  stale,
}: {
  source?: DataSource | string | null;
  age?: number | null;
  stale?: boolean;
}) {
  const key = (source ?? "unavailable") as DataSource;
  const style = SOURCE_STYLE[key] ?? SOURCE_STYLE.unavailable;
  return (
    <span className="inline-flex items-center gap-1.5" title={style.title}>
      <span
        className="px-1.5 py-0.5 rounded text-[10px] font-semibold tracking-wider"
        style={{ background: style.bg, color: style.fg }}
      >
        {style.label}
      </span>
      {age !== undefined && age !== null && (
        <span
          className="text-[10px] num"
          style={{ color: stale ? "var(--warn)" : "var(--text-3)" }}
        >
          {stale ? `stale · ${fmtAge(age)}` : fmtAge(age)}
        </span>
      )}
    </span>
  );
}

export function StatusDot({
  tone,
  pulse,
}: {
  tone: "ok" | "warn" | "bad" | "idle";
  pulse?: boolean;
}) {
  const color = {
    ok: "var(--ok)",
    warn: "var(--warn)",
    bad: "var(--bad)",
    idle: "var(--idle)",
  }[tone];
  return (
    <span
      className={cx("inline-block w-2 h-2 rounded-full", pulse && "pulse-soft")}
      style={{ background: color }}
    />
  );
}

// ------------------------------------------------------------------- states
export function Empty({
  title,
  detail,
  action,
}: {
  title: string;
  detail?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-10 px-6 gap-2">
      <div
        className="w-9 h-9 rounded-lg border flex items-center justify-center text-base"
        style={{ borderColor: "var(--border-2)", color: "var(--text-3)" }}
      >
        ∅
      </div>
      <p className="text-[13px] font-medium">{title}</p>
      {detail && (
        <p
          className="text-[12px] max-w-md leading-relaxed"
          style={{ color: "var(--text-3)" }}
        >
          {detail}
        </p>
      )}
      {action}
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div
      className="rounded-lg border px-4 py-3 text-[12px] flex items-start gap-3"
      style={{
        borderColor: "rgba(224,82,82,0.35)",
        background: "rgba(224,82,82,0.08)",
        color: "#f0a3a3",
      }}
    >
      <span className="mt-0.5">⚠</span>
      <div className="flex-1 min-w-0">
        <p className="font-medium text-[12px]">Something went wrong</p>
        <p className="mt-0.5 leading-relaxed break-words">{message}</p>
      </div>
      {onRetry && (
        <button
          onClick={onRetry}
          className="shrink-0 px-2 py-1 rounded border text-[11px]"
          style={{ borderColor: "rgba(224,82,82,0.4)", color: "#f0a3a3" }}
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2 animate-pulse" aria-label="loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="h-3 rounded"
          style={{
            background: "var(--surface-3)",
            width: `${70 + ((i * 13) % 25)}%`,
          }}
        />
      ))}
    </div>
  );
}

// ------------------------------------------------------------- metric card
export function MetricCard({
  label,
  value,
  unit,
  sub,
  kind,
  source,
  tone,
  direction,
  explain,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  sub?: ReactNode;
  kind?: string;
  source?: DataSource | string | null;
  tone?: "ok" | "warn" | "bad";
  /** Which way is desirable, shown so a reader need not guess. */
  direction?: string;
  /** One-line plain-English meaning, surfaced as a tooltip. */
  explain?: string;
}) {
  const unavailable =
    value === "N/A" || value === "—" || source === "unavailable";
  const color = unavailable
    ? "var(--text-4)"
    : tone === "bad"
      ? "var(--bad)"
      : tone === "warn"
        ? "var(--warn)"
        : "var(--text-1)";
  return (
    <div className="panel panel-hover px-4 py-3 flex flex-col gap-1">
      <div className="flex items-center justify-between gap-2">
        <span
          className="text-[11px] font-medium truncate"
          style={{ color: "var(--text-2)" }}
          title={explain ?? label}
        >
          {label}
        </span>
        {source && <SourceBadge source={source} />}
      </div>
      <div className="flex items-baseline gap-1.5">
        <span className="text-2xl font-semibold num leading-tight" style={{ color }}>
          {value}
        </span>
        {unit && !unavailable && (
          <span className="text-[11px]" style={{ color: "var(--text-3)" }}>
            {unit}
          </span>
        )}
      </div>
      <div className="flex items-center justify-between gap-2 mt-auto pt-0.5">
        <span className="text-[11px] truncate" style={{ color: "var(--text-3)" }}>
          {sub}
        </span>
        <span className="flex items-center gap-1.5 shrink-0">
          <DirectionHint direction={direction} />
          {kind && (
            <span
              className="text-[9.5px] px-1 py-0.5 rounded"
              style={{ background: "var(--surface-3)", color: "var(--text-4)" }}
              title={
                kind === "instantaneous"
                  ? "A single reading at one instant."
                  : kind === "interval_count"
                    ? "A count accumulated over the polling interval."
                    : kind === "event"
                      ? "Measured when a specific event occurs."
                      : "A rate averaged over the polling interval."
              }
            >
              {kind === "instantaneous" ? "instant"
                : kind === "event" ? "event" : "interval"}
            </span>
          )}
        </span>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ controls
export function Button({
  children,
  onClick,
  variant = "default",
  disabled,
  title,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  variant?: "default" | "primary" | "danger" | "ghost";
  disabled?: boolean;
  title?: string;
  type?: "button" | "submit";
}) {
  const styles: Record<string, { bg: string; fg: string; border: string }> = {
    default: { bg: "var(--surface-2)", fg: "var(--text-1)", border: "var(--border-2)" },
    primary: { bg: "#1f5fae", fg: "#ffffff", border: "#2a73c9" },
    danger: { bg: "rgba(224,82,82,0.15)", fg: "#f0a3a3", border: "rgba(224,82,82,0.4)" },
    ghost: { bg: "transparent", fg: "var(--text-2)", border: "var(--border-1)" },
  };
  const s = styles[variant];
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      title={title}
      className={cx(
        "px-3 py-1.5 rounded-lg border text-[12px] font-medium transition-opacity",
        disabled ? "opacity-40 cursor-not-allowed" : "hover:opacity-85",
      )}
      style={{ background: s.bg, color: s.fg, borderColor: s.border }}
    >
      {children}
    </button>
  );
}

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] font-medium" style={{ color: "var(--text-2)" }}>
        {label}
      </span>
      {children}
      {hint && (
        <span className="text-[10px] leading-snug" style={{ color: "var(--text-3)" }}>
          {hint}
        </span>
      )}
    </label>
  );
}

export const inputStyle = {
  background: "var(--surface-2)",
  borderColor: "var(--border-2)",
  color: "var(--text-1)",
};

export const inputClass =
  "px-2.5 py-1.5 rounded-lg border text-[12px] outline-none focus:ring-1 focus:ring-sky-600";

/** Collapsible technical detail.
 *
 * Keeps the main view readable for someone meeting the project for the first
 * time while leaving the exact mechanics one click away. Uses native
 * <details> so it is keyboard accessible and works without JavaScript state.
 */
export function Details({
  summary,
  children,
  defaultOpen,
}: {
  summary: string;
  children: ReactNode;
  defaultOpen?: boolean;
}) {
  return (
    <details className="mt-2 group" open={defaultOpen}>
      <summary
        className="cursor-pointer text-[11px] font-medium select-none list-none
                   inline-flex items-center gap-1.5 rounded px-1 -mx-1 py-0.5"
        style={{ color: "var(--accent)" }}
      >
        <svg
          width="10" height="10" viewBox="0 0 24 24" fill="none"
          stroke="currentColor" strokeWidth="3" strokeLinecap="round"
          strokeLinejoin="round"
          className="transition-transform group-open:rotate-90"
          aria-hidden="true"
        >
          <path d="M9 6l6 6-6 6" />
        </svg>
        {summary}
      </summary>
      <div
        className="mt-2 text-[11px] leading-relaxed rounded-lg border px-3 py-2"
        style={{
          color: "var(--text-2)",
          borderColor: "var(--border-1)",
          background: "var(--surface-0)",
        }}
      >
        {children}
      </div>
    </details>
  );
}

/** A compact label explaining which direction of a metric is desirable. */
export function DirectionHint({ direction }: { direction?: string }) {
  if (!direction || direction === "context") {
    return (
      <span className="text-[10px]" style={{ color: "var(--text-4)" }}
            title="Neither direction is automatically better; it depends on context.">
        context
      </span>
    );
  }
  const higher = direction === "higher_is_better";
  return (
    <span
      className="text-[10px] inline-flex items-center gap-0.5"
      style={{ color: "var(--text-4)" }}
      title={higher ? "Higher is better" : "Lower is better"}
    >
      {higher ? "↑" : "↓"} better
    </span>
  );
}

export function Pill({
  children,
  color,
  title,
}: {
  children: ReactNode;
  color?: string;
  title?: string;
}) {
  return (
    <span
      className="px-1.5 py-0.5 rounded text-[10px] font-medium"
      title={title}
      style={{
        background: color ? `${color}22` : "var(--surface-3)",
        color: color ?? "var(--text-2)",
      }}
    >
      {children}
    </span>
  );
}
