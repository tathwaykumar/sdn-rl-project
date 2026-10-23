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
import { useState } from "react";
import { api, downloadUrls } from "../api/client";
import { useAsync } from "../hooks/useLive";
import type { ComparisonPayload, DeltaInfo, StatSummary } from "../api/types";
import {
  Card, Empty, ErrorBox, Pill, Skeleton, SourceBadge, VARIANT_COLOR,
  VARIANT_LABEL, fmtNumber, inputClass, inputStyle,
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

      {data.workloads.map((workload) => (
        <Card
          key={workload}
          title={`Workload: ${workload}`}
          subtitle={`Median across seeds [IQR]; baseline is variant ${baseline}`}
          bodyClassName="p-0"
        >
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
        </Card>
      ))}

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
