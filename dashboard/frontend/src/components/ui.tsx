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

/** Colour for the network's two link-disjoint forwarding paths. Deliberately
    not the variant colours: "Path A/B" and "Variant A/B/C" are two unrelated
    axes, and sharing a palette between them read as a connection that is not
    there. Kept within the accent's own family (a brighter and a muted shade)
    rather than a third, unrelated hue. */
export const PATH_COLOR: Record<string, string> = {
  A: "var(--accent-2)",
  B: "var(--rl)",
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
  live,
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
  /** Streaming live telemetry: adds the sweeping sheen and the glow ring.
      Pass this only when the panel's data really is arriving right now. */
  live?: boolean;
}) {
  const tint = accent === "rl" ? "var(--rl)" : "var(--accent)";
  return (
    <section
      className={cx(
        "panel overflow-hidden flex flex-col",
        raised && "panel-raised",
        accent === "net" && "rail-accent",
        accent === "rl" && "rail-rl",
        live && "panel-live",
        className,
      )}
    >
      {(title || right) && (
        // flex-wrap, not a fixed row: a long subtitle sharing a row with a
        // wide `right` control (a metric picker, a toolbar) had nowhere to
        // go but a column one word wide on a phone. Wrapped, `right` drops
        // to its own line and the subtitle gets the full row back.
        <header
          className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-2 px-5 pt-4 pb-3 border-b relative"
          style={{ borderColor: "var(--hairline)" }}
        >
          <div className="min-w-0 flex items-baseline gap-2.5">
            {icon && (
              <span
                className="shrink-0 self-center"
                style={{ color: tint }}
                aria-hidden="true"
              >
                {icon}
              </span>
            )}
            <div className="min-w-0">
              {title && (
                <h2 className="t-h2" style={{ color: "var(--text-2)" }}>
                  {title}
                </h2>
              )}
              {subtitle && (
                <p className="t-small mt-1.5" style={{ color: "var(--text-3)" }}>
                  {subtitle}
                </p>
              )}
            </div>
          </div>
          {right && <div className="shrink-0 self-start">{right}</div>}
        </header>
      )}
      <div className={cx("p-5 flex-1 min-h-0 relative", bodyClassName)}>{children}</div>
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
    <div className="flex items-end justify-between gap-4 mb-4 mt-2">
      <div className="min-w-0 flex-1">
        <h2
          className="t-label flex items-center gap-3"
          style={{ color: "var(--text-2)" }}
        >
          {children}
          {/* A rule that runs out to the edge: reads as a ledger heading rather
              than a document heading. */}
          <span
            className="flex-1 h-px min-w-[1.5rem]"
            style={{ background: "var(--border-1)" }}
            aria-hidden="true"
          />
        </h2>
        {hint && (
          <p className="t-small mt-1" style={{ color: "var(--text-4)" }}>
            {hint}
          </p>
        )}
      </div>
      {right}
    </div>
  );
}

/** A page's own explanation of itself: what it is for, and the handful of
 * terms a first-time reader needs to make sense of what follows. Always
 * visible — no accordion, no click to reveal — because the console is a
 * research instrument people outside the project will open cold, and an
 * explanation nobody opens is not an explanation.
 *
 * Kept to one short paragraph plus a compact term strip on purpose: this is
 * orientation, not documentation. Anything longer belongs in a `Details`
 * block further down the page, where it can be as technical as the subject
 * needs without competing with the page for the reader's first glance. */
export function AboutPanel({
  summary,
  terms,
}: {
  summary: ReactNode;
  /** Short glossary, shown inline: a term and a one-line gloss. */
  terms?: { term: string; def: string }[];
}) {
  return (
    <div
      className="rounded-[10px] border px-4 py-3 mb-5"
      style={{ borderColor: "var(--border-1)", background: "var(--surface-1)" }}
    >
      <div className="flex items-start gap-3">
        <span
          className="t-micro font-bold shrink-0 mt-[1px]"
          style={{ color: "var(--accent)", letterSpacing: "0.12em" }}
        >
          ABOUT THIS PAGE
        </span>
        <p className="t-small leading-relaxed min-w-0" style={{ color: "var(--text-2)" }}>
          {summary}
        </p>
      </div>
      {terms && terms.length > 0 && (
        <dl
          className="flex flex-wrap gap-x-5 gap-y-1 mt-2.5 pt-2.5 border-t"
          style={{ borderColor: "var(--hairline)" }}
        >
          {terms.map((t) => (
            <div key={t.term} className="flex items-baseline gap-1.5 max-w-full">
              <dt className="t-micro font-bold shrink-0" style={{ color: "var(--text-3)" }}>
                {t.term}
              </dt>
              <dd className="t-micro" style={{ color: "var(--text-4)" }}>
                {t.def}
              </dd>
            </div>
          ))}
        </dl>
      )}
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
      className="panel panel-hover relative overflow-hidden flex flex-col"
      title={explain}
    >
      {/* Header, figure and footnote are separated by rules rather than by a
          pool of light. The number is the largest thing on the plate, which is
          what makes it the focal point — it does not need to glow. */}
      <div
        className="flex items-center justify-between gap-2 px-5 pt-3.5 pb-3"
      >
        <span className="t-label truncate" style={{ color: "var(--text-3)" }}>
          {label}
        </span>
        {source && <SourceBadge source={source} />}
      </div>

      <div className="px-5 pb-1">
        <div className="flex items-baseline gap-2">
          <span
            className="readout leading-none"
            style={{ color, fontSize: 44, fontWeight: 500 }}
          >
            {value}
          </span>
          {unit && !unavailable && (
            <span className="t-small" style={{ color: "var(--text-3)" }}>
              {unit}
            </span>
          )}
        </div>
        {/* Segmented meter: the last N samples, drawn only from the same
            telemetry the number came from. */}
        {spark && spark.length > 1 && !unavailable && (
          <LedMeter values={spark} tone={tone} />
        )}
      </div>

      <div
        className="flex items-center justify-between gap-2 mt-auto px-5 py-2.5 border-t"
        style={{ borderColor: "var(--hairline)" }}
      >
        <span className="t-small truncate" style={{ color: "var(--text-3)" }}>
          {sub}
        </span>
        <DirectionHint direction={direction} />
      </div>
    </div>
  );
}

/** Segmented bar readout of recent samples. Height is relative to the window's
    own maximum, so it shows shape, not an absolute scale — the number above
    carries the magnitude. */
function LedMeter({ values, tone }: { values: number[]; tone?: string }) {
  const window = values.slice(-30);
  const max = Math.max(...window, 0.0001);
  const color =
    tone === "bad" ? "var(--bad)" : tone === "warn" ? "var(--warn)" : "var(--accent)";
  return (
    <div
      className="flex items-end gap-[2px] h-7 relative mt-1.5"
      aria-hidden="true"
    >
      {window.map((v, i) => {
        const h = Math.max(6, (v / max) * 100);
        const recent = i >= window.length - 3;
        return (
          <span
            key={i}
            className="flex-1 rounded-t-[2px]"
            style={{
              height: `${h}%`,
              background: color,
              /* Older samples fade back so the newest reading reads first.
                 Opacity, not a glow: the bar is a mark, not a lamp. */
              opacity: recent ? 0.95 : 0.2 + (i / window.length) * 0.35,
            }}
          />
        );
      })}
    </div>
  );
}

// -------------------------------------------------------------- source badge
const SOURCE_STYLE: Record<
  DataSource,
  { label: string; bg: string; fg: string; title: string }
> = {
  // Deliberately not the variant hues: a RECORDED badge in variant A's blue
  // read as a claim about variant A. These are chrome, so they come from the
  // theme's own ramp instead.
  live: {
    label: "LIVE",
    bg: "var(--ok-dim)",
    fg: "var(--ok)",
    title: "Measured from the experiment running right now.",
  },
  historical: {
    label: "RECORDED",
    bg: "var(--rl-dim)",
    fg: "var(--rl)",
    title: "Read from a completed run's stored measurements.",
  },
  config: {
    label: "CONFIG",
    bg: "var(--config-dim)",
    fg: "var(--text-2)",
    title: "Static project configuration, not a measurement.",
  },
  mock: {
    label: "MOCK DATA",
    bg: "var(--warn-dim)",
    fg: "var(--warn)",
    title: "Synthetic demo data. NOT a measurement of any network.",
  },
  unavailable: {
    label: "UNAVAILABLE",
    bg: "var(--unavailable-dim)",
    fg: "var(--text-3)",
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
        className="px-1.5 py-[1px] rounded text-[9.5px] font-bold tracking-[0.1em] whitespace-nowrap"
        style={{
          background: style.bg,
          color: style.fg,
          border: `1px solid ${style.fg}44`,
          boxShadow: key === "live" ? `0 0 14px -4px ${style.fg}` : "none",
        }}
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
    <span className="relative inline-flex items-center justify-center w-2 h-2 shrink-0">
      {pulse && (
        <span
          className="absolute w-2 h-2 rounded-full pulse-ring"
          style={{ color }}
          aria-hidden="true"
        />
      )}
      <span
        className="inline-block w-2 h-2 rounded-full"
        style={{
          background: color,
          boxShadow: tone === "idle" ? "none" : `0 0 8px ${color}`,
        }}
      />
    </span>
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
    <div className="flex flex-col items-center justify-center text-center py-12 px-6 gap-3 relative overflow-hidden">
      {/* A waiting instrument sweeps; it is not reporting a measurement. */}
      <span className="scanline" aria-hidden="true" />
      <div
        className="w-12 h-12 rounded-xl border flex items-center justify-center text-lg relative"
        style={{
          borderColor: "var(--border-2)",
          color: "var(--text-3)",
          background: "var(--panel-sunken)",
          boxShadow: "inset 0 0 22px var(--accent-wash)",
        }}
      >
        ∅
      </div>
      {/* Not `t-h2`: that is the small-caps label used for panel furniture, and
          an empty state is a sentence to read, not a heading to scan past. */}
      <p className="t-body font-medium" style={{ color: "var(--text-2)" }}>
        {title}
      </p>
      {detail && (
        <p
          className="t-small max-w-md leading-relaxed"
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
        borderColor: "color-mix(in srgb, var(--bad) 42%, transparent)",
        background: "var(--bad-dim)",
        color: "var(--bad)",
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
          style={{
            borderColor: "color-mix(in srgb, var(--bad) 45%, transparent)",
            color: "var(--bad)",
          }}
        >
          Retry
        </button>
      )}
    </div>
  );
}

export function Skeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-label="loading">
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="h-3 skeleton"
          style={{ width: `${70 + ((i * 13) % 25)}%` }}
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
    <div className="panel panel-hover flex flex-col relative overflow-hidden">
      <div className="flex items-center justify-between gap-2 px-5 pt-3.5 pb-3">
        <span
          className="t-label truncate"
          style={{ color: "var(--text-3)" }}
          title={explain ?? label}
        >
          {label}
        </span>
        {source && <SourceBadge source={source} />}
      </div>
      <div className="flex items-baseline gap-2 px-5 pb-3.5">
        <span
          className="readout text-[32px] leading-none"
          style={{ color, fontWeight: 500 }}
        >
          {value}
        </span>
        {unit && !unavailable && (
          <span className="text-[11px]" style={{ color: "var(--text-3)" }}>
            {unit}
          </span>
        )}
      </div>
      <div
        className="flex items-center justify-between gap-2 mt-auto px-5 py-2.5 border-t"
        style={{ borderColor: "var(--hairline)" }}
      >
        <span className="t-small truncate" style={{ color: "var(--text-3)" }}>
          {sub}
        </span>
        <span className="flex items-center gap-1.5 shrink-0">
          <DirectionHint direction={direction} />
          {kind && (
            <span
              className="text-[9.5px] px-1.5 py-0.5 rounded"
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
  const styles: Record<
    string,
    { bg: string; fg: string; border: string; glow?: string }
  > = {
    // Flat fills. The primary action is the only solid one on a page, which is
    // what makes it read as primary — it does not need a gradient or a halo.
    default: {
      bg: "var(--surface-3)", fg: "var(--text-1)", border: "var(--border-2)",
    },
    primary: {
      bg: "var(--accent)", fg: "var(--on-accent)", border: "var(--accent)",
    },
    danger: {
      bg: "var(--bad-dim)", fg: "var(--bad)",
      border: "color-mix(in srgb, var(--bad) 50%, transparent)",
    },
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
        "px-3.5 py-2 rounded-[8px] border text-[12px] font-semibold",
        "transition-all duration-150",
        disabled
          ? "opacity-40 cursor-not-allowed"
          : "hover:brightness-110 active:scale-[0.98]",
      )}
      style={{
        background: s.bg,
        color: s.fg,
        borderColor: s.border,
        boxShadow: disabled ? "none" : s.glow,
        letterSpacing: "0.03em",
      }}
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

// ---------------------------------------------------------------- chart parts
/** One tooltip style for every chart in the console.
 *
 * Previously each chart carried its own inline copy and they had drifted into
 * two different looks, so hovering one panel and then another changed the
 * furniture around the number. Recharts needs a plain object here, not a
 * class, which is why this is a shared constant rather than CSS. */
export const tooltipStyle = {
  background: "var(--glass-strong)",
  border: "1px solid var(--border-2)",
  borderRadius: 10,
  fontSize: 11,
  boxShadow: "var(--e3)",
  padding: "7px 10px",
} as const;

export const tooltipLabelStyle = {
  color: "var(--text-3)",
  fontSize: 10,
  marginBottom: 2,
} as const;

export const tooltipCursor = { fill: "var(--accent-wash)" } as const;

/** Chart legend.
 *
 * Present whenever a chart draws two or more series, so identity never rests
 * on colour memory alone; a single-series chart does not get one, because its
 * title already names the series. `shape` matches how the series is actually
 * drawn, so the key is readable against the plot rather than being a generic
 * row of squares.
 */
export function ChartLegend({
  items,
  className,
  note,
}: {
  items: {
    label: string;
    color: string;
    shape?: "line" | "dashed" | "area" | "dot" | "band";
    /** Dimmed, for a supporting series drawn faintly in the plot. */
    faint?: boolean;
    hint?: string;
  }[];
  className?: string;
  /** Short caveat or axis note, shown right-aligned on wide layouts. */
  note?: ReactNode;
}) {
  if (!items.length) return null;
  return (
    <div
      className={cx(
        "flex flex-wrap items-center gap-x-4 gap-y-1.5 mt-2 t-small",
        className,
      )}
    >
      {items.map((it) => (
        <span
          key={it.label}
          className="inline-flex items-center gap-1.5 min-w-0"
          title={it.hint}
        >
          <Swatch shape={it.shape ?? "line"} color={it.color} faint={it.faint} />
          <span className="truncate" style={{ color: "var(--text-2)" }}>
            {it.label}
          </span>
        </span>
      ))}
      {note && (
        <span className="t-micro sm:ml-auto" style={{ color: "var(--text-4)" }}>
          {note}
        </span>
      )}
    </div>
  );
}

function Swatch({
  shape, color, faint,
}: {
  shape: "line" | "dashed" | "area" | "dot" | "band";
  color: string;
  faint?: boolean;
}) {
  const opacity = faint ? 0.4 : 1;
  if (shape === "dot") {
    return (
      <span
        className="w-2 h-2 rounded-full shrink-0"
        style={{ background: color, opacity }}
        aria-hidden="true"
      />
    );
  }
  if (shape === "band") {
    return (
      <span
        className="w-3.5 h-2.5 rounded-sm shrink-0"
        style={{ background: color, opacity: faint ? 0.2 : 0.34 }}
        aria-hidden="true"
      />
    );
  }
  if (shape === "area") {
    return (
      <span
        className="w-4 h-2.5 rounded-sm shrink-0"
        style={{
          background: `linear-gradient(180deg, ${color}, transparent)`,
          borderTop: `1.5px solid ${color}`,
          opacity,
        }}
        aria-hidden="true"
      />
    );
  }
  return (
    <span
      className="w-5 h-0.5 shrink-0 rounded"
      style={{
        background: shape === "dashed"
          ? `repeating-linear-gradient(90deg, ${color} 0 4px, transparent 4px 7px)`
          : color,
        opacity,
      }}
      aria-hidden="true"
    />
  );
}

export const inputClass =
  "px-3 py-2 rounded-[10px] border text-[12px] outline-none focus:ring-1 focus:ring-sky-600";

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
      className="px-1.5 py-[1px] rounded text-[10px] font-semibold tracking-wide"
      title={title}
      style={{
        background: color ? `${color}1f` : "var(--surface-3)",
        color: color ?? "var(--text-2)",
        border: `1px solid ${color ? `${color}3d` : "var(--border-1)"}`,
      }}
    >
      {children}
    </span>
  );
}
