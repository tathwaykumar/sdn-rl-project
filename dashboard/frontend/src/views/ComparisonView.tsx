/** A/B/C comparison over completed runs.
 *
 * Figures come from the backend, which computes them with the project's own
 * analysis code, so this page and the generated report agree by construction.
 *
 * The verdict wording is deliberately conservative. With five seeds per cell a
 * difference is only called real when the 95% confidence intervals do not
 * overlap; otherwise it reads "not distinguishable at this sample size", which
 * is not the same as "no difference" and is not dressed up as significance.
 */
import { useMemo, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell as RCell, ErrorBar, LabelList,
  ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { api, downloadUrls } from "../api/client";
import { useAsync } from "../hooks/useLive";
import type {
  ComparisonPayload, DeltaInfo, MetricInfo, StatSummary,
} from "../api/types";
import {
  Card, ChartLegend, Details, Empty, ErrorBox, Pill, Skeleton, SourceBadge,
  VARIANT_COLOR, VARIANT_LABEL, fmtNumber, inputClass, inputStyle,
  tooltipCursor, tooltipStyle,
} from "../components/ui";

const VERDICT_STYLE: Record<string, { color: string; label: string; title: string }> = {
  improved: {
    color: "var(--ok)", label: "improved",
    title: "Better than baseline and the 95% confidence intervals do not overlap.",
  },
  regressed: {
    color: "var(--bad)", label: "regressed",
    title: "Worse than baseline and the 95% confidence intervals do not overlap.",
  },
  not_distinguishable: {
    color: "var(--text-3)", label: "not distinguishable",
    title:
      "The confidence intervals overlap at this sample size. This is not evidence of no difference.",
  },
  indeterminate: {
    color: "var(--text-3)", label: "too few runs",
    title: "Not enough observations to form a confidence interval.",
  },
};

export function ComparisonView() {
  const [window_, setWindow] = useState<"full" | "second_half">("second_half");
  const [baseline, setBaseline] = useState("A");
  const [workloadFilter, setWorkloadFilter] = useState("");
  const [variantFilter, setVariantFilter] = useState("");
  const [seedFilter, setSeedFilter] = useState("");

  const { data, loading, error, refresh } = useAsync<ComparisonPayload>(
    () =>
      api.comparison({
        window: window_, baseline,
        workloads: workloadFilter || undefined,
        variants: variantFilter || undefined,
        seeds: seedFilter || undefined,
      }),
    [window_, baseline, workloadFilter, variantFilter, seedFilter],
  );

  if (loading && !data) return <Card title="A/B/C comparison"><Skeleton rows={10} /></Card>;
  if (error) return <Card title="A/B/C comparison"><ErrorBox message={error} onRetry={refresh} /></Card>;
  if (!data) return null;

  if (data.workloads.length === 0) {
    return (
      <Card title="A/B/C comparison" right={<SourceBadge source="historical" />}>
        <Empty
          title="No completed runs to compare"
          detail="Runs appear here once they finish and their manifest marks them complete."
        />
      </Card>
    );
  }

  const metrics = Object.values(data.metric_info);

  return (
    <div className="flex flex-col gap-4">
      <HeadlineChart data={data} metrics={metrics} baseline={baseline} />
      <Card
        title="Comparison settings"
        right={<SourceBadge source={data.source} />}
        bodyClassName="p-3"
      >
        <div className="flex flex-wrap gap-3 items-end">
          <label className="flex flex-col gap-1">
            <span className="text-[10px]" style={{ color: "var(--text-3)" }}>Window</span>
            <select value={window_} onChange={(e) => setWindow(e.target.value as "full" | "second_half")}
                    className={inputClass} style={inputStyle}>
              <option value="second_half">Second half (post-exploration)</option>
              <option value="full">Full run</option>
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px]" style={{ color: "var(--text-3)" }}>Baseline</span>
            <select value={baseline} onChange={(e) => setBaseline(e.target.value)}
                    className={inputClass} style={inputStyle}>
              {data.variants.map((v) => <option key={v} value={v}>Variant {v}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px]" style={{ color: "var(--text-3)" }}>Workloads</span>
            <input value={workloadFilter} onChange={(e) => setWorkloadFilter(e.target.value)}
                   placeholder="e.g. heavy,churn" className={inputClass} style={inputStyle} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px]" style={{ color: "var(--text-3)" }}>Variants</span>
            <input value={variantFilter} onChange={(e) => setVariantFilter(e.target.value)}
                   placeholder="e.g. A,C" className={inputClass} style={inputStyle} />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-[10px]" style={{ color: "var(--text-3)" }}>Seeds</span>
            <input value={seedFilter} onChange={(e) => setSeedFilter(e.target.value)}
                   placeholder="e.g. 1,2,3" className={inputClass} style={inputStyle} />
          </label>
          <a href={downloadUrls.comparisonCsv(window_)}
             className="px-3 py-1.5 rounded-lg border text-[12px]"
             style={{ background: "var(--surface-2)", borderColor: "var(--border-2)", color: "var(--text-1)" }}>
            Export CSV
          </a>
        </div>
        <div className="flex flex-wrap gap-4 mt-3 text-[11px]" style={{ color: "var(--text-3)" }}>
          <span>{data.runs_analysed} runs analysed</span>
          {data.runs_excluded_incomplete > 0 && (
            <span style={{ color: "var(--warn)" }}>
              {data.runs_excluded_incomplete} incomplete run(s) excluded
            </span>
          )}
          <span>{data.note}</span>
        </div>
      </Card>

      <Card
        title="Full numeric tables"
        subtitle="Every metric, every variant. Median across seeds [IQR]; the charts above read from the same figures."
      >
        <Details summary={`Show all ${metrics.length} metrics across ${data.workloads.length} workloads`}>
          <div className="flex flex-col gap-4 mt-1">
      {data.workloads.map((workload) => (
        <div key={workload}>
          <p className="t-label mb-1.5" style={{ color: "var(--accent)" }}>
            Workload: {workload}
          </p>
          <div className="overflow-auto">
            <table className="w-full text-[11px]">
              <thead style={{ background: "var(--surface-2)" }}>
                <tr style={{ color: "var(--text-3)" }}>
                  <th className="px-3 py-2 text-left font-medium text-[10px] uppercase tracking-wider">
                    Metric
                  </th>
                  {data.variants.map((v) => (
                    <th key={v} className="px-3 py-2 text-right font-medium text-[10px] uppercase tracking-wider">
                      <span style={{ color: VARIANT_COLOR[v] }}>{v}</span>
                      <span className="ml-1 normal-case" style={{ color: "var(--text-3)" }}>
                        (n={data.cells[workload]?.[v]?.runs ?? 0})
                      </span>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {metrics.map((m) => {
                  const anyData = data.variants.some(
                    (v) => (data.cells[workload]?.[v]?.metrics[m.key]?.n ?? 0) > 0,
                  );
                  if (!anyData) return null;
                  return (
                    <tr key={m.key} className="border-t" style={{ borderColor: "var(--border-1)" }}>
                      <td className="px-3 py-2">
                        {m.label}
                        {m.unit && <span style={{ color: "var(--text-3)" }}> ({m.unit})</span>}
                        <span className="ml-1 text-[9px]" style={{ color: "var(--text-3)" }}
                              title={m.lower_is_better ? "Lower is better" : "Higher is better"}>
                          {m.lower_is_better ? "↓" : "↑"}
                        </span>
                      </td>
                      {data.variants.map((v) => {
                        const summary = data.cells[workload]?.[v]?.metrics[m.key];
                        const delta = v === baseline
                          ? null
                          : data.deltas[workload]?.[v]?.[m.key];
                        return (
                          <td key={v} className="px-3 py-2 text-right">
                            <Cell summary={summary} metricScale={m.scale} digits={m.digits}
                                  delta={delta} isBaseline={v === baseline} />
                          </td>
                        );
                      })}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ))}
          </div>
        </Details>
      </Card>

      <Card title="Seed coverage" subtitle="Which seeds produced a usable run in each cell">
        <div className="overflow-auto">
          <table className="w-full text-[11px]">
            <thead>
              <tr style={{ color: "var(--text-3)" }}>
                <th className="px-2 py-1.5 text-left text-[10px] uppercase">Workload</th>
                {data.variants.map((v) => (
                  <th key={v} className="px-2 py-1.5 text-left text-[10px] uppercase">{v}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Object.entries(data.coverage).map(([workload, variants]) => (
                <tr key={workload} className="border-t" style={{ borderColor: "var(--border-1)" }}>
                  <td className="px-2 py-1.5">{workload}</td>
                  {data.variants.map((v) => (
                    <td key={v} className="px-2 py-1.5 num">
                      {(variants[v] ?? []).length
                        ? (variants[v] ?? []).join(", ")
                        : <span style={{ color: "var(--text-3)" }}>none</span>}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {data.variants.map((v) => (
            <Pill key={v} color={VARIANT_COLOR[v]}>{v} — {VARIANT_LABEL[v]}</Pill>
          ))}
        </div>
      </Card>

      {data.excluded_runs.length > 0 && (
        <Card title="Excluded runs" subtitle="Incomplete; not counted in any figure above">
          <div className="flex flex-wrap gap-1.5">
            {data.excluded_runs.map((r) => (
              <Pill key={r.run_id} color="var(--warn)" title={r.run_id}>
                {r.variant}/{r.workload}/s{r.seed}
              </Pill>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

/** The page's lead: one metric, every workload, every variant, with the
 * uncertainty drawn rather than parenthesised.
 *
 * Bars are the mean across seeds and the whiskers are the 95% confidence
 * interval — the same interval the backend's verdict logic tests for overlap,
 * so a reader can see for themselves why a difference was or was not called
 * real. The verdict matrix underneath states that conclusion in words.
 *
 * Deliberately *not* done here: no bar is drawn for a cell with no
 * observations, no interval is drawn when the backend could not form one
 * (n < 2), and nothing is interpolated across missing seeds.
 */
function HeadlineChart({
  data, metrics, baseline,
}: {
  data: ComparisonPayload;
  metrics: MetricInfo[];
  baseline: string;
}) {
  // Throughput first if present: it is the metric the project leads with.
  const [metricKey, setMetricKey] = useState(
    () => (metrics.find((m) => m.key === "throughput_of_bps") ?? metrics[0])?.key ?? "",
  );
  const metric = data.metric_info[metricKey];

  const rows = useMemo(() => {
    if (!metric) return [];
    return data.workloads.map((workload) => {
      const row: Record<string, number | string | [number, number] | null> = {
        workload,
      };
      for (const v of data.variants) {
        const s: StatSummary | undefined = data.cells[workload]?.[v]?.metrics[metric.key];
        if (!s || s.n === 0 || s.mean === null) {
          row[v] = null;
          row[`${v}_err`] = null;
          continue;
        }
        const mean = s.mean / metric.scale;
        row[v] = mean;
        // Recharts reads ErrorBar values as offsets either side of the bar.
        // Absent when the backend could not form an interval.
        row[`${v}_err`] =
          s.ci95_low !== null && s.ci95_high !== null
            ? [mean - s.ci95_low / metric.scale, s.ci95_high / metric.scale - mean]
            : null;
        row[`${v}_n`] = s.n;
        row[`${v}_median`] = s.median !== null ? s.median / metric.scale : null;
        row[`${v}_iqr`] = s.iqr !== null ? s.iqr / metric.scale : null;
      }
      return row;
    });
  }, [data, metric]);

  if (!metric) return null;

  const anyData = rows.some((r) => data.variants.some((v) => r[v] !== null));

  return (
    <Card
      title="Variant comparison"
      subtitle={
        `${metric.label}${metric.unit ? ` (${metric.unit})` : ""} — bars are the mean `
        + "across seeds, whiskers the 95% confidence interval. "
        + (metric.lower_is_better ? "Lower is better." : "Higher is better.")
      }
      accent="rl"
      right={
        <label className="flex items-center gap-2">
          <span className="t-micro" style={{ color: "var(--text-4)" }}>METRIC</span>
          <select
            value={metricKey}
            onChange={(e) => setMetricKey(e.target.value)}
            className={inputClass}
            style={inputStyle}
          >
            {metrics.map((m) => (
              <option key={m.key} value={m.key}>{m.label}</option>
            ))}
          </select>
        </label>
      }
    >
      {!anyData ? (
        <Empty
          title="This metric was not measured in any cell"
          detail="Nothing is plotted rather than plotting zero, because zero would itself be a measurement."
        />
      ) : (
        <>
          <div style={{ height: 268 }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart data={rows} margin={{ top: 10, right: 8, bottom: 4, left: -10 }}
                        barGap={2} barCategoryGap="22%">
                <CartesianGrid stroke="var(--border-1)" strokeDasharray="2 5" vertical={false} />
                <XAxis
                  dataKey="workload" tick={{ fontSize: 11 }} tickLine={false}
                  axisLine={{ stroke: "var(--border-1)" }}
                />
                <YAxis tick={{ fontSize: 10 }} tickLine={false} axisLine={false} />
                <Tooltip
                  cursor={tooltipCursor}
                  contentStyle={tooltipStyle}
                  formatter={(value: number, name: string, p) => {
                    const d = p.payload as Record<string, unknown>;
                    const n = d[`${name}_n`] as number | undefined;
                    const med = d[`${name}_median`] as number | null | undefined;
                    const iqr = d[`${name}_iqr`] as number | null | undefined;
                    return [
                      `mean ${fmtNumber(value, metric.digits)}`
                      + (med != null ? ` · median ${fmtNumber(med, metric.digits)}` : "")
                      + (iqr != null ? ` [IQR ${fmtNumber(iqr, metric.digits)}]` : "")
                      + (n != null ? ` · n=${n}` : ""),
                      `Variant ${name}`,
                    ];
                  }}
                />
                {data.variants.map((v) => (
                  <Bar
                    key={v} dataKey={v} name={v} fill={VARIANT_COLOR[v]}
                    radius={[3, 3, 0, 0]} isAnimationActive={false}
                  >
                    {/* 2px surface gap keeps adjacent bars from fusing. */}
                    {rows.map((_, i) => (
                      <RCell key={i} stroke="var(--surface-0)" strokeWidth={2} />
                    ))}
                    <ErrorBar
                      dataKey={`${v}_err`} width={5} strokeWidth={1.4}
                      stroke="var(--text-2)"
                    />
                    {data.workloads.length <= 4 && (
                      <LabelList
                        dataKey={v} position="top" offset={8}
                        formatter={(x: number) => fmtNumber(x, metric.digits)}
                        style={{ fontSize: 9, fill: "var(--text-3)" }}
                      />
                    )}
                  </Bar>
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>

          <ChartLegend
            className="mb-3"
            items={[
              ...data.variants.map((v) => ({
                label: v === baseline ? `${v} · baseline` : `Variant ${v}`,
                color: VARIANT_COLOR[v],
                shape: "band" as const,
                hint: VARIANT_LABEL[v],
              })),
              {
                label: "95% confidence interval",
                color: "var(--text-2)",
                shape: "line" as const,
                hint: "The interval the verdict below tests for overlap.",
              },
            ]}
            note={`n per cell shown on hover · ${data.runs_analysed} runs`}
          />

          <Takeaway data={data} metric={metric} baseline={baseline} />
          <VerdictMatrix data={data} metric={metric} baseline={baseline} />
        </>
      )}
    </Card>
  );
}

/** The headline, in one sentence, for the metric on show.
 *
 * Someone opening this page sees four charts and a grid of verdicts; this says
 * what they add up to before they start reading cells. It is assembled from
 * the backend's own verdicts — the same ones the matrix below lists — so it
 * cannot drift from them, and it is regenerated when the metric changes.
 *
 * The wording is kept to what the measurements support: "measured higher",
 * never "improves" or "because". Where the intervals overlap it says the
 * difference is not distinguishable at this sample size, which is not a
 * finding of no difference.
 */
function Takeaway({
  data, metric, baseline,
}: {
  data: ComparisonPayload;
  metric: MetricInfo;
  baseline: string;
}) {
  const others = data.variants.filter((v) => v !== baseline);

  const groups = useMemo(() => {
    const better: string[] = [];
    const worse: string[] = [];
    const flat: string[] = [];
    const mixed: string[] = [];
    for (const w of data.workloads) {
      const verdicts = others
        .map((v) => data.deltas[w]?.[v]?.[metric.key])
        .filter((d): d is DeltaInfo => Boolean(d?.available) && Boolean(d?.verdict))
        .map((d) => d.verdict as string);
      if (!verdicts.length) continue;
      const uniq = new Set(verdicts);
      if (uniq.size > 1 && (uniq.has("improved") || uniq.has("regressed"))) mixed.push(w);
      else if (uniq.has("improved") && uniq.size === 1) better.push(w);
      else if (uniq.has("regressed") && uniq.size === 1) worse.push(w);
      else flat.push(w);
    }
    return { better, worse, flat, mixed };
  }, [data, metric, others]);

  const list = (xs: string[]) =>
    xs.length === 1
      ? xs[0]
      : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`;
  const vs = others.length === 1 ? others[0] : others.join(" and ");

  const clauses: { text: string; color: string }[] = [];
  if (groups.better.length) {
    clauses.push({
      text: `Under ${list(groups.better)}, ${vs} measured `
        + `${metric.lower_is_better ? "lower" : "higher"} than ${baseline} `
        + "with non-overlapping 95% intervals.",
      color: "var(--ok)",
    });
  }
  if (groups.worse.length) {
    clauses.push({
      text: `Under ${list(groups.worse)}, ${vs} measured `
        + `${metric.lower_is_better ? "higher" : "lower"} than ${baseline} `
        + "with non-overlapping 95% intervals.",
      color: "var(--bad)",
    });
  }
  if (groups.mixed.length) {
    clauses.push({
      text: `Under ${list(groups.mixed)}, the variants did not agree with each `
        + "other — read those rows individually.",
      color: "var(--warn)",
    });
  }
  if (groups.flat.length) {
    clauses.push({
      text: `Under ${list(groups.flat)}, no difference was distinguishable at `
        + "five seeds per cell.",
      color: "var(--text-3)",
    });
  }

  if (!clauses.length) return null;

  return (
    <div
      className="rounded-lg px-3 py-2.5 mb-3"
      style={{
        background: "rgba(10,18,31,0.5)",
        border: "1px solid var(--border-1)",
      }}
    >
      <p className="t-label mb-1.5" style={{ color: "var(--text-4)" }}>
        At a glance — {metric.label}
      </p>
      <ul className="space-y-1">
        {clauses.map((c) => (
          <li key={c.text} className="flex items-start gap-2 t-body">
            <span
              className="w-1.5 h-1.5 rounded-full shrink-0 mt-[6px]"
              style={{ background: c.color }}
              aria-hidden="true"
            />
            <span style={{ color: "var(--text-2)" }}>{c.text}</span>
          </li>
        ))}
      </ul>
      <p className="t-small mt-2" style={{ color: "var(--text-4)" }}>
        Differences are measured, not explained: this page compares outcomes
        across variants and does not establish why they differ.
      </p>
    </div>
  );
}

/** The conclusion in words, for the metric on show.
 *
 * Reads straight from the backend's own verdicts so the page cannot disagree
 * with the generated report. "Not distinguishable" is displayed as exactly
 * that, never softened into a win.
 */
function VerdictMatrix({
  data, metric, baseline,
}: {
  data: ComparisonPayload;
  metric: MetricInfo;
  baseline: string;
}) {
  const others = data.variants.filter((v) => v !== baseline);
  if (!others.length) return null;

  return (
    <div className="pt-3 border-t" style={{ borderColor: "var(--border-1)" }}>
      <p className="t-label mb-2" style={{ color: "var(--text-3)" }}>
        Versus variant {baseline} — {metric.label}
      </p>
      <div className="overflow-auto">
        <table className="w-full text-[11px]">
          <thead>
            <tr style={{ color: "var(--text-4)" }}>
              <th className="px-2 py-1.5 text-left t-micro uppercase">Workload</th>
              {others.map((v) => (
                <th key={v} className="px-2 py-1.5 text-left t-micro uppercase">
                  <span style={{ color: VARIANT_COLOR[v] }}>{v}</span> vs {baseline}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.workloads.map((w) => (
              <tr key={w} className="border-t" style={{ borderColor: "var(--border-1)" }}>
                <td className="px-2 py-1.5">{w}</td>
                {others.map((v) => {
                  const d: DeltaInfo | undefined = data.deltas[w]?.[v]?.[metric.key];
                  const style = d?.verdict ? VERDICT_STYLE[d.verdict] : null;
                  if (!d?.available || !style) {
                    return (
                      <td key={v} className="px-2 py-1.5" style={{ color: "var(--text-4)" }}>
                        {d?.reason ?? "not measured"}
                      </td>
                    );
                  }
                  return (
                    <td key={v} className="px-2 py-1.5">
                      <span
                        className="inline-flex items-center gap-1.5"
                        title={d.detail}
                      >
                        <span
                          className="w-1.5 h-1.5 rounded-full shrink-0"
                          style={{
                            background: style.color,
                            boxShadow: d.verdict === "improved" || d.verdict === "regressed"
                              ? `0 0 7px ${style.color}` : "none",
                          }}
                          aria-hidden="true"
                        />
                        {d.relative_change !== undefined && (
                          <span className="num" style={{ color: style.color }}>
                            {d.relative_change > 0 ? "+" : ""}
                            {fmtNumber(d.relative_change * 100, 1)}%
                          </span>
                        )}
                        <span style={{ color: "var(--text-3)" }}>{style.label}</span>
                      </span>
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="t-small mt-2 leading-relaxed" style={{ color: "var(--text-4)" }}>
        A difference is called real only when the 95% intervals do not overlap.
        &ldquo;Not distinguishable&rdquo; means the evidence at five seeds per
        cell cannot separate them — it is not a finding of no difference, and a
        percentage shown beside it is a direction, not a result.
      </p>
    </div>
  );
}

function Cell({
  summary, metricScale, digits, delta, isBaseline,
}: {
  summary?: StatSummary;
  metricScale: number;
  digits: number;
  delta?: DeltaInfo | null;
  isBaseline: boolean;
}) {
  if (!summary || summary.n === 0) {
    return <span style={{ color: "var(--text-3)" }}>not measured</span>;
  }
  const median = summary.median !== null ? summary.median / metricScale : null;
  const iqr = summary.iqr !== null ? summary.iqr / metricScale : null;
  const verdict = delta?.verdict ? VERDICT_STYLE[delta.verdict] : null;

  return (
    <div className="flex flex-col items-end gap-0.5">
      <span className="num font-medium">
        {fmtNumber(median, digits)}
        {iqr !== null && (
          <span className="ml-1 text-[10px]" style={{ color: "var(--text-3)" }}>
            [{fmtNumber(iqr, digits)}]
          </span>
        )}
      </span>
      {isBaseline ? (
        <span className="text-[9px]" style={{ color: "var(--text-3)" }}>baseline</span>
      ) : delta?.available && verdict ? (
        <span className="text-[9px]" style={{ color: verdict.color }} title={delta.detail}>
          {delta.relative_change !== undefined && (
            <>{delta.relative_change > 0 ? "+" : ""}
              {fmtNumber(delta.relative_change * 100, 1)}% · </>
          )}
          {verdict.label}
        </span>
      ) : (
        <span className="text-[9px]" style={{ color: "var(--text-3)" }}>
          {delta?.reason ?? "—"}
        </span>
      )}
    </div>
  );
}
