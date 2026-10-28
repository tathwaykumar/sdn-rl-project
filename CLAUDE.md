# sdn-rl-project — CLAUDE.md

RL-based SDN traffic routing + flow-table optimization research prototype, with
a read-only web dashboard. Full details live in `README.md` (project) and
`dashboard/README.md` (dashboard) — don't duplicate them here, read them when
you need specifics this file doesn't cover.

## Architecture

- **Topology/sim**: `topo/` (Mininet + OVS), `controller/` (os-ken OpenFlow 1.3
  apps — `baseline.py`=A, `rl_route.py`=B, `rl_joint.py`=C, shared Q-learning in
  `rl_mixin.py`), `agent/` (Q-learning, state/action/reward).
- **Experiments**: `experiments/` (runner, workloads, smoke test) →
  `results/runs/<run>/` (raw data, **never edited after a run**). Each run's
  `raw/events.csv` holds discrete controller events (`controller_start/stop`,
  `switch_connected/disconnected`, `flow_installed`, `flow_removed`,
  `link_state_change`, `routed_flows_flushed`) — `flow_installed` only exists
  on runs recorded after `8207540`.
- **Analysis**: `analysis/` (aggregate, verify_behaviour, plots, report) →
  `analysis/out/` (derived, regenerated, never edited).
- **Dashboard**: `dashboard/backend` (FastAPI, unprivileged, reads files from
  `results/runs/`, never queries switches) + `dashboard/frontend` (React 18 +
  TypeScript + Vite + Tailwind v4 + Recharts + ReactFlow).
- **Dashboard run commands**:
  ```bash
  ./dashboard/run_dashboard.sh          # live mode, http://127.0.0.1:5173
  ./dashboard/run_dashboard.sh --mock   # demo mode, synthetic data
  # or separately:
  python3 -m uvicorn dashboard.backend.app:app --host 127.0.0.1 --port 8000
  cd dashboard/frontend && npm run dev   # :5173, proxies /api and /ws to :8000
  ```
  Experiment control from the browser needs `sudo python3 -m dashboard.launcher.daemon`
  running separately; without it the Experiments page shows the terminal command instead.

## Current dashboard status (verify against `git log` / `git status` if stale)

Branch `dashboard/console-redesign`, most recent work first:

- `8494dd4` Fixed Overview and Live Topology going blank for every **recorded**
  run: Learning Snapshot, the topology mini-map, switch rule-count badges and
  Confirmed Forwarding all read only the live websocket snapshot (forced null
  once a run is selected) and had no historical fallback. Each now fetches its
  own data for a selected run.
- `3cf53cd` Fixed Metrics & Analytics headline/supporting-reading cards reading
  as all-zero for a completed run (they used the literal last row of the
  series, not even filtered for validity — a run's last row is reliably a
  quiet post-traffic moment). A recorded run now shows each reading's own peak
  interval, labelled `(peak)`. Also added a plain-language caption under every
  chart.
- `2b245af` Simplified the flow table to Kind/Source/Destination/Hop, with a
  per-row expandable Details panel for everything else (priority, timeouts,
  age, counters, cookie, and the switch-reported install timestamp).
- `fced158` Expanded Decision Inspector's own explanation of what it's doing
  and why (it's where the project's "the learned controller does better"
  claim gets checked against one real decision at a time).
- `8207540` The controller now logs a `flow_installed` event (switch, host
  pair, protocol, path, idle timeout, rules installed) on every table miss,
  in `events.csv` alongside the pre-existing `flow_removed`/`link_state_change`
  events — previously FlowMod installs were only counted (`flow_mod_rate`),
  never logged individually. Only present in runs recorded after this change.

Earlier: `cd8e36f` (Reward Explorer/Decision Inspector/Comparison/Experiments
got their own always-visible explanatory text), `acbefef` (console rebuilt as
an emerald-dark instrument). **Confirm current state with `git status`/
`git diff` before assuming anything here is still accurate** — this file is
not updated automatically.

## A/B/C variants, workloads, reward (verified from README.md)

| Variant | Routing | Flow-rule idle timeout |
|---|---|---|
| A | conventional shortest-path | fixed |
| B | Q-learning chooses path | fixed |
| C | Q-learning chooses path | Q-learning chooses timeout |

Workloads: `light` (1 flow, path A should suffice), `heavy` (2 saturating flows,
exceeds path A capacity), `churn` (24 short flows, stresses flow table),
`failure` (`heavy` + scripted link cut/repair). All seeded via
`random.Random(seed)` — same `(workload, seed)` gives identical traffic to all
three variants.

Reward (computed **only from OpenFlow counters**, agent never sees ping/iperf3):
```
r = 1.00 * throughput
  - 0.40 * max link utilisation
  - 0.60 * packet loss
  - 0.25 * flow-table occupancy
  - 0.15 * reactive controller messages
```

## Research-integrity constraints (do not violate without explicit request)

- `results/runs/` and `analysis/out/` are append-only from the research
  pipeline's point of view — never hand-edit run data or regenerated analysis
  output yourself.
- The dashboard backend is **read-only** against run data; it must never write
  into a run directory, and it must not query switches directly (that would
  add OpenFlow traffic the project measures as a result).
- The reward formula, its weights, state/action discretisation, and experiment
  configs encode specific research assumptions — do not change them unless the
  user explicitly asks.

## Current unfinished task

None known as of this update. The beginner-friendly-explanations pass
(Reward Explorer, Decision Inspector, A/B/C Comparison, Experiments) and the
recorded-run data-wiring bugs found while reviewing it are both committed
(see above). Mobile-layout verification across the console has not been
explicitly re-checked this session — confirm before assuming it still holds.
If you start new work, update this section before ending the session so the
next one doesn't have to rediscover it from `git log`.

## Usage-saving rules for Claude Code sessions here

1. Inspect only the files relevant to the task. Use targeted `grep`/search, and
   read large files in small ranges — don't dump a whole file to re-derive
   context already established in this conversation.
2. Make the smallest change that satisfies the request. No unrelated refactors,
   no re-explaining things already said earlier in the same session.
3. Run targeted tests first (e.g. `pytest tests/test_dashboard_api.py -q`,
   `npm test -- <file>`). Only run the full suite (`pytest tests/ -q`,
   `npm test`) when the change is broad enough to justify it.
4. Use Playwright only when browser interaction or visual verification is
   actually necessary — not as a default check after every edit.
5. Never repeatedly re-inspect the same screenshot, rerun a check that already
   passed, or re-derive a fact already established in this conversation or
   documented in `README.md` / `dashboard/README.md` / `docs/`.
6. Never change RL logic, reward weights, experiment configs, or research
   results/conclusions unless the user explicitly asks for it.
7. Keep responses concise: report which files changed, which tests ran, and
   any blockers. Skip restating things already said.
8. Ask before destructive operations (`sudo`, deleting run data, force-git) or
   before running a full experiment matrix (~2 hours, needs root + Mininet).

MCP servers (incl. Playwright) are configured in `.mcp.json` — do not install,
remove, or globally disable any MCP server, and do not edit `.mcp.json`.
