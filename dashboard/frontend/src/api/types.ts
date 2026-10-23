/** Shapes returned by the backend.
 *
 * `DataSource` is attached to every payload that carries data and drives how
 * the UI labels it. The distinction between `live`, `historical` and `mock` is
 * load-bearing: mock data must never be presented as a measurement.
 */
export type DataSource =
  | "live"
  | "historical"
  | "config"
  | "mock"
  | "unavailable";

export interface Health {
  ok: boolean;
  time: number;
  mode: string;
  mock_mode: boolean;
  project_root: string;
  runs_dir_readable: boolean;
  experiment_active: boolean;
  matrix_running: boolean;
}

export interface Capabilities {
  mode: string;
  mock_mode: boolean;
  is_root: boolean;
  can_read_runs: boolean;
  can_start_experiments: boolean;
  can_query_switches_directly: boolean;
  reason_cannot_start: string | null;
  reason_cannot_query: string | null;
  notes: string[];
  project_root: string;
  runs_dir: string;
}

export interface LiveStatus {
  active: boolean;
  run_id: string | null;
  variant: string | null;
  workload: string | null;
  seed: number | null;
  started_at: number | null;
  elapsed_s: number | null;
  duration_s: number | null;
  matrix_running: boolean;
  processes: { pid: number; cmd: string; elapsed_s: number }[];
  detail: string;
}

export interface TopoNode {
  id: string;
  kind: "switch" | "host";
  label: string;
  dpid?: number;
  is_edge?: boolean;
  ip?: string;
  mac?: string;
  attached_to?: string;
  attached_port?: number;
}

export interface TopoLink {
  id: string;
  kind: "core" | "access";
  source: string;
  target: string;
  source_port: number;
  target_port: number;
  bandwidth_mbit: number;
  delay_ms: number;
  queue_packets?: number;
  paths: string[];
}

export interface PathInfo {
  name: string;
  switches: string[];
  links: string[];
  hops: number;
  bottleneck_mbit: number;
}

export interface ActivePaths {
  available: boolean;
  source?: DataSource;
  reason?: string;
  rule_counts?: Record<string, number>;
  packet_counts?: Record<string, number>;
  share?: Record<string, number>;
  dominant_path?: string | null;
  total_edge_rules?: number;
  dump_t?: number;
  age_s?: number | null;
  note?: string;
}

export interface LinkState {
  state: string;
  state_observed: boolean;
  state_changed_at: number | null;
  utilisation: number | null;
  directions: Record<string, { bps: number; utilisation: number | null }>;
}

export interface TopologyPayload {
  source: DataSource;
  nodes: TopoNode[];
  links: TopoLink[];
  paths: Record<string, PathInfo>;
  controller: { ip: string; port: number; openflow_version: string };
  recovery_link: string;
  description: string;
  live?: {
    available: boolean;
    source?: DataSource;
    reason?: string;
    link_state?: Record<string, LinkState>;
    utilisation_available?: boolean;
    utilisation_reason?: string | null;
    utilisation_interval_s?: number | null;
    active_paths?: ActivePaths;
    age_s?: number | null;
  };
}

export interface FlowRule {
  switch: string;
  kind: "routed" | "table_miss" | "infrastructure";
  priority: number;
  cookie: string;
  table_id: number;
  idle_timeout: number | null;
  hard_timeout: number | null;
  duration_s: number | null;
  n_packets: number;
  n_bytes: number;
  output_port: string | null;
  nw_src: string | null;
  nw_dst: string | null;
  proto: string | null;
  installed_at: number | null;
  raw: string;
}

export interface FlowTablePayload {
  available: boolean;
  source?: DataSource;
  reason?: string;
  dump_t?: number;
  age_s?: number | null;
  stale?: boolean;
  dump_interval_s?: number;
  dump_file?: string;
  switch?: string | null;
  rules?: FlowRule[];
  counts_by_switch?: Record<
    string,
    { total: number; routed: number; table_miss: number; infrastructure: number }
  >;
  routed_timeout_distribution?: Record<string, number>;
  note?: string;
}

export interface Decision {
  t: number | null;
  step: number;
  variant: string;
  state: string;
  action: string;
  path: string;
  idle_timeout: number | null;
  reward: number | null;
  epsilon: number | null;
  q_before: number | null;
  q_after: number | null;
  td_error: number | null;
  exploratory: boolean | null;
  greedy_action: string | null;
  usable_paths: string[];
  reward_terms: Record<string, number | null>;
}

export interface DecodedState {
  raw: string;
  decoded: boolean;
  util_a?: { bin: number; label: string };
  util_b?: { bin: number; label: string };
  occupancy?: { bin: number; label: string };
  miss_rate?: { bin: number; label: string };
}

export interface Verification {
  available: boolean;
  source?: DataSource;
  reason?: string;
  decision?: Decision;
  installed?: ActivePaths | null;
  status?: "confirmed" | "awaiting_confirmation" | "not_observed" | "unverified";
  detail?: string;
  lag_s?: number | null;
}

export interface MetricPoint {
  t: number;
  dt: number | null;
  valid: boolean;
  note: string;
  [key: string]: number | boolean | string | null;
}

export interface MetricSeries {
  points: MetricPoint[];
  count: number;
  source?: DataSource;
  reason?: string;
  age_s?: number | null;
  interval_s?: number | null;
  metric_meta?: Record<string, { label: string; unit: string; kind: string }>;
}

export interface LiveMetrics {
  available: boolean;
  source?: DataSource;
  reason?: string;
  t?: number;
  interval_s?: number | null;
  age_s?: number | null;
  values?: Record<string, number | null>;
  metric_meta?: Record<string, { label: string; unit: string; kind: string }>;
}

export interface LatencyPayload {
  available: boolean;
  source?: DataSource;
  reason?: string;
  window_s?: number;
  samples?: number;
  losses?: number;
  loss_fraction?: number | null;
  median_ms?: number | null;
  p95_ms?: number | null;
  min_ms?: number | null;
  max_ms?: number | null;
  series?: { t: number; rtt_ms: number }[];
  age_s?: number | null;
  source_note?: string;
}

export interface RunSummary {
  run_id: string;
  variant: string;
  workload: string;
  seed: number;
  status: "running" | "complete" | "incomplete" | "interrupted";
  complete: boolean;
  started_at: number | null;
  ended_at: number | null;
  duration_s: number | null;
  git_commit: string | null;
  flow_dump_samples: number | null;
  iperf_files: number;
}

export interface ExperimentStatus {
  launched_by_dashboard: Record<string, unknown> | null;
  live_run: LiveStatus;
  external_experiment_running: boolean;
  external_processes: { pid: number; cmd: string; elapsed_s: number }[];
  can_start: boolean;
  blocked_reason: string | null;
  history: Record<string, unknown>[];
}

export interface ExperimentOptions {
  variants: {
    id: string;
    label: string;
    app: string;
    selects_own_timeout: boolean;
  }[];
  workloads: { id: string; description: string }[];
  duration: { min: number; max: number; default: number; unit: string };
  seed: { min: number; max: number; default: number };
  idle_timeouts: number[];
  notes: string[];
}

export interface StatSummary {
  n: number;
  mean: number | null;
  median: number | null;
  q1: number | null;
  q3: number | null;
  iqr: number | null;
  stdev: number | null;
  ci95_low: number | null;
  ci95_high: number | null;
  min: number | null;
  max: number | null;
}

export interface MetricInfo {
  key: string;
  label: string;
  scale: number;
  digits: number;
  unit: string;
  lower_is_better: boolean;
}

export interface DeltaInfo {
  available: boolean;
  reason?: string;
  relative_change?: number;
  improved?: boolean;
  verdict?: "improved" | "regressed" | "not_distinguishable" | "indeterminate";
  detail?: string;
  ci_overlap?: boolean | null;
  baseline_median?: number;
  variant_median?: number;
}

export interface ComparisonPayload {
  source: DataSource;
  window: string;
  baseline: string;
  workloads: string[];
  variants: string[];
  metric_info: Record<string, MetricInfo>;
  cells: Record<
    string,
    Record<string, { runs: number; seeds: number[]; metrics: Record<string, StatSummary> }>
  >;
  deltas: Record<string, Record<string, Record<string, DeltaInfo>>>;
  coverage: Record<string, Record<string, number[]>>;
  runs_analysed: number;
  runs_excluded_incomplete: number;
  excluded_runs: {
    run_id: string;
    variant: string;
    workload: string;
    seed: number;
  }[];
  note: string;
}

export interface LiveSnapshot {
  type: "snapshot" | "error";
  t: number;
  source?: DataSource;
  mock_mode?: boolean;
  live?: LiveStatus;
  metrics?: LiveMetrics;
  latency?: LatencyPayload;
  topology?: TopologyPayload["live"];
  decisions?: Decision[];
  verification?: Verification;
  events?: { t: number | null; kind: string; detail: string }[];
  flow_counts?: Record<string, { total: number; routed: number }>;
  detail?: string;
}
