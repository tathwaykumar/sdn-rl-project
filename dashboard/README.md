# SDN + RL Dashboard

A web interface for watching the SDN prototype actually run: the real topology,
the routing path the switches are currently using, live flow tables, the RL
agent's decisions, live metrics, and A/B/C comparisons over completed runs.

---

## 1. The central design decision

**The dashboard never queries the switches.** It reads telemetry the controller
and experiment runner already write to disk:

| What you see | Where it comes from | Freshness |
|---|---|---|
| Metrics, utilisation, controller load | `raw/snapshots.csv` (controller, flushed per row) | ~1 s |
| RL decisions, Q-values, rewards | `raw/decisions.csv` (controller, flushed per row) | ~1 s |
| Flow tables | `raw/flow_dumps/*.txt` (runner, real `ovs-ofctl dump-flows`) | ≤ 5 s |
| Latency and probe loss | `raw/ping_h1.txt` (line-buffered `ping -D -O`) | ~1 s |
| Link up/down | `raw/events.csv` (controller `OFPPortStatus` handler) | ~1 s |

This matters for correctness, not just convenience. The project **measures
controller message load as an experimental result**, and its reward function
counts reactive messages (PacketIn + FlowMod). A dashboard that polled OpenFlow
for statistics would inflate exactly that measurement, and results would differ
depending on whether anyone was watching. Reading files adds **zero OpenFlow
messages**, so A/B/C comparisons are identical with the dashboard open or shut.

The cost, stated honestly in the UI: flow-table views are as fresh as the
5-second dump cadence, not instantaneous. Every panel shows the age of its data
and marks it stale when it gets old.

---

## 2. Prerequisites

Already satisfied if you followed the main README. Otherwise:

```bash
sudo apt-get install -y python3-fastapi python3-uvicorn python3-websockets \
                        python3-httpx nodejs npm
```

Verified with FastAPI 0.118.0, uvicorn 0.38.0, Node 22.22.1, npm 9.2.0.

---

## 3. Running it

```bash
./dashboard/run_dashboard.sh          # live mode
./dashboard/run_dashboard.sh --mock   # demo mode, synthetic data
```

Then open **http://127.0.0.1:5173**.

### To start experiments from the browser

The dashboard is read-only until you also run the privileged launcher, in a
second terminal:

```bash
sudo python3 -m dashboard.launcher.daemon
```

With it running, the Experiments page can start, stop and inject link failures
into real Mininet experiments. The API itself stays unprivileged throughout —
it talks to the daemon over a Unix socket whose entire vocabulary is five
operations, and the daemon builds the runner command itself from a fixed
allowlist. No sudoers changes are made.

Without it, everything else still works and the Experiments page shows the
exact command to run in a terminal instead.

Full security model, systemd unit and limitations: **`dashboard/launcher/README.md`**.

Or start the two halves separately:

```bash
# Backend — from the project root
python3 -m uvicorn dashboard.backend.app:app --host 127.0.0.1 --port 8000

# Frontend
cd dashboard/frontend && npm install && npm run dev
```

| Port | Service | Notes |
|---|---|---|
| 5173 | Vite dev server | proxies `/api` and `/ws` to 8000, so the browser is same-origin |
| 8000 | FastAPI backend | OpenAPI docs at `/docs` |
| 6653 | **The SDN controller** | not the dashboard; never bound by it |

### Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `SDNRL_DASHBOARD_MOCK` | unset | `1` enables mock mode; every payload is labelled `mock` and a banner is always visible |
| `SDNRL_API_HOST` / `SDNRL_API_PORT` | `127.0.0.1` / `8000` | backend bind address |
| `SDNRL_UI_PORT` | `5173` | frontend port (startup script only) |

---

## 4. Live mode vs mock mode

**Live mode** (default) shows real data when an experiment is running, and says
so plainly when one is not. It does not invent activity to fill the screen: with
no experiment running, the topology shows the configured network with no link
state, and the metric panels say why they are empty.

**Mock mode** exists so the UI can be developed and demonstrated without
Mininet — which needs root and exclusive use of the OpenFlow port. It is
unmistakable: a persistent banner across the top, and a `MOCK DATA` badge on
every panel. Mock data is never blended with real measurements.

The badge on each panel is the contract:

| Badge | Meaning |
|---|---|
| `LIVE` | measured from the experiment running right now |
| `RECORDED` | read from a completed run's stored measurements |
| `CONFIG` | static project configuration, not a measurement |
| `MOCK DATA` | synthetic; not a measurement of any network |
| `UNAVAILABLE` | genuinely not measurable right now |

---

## 5. Pages

**Overview** — system status, live metric cards, latest agent decision with its
installation status, recent events.

**Live Topology** — the real five-switch topology from `topo/spec.py`. Link
colour reflects **rules installed at the edge switches**, not the agent's latest
choice: a path is only drawn as carrying traffic once rules for it exist. Click
a switch or link for details (ports, bandwidth, delay, utilisation per
direction, link state).

**Flow Tables** — per-switch rules with cookie, priority, match, actions, idle
and hard timeouts, age, packet and byte counters. Table-miss and infrastructure
rules are visually distinguished from routed rules and excluded from occupancy.
Includes search, filtering, sorting, the idle-timeout distribution and occupancy
over time.

**RL Decisions** — state (decoded into its bins), chosen path and timeout,
greedy vs exploratory, ε, Q before/after, TD error, reward with a per-term
breakdown, and whether the switches have confirmed the decision.

**Live Metrics** — throughput, latency (median/p95), loss, utilisation,
occupancy, table misses and controller load, with units, measurement interval
and data age. Instantaneous readings and interval aggregates are labelled
differently. A panel lists what is *not* available live and why.

**Experiments** — start a run with a validated variant/workload/seed/duration,
view status and runner output, stop a run the dashboard started (addressed by
experiment id, so a stale tab cannot stop something it is not looking at), and
— while a dashboard-started run is live — inject a link failure or recovery on
any of the five core links. The injection is applied by the runner to the real
Mininet session and observed by the controller over OpenFlow; the topology
view only redraws once the switches confirm it.

**A/B/C Comparison** — medians with IQR across seeds, relative change against a
baseline, and a verdict that only claims a difference when the 95% confidence
intervals do not overlap. Filter by workload, variant and seed. CSV export.

**History** — every run including interrupted and incomplete ones, with
provenance (git commit, package versions, reward weights), per-run CSV export,
and links to the generated figures and report.

---

## 6. Security and privilege separation

The API **runs unprivileged and does not try to become root.** Reading
telemetry needs no privileges at all.

Mininet does need root, so experiment control is served one of three ways,
reported by `/api/capabilities` as `start_mechanism`:

| `start_mechanism` | What it means |
|---|---|
| `launcher` | **Recommended.** A small root daemon owns the privileged work; the API is a client over a Unix socket. |
| `root_api` | The API itself was started as root. It works, but a root-capable web service is a larger attack surface than necessary. |
| `none` | Neither. Experiments cannot be started from the browser; the UI shows the exact terminal command instead. |

The launcher's trust boundary is deliberately tiny: five operations, all
parameters drawn from fixed allowlists, the command line assembled by the
daemon from its own constants, `shell=False` everywhere, peer credentials
checked with `SO_PEERCRED` on top of `0660` socket permissions, stop addressed
by experiment id rather than pid or pattern, and no sudoers changes anywhere.
See `dashboard/launcher/README.md` for the full model and threat notes.

Other deliberate limits:

* **No arbitrary command execution.** The runner is launched as an argv list
  with `shell=False`. Variant, workload, seed, duration and idle timeout are
  validated against the runner's own tables; there is no free-form command
  field anywhere in the API.
* **One experiment at a time.** Starting is refused (HTTP 409) whenever any
  experiment is running, including a matrix sweep started from a terminal,
  because Mininet, Open vSwitch and TCP 6653 are exclusive resources.
* **Stop only affects what the dashboard started.** An experiment launched from
  a terminal is reported but is not stoppable from the browser. Silently killing
  someone's two-hour matrix because a web button was clicked is not a tradeoff
  worth making.
* **Stopping is graceful.** SIGTERM first, with a long wait, so the runner tears
  down Mininet, flushes telemetry and writes its manifest. SIGKILL only as a
  last resort, and the response says so.
* **Run data is read-only.** Nothing in the backend writes to a run directory.
  Exports are generated on demand.
* **Path traversal is blocked** on run IDs and artefact names.

There is **no authentication**. This is a local research tool bound to
loopback; do not put it on a network.

---

## 7. Tests

```bash
# Backend (part of the project suite)
python3 -m pytest tests/test_dashboard_api.py -q

# Frontend
cd dashboard/frontend && npm test
```

The backend tests cover API schemas, that the reported topology matches
`topo.spec`, flow-dump parsing and rule classification, decision parsing with
null-preserving semantics, the decision-vs-installed verification logic,
experiment input validation, concurrent-experiment refusal, safe-stop refusal,
comparison direction and confidence-interval handling, path-traversal guards,
and that mock mode labels every payload.

The frontend tests cover formatting (missing values must never render as
numbers), source badges, metric cards, empty and error states, websocket
connect/reconnect/backoff, fallback to polling, and API error propagation.

---

## 8. Known limitations

* **Flow tables lag by up to 5 seconds** — the runner's dump cadence. Shown as
  data age on every panel; marked stale past 12 seconds.
* **Flow tables only exist while a run is active or for runs that recorded
  them.** With no experiment running there is nothing to show, and the UI says
  so rather than displaying an empty table.
* **Live iperf3 throughput is not available.** iperf3 writes its JSON only when
  a test finishes, so per-flow throughput does not exist until a run (or a
  failure-workload segment) ends. The live throughput shown is the switch-counter
  measurement; the iperf3 figure appears in the comparison views.
* **Recovery time is not a live metric.** It is defined relative to a link
  failure, so it only exists for `failure` runs, after the failure.
* **Link failure from the UI needs a dashboard-started experiment.** The runner
  owns the Mininet session, so injection is only offered while an experiment
  this dashboard started is running — that is the only case where the runner
  can safely coordinate the change. The `failure` workload also schedules its
  own cut of `s1-s2` partway through any run.
* **Experiments cannot be started from the browser unless the privileged
  launcher is running.** Without it the Experiments page shows the exact
  terminal command instead of a button that would fail.
* **No direct `ovs-ofctl` query button.** It would need root *and* would add
  OpenFlow traffic to a system whose controller load is a measured result. The
  capability is reported as unavailable with that reason rather than hidden.
* **Variant A exposes no Q-values or ε** because it has no agent. Those fields
  render as `n/a`, never as `0`.
* **No authentication, no multi-user support, no persistence of UI state.**
