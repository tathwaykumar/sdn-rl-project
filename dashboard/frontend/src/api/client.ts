/** Thin fetch wrapper around the backend API.
 *
 * Errors are surfaced, never swallowed into an empty result: a view that
 * cannot load its data shows why, because silently rendering "0" or "no rules"
 * would be indistinguishable from a real measurement of zero.
 */
import type {
  Capabilities, ComparisonPayload, ExperimentOptions, ExperimentStatus,
  FlowTablePayload, Health, LatencyPayload, LiveMetrics, LiveSnapshot,
  LiveStatus, MetricSeries, RunSummary, TopologyPayload, Verification,
  Decision, DecodedState, RewardExplain, RewardModel, MetricCatalog,
} from "./types";

export class ApiError extends Error {
  constructor(message: string, readonly status: number) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch (cause) {
    throw new ApiError(
      `Cannot reach the dashboard API at ${path}. Is the backend running?`,
      0,
    );
  }
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body && typeof body.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body; keep the status text */
    }
    throw new ApiError(detail, response.status);
  }
  return (await response.json()) as T;
}

const qs = (params: Record<string, string | number | undefined | null>) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value));
    }
  }
  const text = search.toString();
  return text ? `?${text}` : "";
};

export const api = {
  health: () => request<Health>("/api/health"),
  capabilities: () => request<Capabilities>("/api/capabilities"),
  liveStatus: () => request<LiveStatus>("/api/live/status"),
  snapshot: () => request<LiveSnapshot>("/api/snapshot"),

  topology: () => request<TopologyPayload>("/api/topology"),
  topologyState: (runId?: string) =>
    request<TopologyPayload>(`/api/topology/state${qs({ run_id: runId })}`),

  flows: (switchId?: string, runId?: string) =>
    request<FlowTablePayload>(`/api/flows${qs({ switch: switchId, run_id: runId })}`),
  flowOccupancy: (runId?: string, limit = 60) =>
    request<{ available: boolean; history: { t: number; per_switch: Record<string, number>; total: number }[] }>(
      `/api/flows/occupancy${qs({ run_id: runId, limit })}`,
    ),

  decisions: (runId?: string, limit = 60) =>
    request<{
      available: boolean;
      source?: string;
      reason?: string;
      decisions: Decision[];
      states?: Record<string, DecodedState>;
      note?: string;
    }>(`/api/rl/decisions${qs({ run_id: runId, limit })}`),
  verification: (runId?: string) =>
    request<Verification>(`/api/rl/verification${qs({ run_id: runId })}`),

  metricsLive: (runId?: string) =>
    request<LiveMetrics>(`/api/metrics/live${qs({ run_id: runId })}`),
  metricsSeries: (runId?: string, limit = 180) =>
    request<MetricSeries>(`/api/metrics/series${qs({ run_id: runId, limit })}`),
  latency: (runId?: string, windowS = 30) =>
    request<LatencyPayload>(`/api/metrics/latency${qs({ run_id: runId, window_s: windowS })}`),
  rewardModel: (runId?: string) =>
    request<RewardModel>(`/api/reward/model${qs({ run_id: runId })}`),
  rewardExplain: (runId?: string, limit = 200) =>
    request<RewardExplain>(`/api/reward/explain${qs({ run_id: runId, limit })}`),
  metricCatalog: () => request<MetricCatalog>("/api/metrics/catalog"),

  events: (runId?: string, limit = 80) =>
    request<{ available: boolean; events: { t: number | null; kind: string; detail: string }[] }>(
      `/api/events${qs({ run_id: runId, limit })}`,
    ),

  experimentOptions: () => request<ExperimentOptions>("/api/experiments/options"),
  experimentStatus: () => request<ExperimentStatus>("/api/experiments/status"),
  experimentStart: (body: {
    variant: string;
    workload: string;
    seed: number;
    duration_s: number;
    idle_timeout?: number | null;
  }) =>
    request<Record<string, unknown>>("/api/experiments/start", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  experimentStop: (experimentId?: string) =>
    request<Record<string, unknown>>("/api/experiments/stop", {
      method: "POST",
      // Addressed by id so the browser can never stop "whatever is running".
      body: JSON.stringify(experimentId ? { experiment_id: experimentId } : {}),
    }),
  experimentInjectLink: (body: {
    experiment_id: string;
    action: "link_down" | "link_up";
    link: string;
  }) =>
    request<{ queued?: Record<string, unknown>; note?: string }>(
      "/api/experiments/inject-link",
      { method: "POST", body: JSON.stringify(body) },
    ),
  experimentLinks: () =>
    request<{
      links: {
        id: string;
        endpoints: string[];
        bandwidth_mbit: number;
        delay_ms: number;
        on_paths: string[];
      }[];
      actions: string[];
      note: string;
    }>("/api/experiments/links"),
  experimentCommand: (params: {
    variant: string;
    workload: string;
    seed: number;
    duration_s: number;
    idle_timeout?: number | null;
  }) =>
    request<{ argv: string[]; command: string; cwd: string }>(
      `/api/experiments/command${qs(params as Record<string, string | number>)}`,
    ),
  experimentLog: (lines = 200) =>
    request<{ available: boolean; reason?: string; path?: string; lines?: string[] }>(
      `/api/experiments/log${qs({ lines })}`,
    ),

  runs: () => request<{ runs: RunSummary[]; total: number; live_run_id: string | null }>("/api/runs"),
  run: (runId: string) => request<Record<string, unknown>>(`/api/runs/${encodeURIComponent(runId)}`),

  comparison: (params: {
    window?: string;
    baseline?: string;
    workloads?: string;
    variants?: string;
    seeds?: string;
  }) => request<ComparisonPayload>(`/api/comparison${qs(params)}`),

  artifacts: () =>
    request<{
      available: boolean;
      directory: string;
      items: { name: string; kind: string; bytes: number; modified: number }[];
      note: string;
    }>("/api/analysis/artifacts"),
};

export const downloadUrls = {
  runCsv: (runId: string, window = "full") =>
    `/api/runs/${encodeURIComponent(runId)}/export.csv?window=${window}`,
  comparisonCsv: (window = "full") => `/api/comparison/export.csv?window=${window}`,
  artifact: (name: string) => `/api/analysis/file/${encodeURIComponent(name)}`,
};
