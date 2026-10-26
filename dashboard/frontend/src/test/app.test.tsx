/** App-level rendering: the shell mounts, and each global state is visible.
 *
 * These assert the states a user actually lands in -- no experiment running,
 * an experiment running, and mock mode -- because each one has to be
 * distinguishable at a glance. In particular, mock mode must be impossible to
 * miss.
 */
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";
import { api } from "../api/client";

/** A fake socket that records its instances so a test can push a frame. */
class SilentSocket {
  static instances: SilentSocket[] = [];
  onopen: (() => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  constructor(readonly url: string) {
    SilentSocket.instances.push(this);
  }
  open() {
    this.onopen?.();
  }
  emit(payload: unknown) {
    this.onmessage?.({ data: JSON.stringify(payload) });
  }
  close() {}
  send() {}
}

const IDLE_SNAPSHOT = {
  type: "snapshot" as const,
  t: 1_792_747_000,
  mock_mode: false,
  source: "unavailable" as const,
  live: {
    active: false, run_id: null, variant: null, workload: null, seed: null,
    started_at: null, elapsed_s: null, duration_s: null, matrix_running: false,
    processes: [], detail: "No run directory is awaiting a manifest.",
  },
  metrics: { available: false, reason: "no experiment is running" },
  decisions: [],
  events: [],
};

const ACTIVE_SNAPSHOT = {
  ...IDLE_SNAPSHOT,
  source: "live" as const,
  live: {
    ...IDLE_SNAPSHOT.live,
    active: true, run_id: "20261023-090000-C-heavy-seed1",
    variant: "C", workload: "heavy", seed: 1,
    elapsed_s: 30, duration_s: 90, matrix_running: true,
    detail: "Run in progress as part of a matrix sweep.",
  },
  metrics: {
    available: true, source: "live" as const, t: 1_792_747_000,
    interval_s: 1, age_s: 0.4,
    values: {
      delivered_bps: 9_900_000, max_link_util: 0.91, total_rules: 88,
      util_A: 0.0, util_B: 0.91,
    },
    metric_meta: {},
  },
  latency: {
    available: true, source: "live" as const, median_ms: 12.4, p95_ms: 31.2,
    samples: 140, losses: 2, loss_fraction: 0.014, window_s: 30, series: [],
  },
  decisions: [
    {
      t: 1_792_747_000, step: 29, variant: "C", state: "0-3-1-0",
      action: "path=B,idle=15", path: "B", idle_timeout: 15, reward: 0.42,
      epsilon: 0.1, q_before: 0.3, q_after: 0.35, td_error: 0.05,
      exploratory: false, greedy_action: "path=B,idle=15",
      usable_paths: ["A", "B"], reward_terms: {},
    },
  ],
  verification: {
    available: true, status: "confirmed" as const,
    detail: "18 rule(s) on path B are installed at the edge switches.",
  },
  // The Overview's forwarding panel reads this; without it the panel
  // correctly shows its empty state instead.
  topology: {
    available: true,
    source: "live" as const,
    link_state: {},
    active_paths: {
      available: true,
      rule_counts: { A: 0, B: 18 },
      dominant_path: "B",
      total_edge_rules: 18,
    },
  },
  events: [{ t: 1_792_746_990, kind: "switch_connected", detail: "s1" }],
};

const CAPS = {
  mode: "live", mock_mode: false, is_root: false, can_read_runs: true,
  can_start_experiments: false, can_query_switches_directly: false,
  reason_cannot_start: "The API is running unprivileged.",
  reason_cannot_query: "ovs-ofctl requires root.",
  notes: ["Live telemetry is read from the active run's files."],
  project_root: "/project", runs_dir: "/project/results/runs",
};

describe("App", () => {
  beforeEach(() => {
    SilentSocket.instances = [];
    vi.stubGlobal("WebSocket", SilentSocket as unknown as typeof WebSocket);
    vi.spyOn(api, "capabilities").mockResolvedValue(CAPS as never);
    vi.spyOn(api, "snapshot").mockResolvedValue(IDLE_SNAPSHOT as never);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it("renders the shell and every navigation section", async () => {
    render(<App />);
    expect(screen.getByText(/SDN.*RL Console/)).toBeInTheDocument();
    for (const label of [
      "Overview", "Live Topology", "Metrics & Analytics", "Reward Explorer",
      "Decision Inspector", "Experiments", "A/B/C Comparison", "History",
    ]) {
      expect(screen.getByRole("button", { name: label })).toBeInTheDocument();
    }
  });

  it("groups navigation so RL pages are findable", () => {
    render(<App />);
    // Asserted through each group's accessible name (the <ul aria-label>)
    // rather than its visible caption text, which is a plain style choice.
    for (const group of [
      "Operations", "Reinforcement learning", "Runs",
    ]) {
      expect(screen.getByRole("list", { name: group })).toBeInTheDocument();
    }
  });

  it("marks the current page for assistive technology", async () => {
    render(<App />);
    const overview = screen.getByRole("button", { name: "Overview" });
    expect(overview).toHaveAttribute("aria-current", "page");

    const reward = screen.getByRole("button", { name: "Reward Explorer" });
    expect(reward).not.toHaveAttribute("aria-current");
  });

  it("states plainly when no experiment is running", async () => {
    render(<App />);
    await waitFor(() =>
      expect(screen.getAllByText(/No experiment running/).length)
        .toBeGreaterThan(0),
    );
    // The headline says Idle, and the metrics read N/A rather than 0 —
    // an unmeasured value must never render as a measurement.
    expect(screen.getByText("Idle")).toBeInTheDocument();
    expect(screen.getAllByText("N/A").length).toBeGreaterThan(0);
  });

  it("shows the running experiment and its live measurements", async () => {
    render(<App />);
    const socket = SilentSocket.instances.at(-1)!;

    await act(async () => {
      socket.open();
      socket.emit(ACTIVE_SNAPSHOT);
    });

    // The run identity appears in the header pill and the status banner.
    expect(screen.getAllByText(/heavy/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Experiment running/)).toBeInTheDocument();
    // Live measurements reach the headline metrics (9.9 Mbit/s, 88 rules).
    // Each also appears in the chrome's top readout strip, so these are
    // deliberately "all" queries: the same measurement is shown twice, in the
    // always-visible strip and in the page's own hero metric.
    expect(screen.getAllByText("9.90").length).toBeGreaterThan(0);
    expect(screen.getAllByText("88").length).toBeGreaterThan(0);
    // Latency comes from the host probe, not the controller.
    expect(screen.getAllByText("12.4").length).toBeGreaterThan(0);
  });

  it("separates confirmed forwarding from agent intent on the Overview", async () => {
    render(<App />);
    const socket = SilentSocket.instances.at(-1)!;
    await act(async () => {
      socket.open();
      socket.emit(ACTIVE_SNAPSHOT);
    });
    // The Overview reports what the switches hold, and says so explicitly.
    expect(screen.getByText("Confirmed forwarding")).toBeInTheDocument();
    expect(
      screen.getByText(/only when rules for it exist on\s+the switches/),
    ).toBeInTheDocument();
  });

  it("makes mock mode impossible to miss", async () => {
    vi.spyOn(api, "capabilities").mockResolvedValue(
      { ...CAPS, mock_mode: true } as never,
    );
    render(<App />);
    await waitFor(() =>
      expect(screen.getByText(/MOCK MODE/)).toBeInTheDocument(),
    );
    expect(
      screen.getByText(/synthetic demo data, not a\s+measurement/),
    ).toBeInTheDocument();
  });

  it("reports connection state in the header", async () => {
    render(<App />);
    await waitFor(() =>
      expect(screen.getAllByText(/Connecting|Live stream|Polling/).length)
        .toBeGreaterThan(0),
    );
  });
});
