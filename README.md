# RL-Based SDN Traffic Routing and Flow-Table Optimization

A working Software-Defined Networking prototype that uses tabular Q-learning to
jointly optimise **traffic routing** and **switch flow-table management**
(specifically flow-rule idle timeouts), and measures whether doing both together
beats doing either conventionally.

Three variants are compared under identical, seeded traffic:

| Variant | Routing | Flow-rule timeouts |
|---|---|---|
| **A** | conventional shortest-path (fewest hops) | fixed for the whole run |
| **B** | Q-learning chooses the path | fixed for the whole run |
| **C** | Q-learning chooses the path | Q-learning chooses the timeout |

A and B differ in exactly one respect; B and C differ in exactly one respect.
Any measured difference between B and C is therefore attributable to timeout
control alone.

---

## 1. Requirements

Verified working on this machine:

| Component | Version |
|---|---|
| OS | Ubuntu 26.04.1 LTS under WSL2 (kernel 6.18.54.1-microsoft-standard-WSL2) |
| Python | 3.14.4 (system interpreter; no virtualenv needed) |
| Mininet | 2.3.0 |
| Open vSwitch | 3.7.1 (kernel datapath module loaded) |
| Controller | `python3-os-ken` 4.1.1 (OpenFlow 1.3) |
| Traffic tools | iperf3 3.20, iputils ping |

### Why os-ken and not Ryu

Ryu's last release is **4.34, from May 2020**, and it declares support only up to
Python 3.7. On Python 3.14 it cannot work: `distutils` was removed in 3.12 and
`inspect.getargspec` in 3.11, and both are on Ryu's startup path. `os-ken` is the
OpenStack-maintained fork with the same application API (`os_ken.base.app_manager`,
`os_ken.controller.ofp_event`, `@set_ev_cls`), and Ubuntu 26.04 ships it built
against this system's Python 3.14.

One wrinkle: **neither the Ubuntu package nor upstream os-ken 4.2.2 still ships
the `osken-manager` CLI** — os-ken is now maintained as a library for OpenStack
Neutron and `os_ken/cmd/` was dropped. `controller/launcher.py` performs the same
startup sequence directly against the public `AppManager` API, which is a few
dozen lines and removes a dependency rather than adding one.

### Install

```bash
sudo apt-get update
sudo apt-get install -y python3-os-ken iperf3 python3-networkx \
                        python3-numpy python3-pandas python3-matplotlib python3-pytest
```

No virtualenv is required or recommended: every dependency is a distro package
built for the system interpreter, which keeps PEP 668 (`EXTERNALLY-MANAGED`) out
of the picture entirely.

---

## 2. Quick start

```bash
# 1. Unit tests - no root, no Mininet, ~0.2s
python3 -m pytest tests/ -q

# 2. End-to-end gate - starts the controller, builds the topology,
#    pings, and reads the rules back off the switches
sudo python3 -m experiments.smoke

# 3. A single experiment run
sudo python3 -m experiments.run_experiment --variant C --workload heavy --seed 1

# 4. The full matrix (3 variants x 4 workloads x 5 seeds = 60 runs, ~2 hours)
sudo python3 -m experiments.run_experiment --matrix --seeds 5 --duration 90

# 5. Analysis - reads results/runs/, writes analysis/out/
python3 -m analysis.aggregate
python3 -m analysis.verify_behaviour
python3 -m analysis.plots
python3 -m analysis.report
```

Steps 2-4 need root because Mininet does. The controller itself does not; it runs
as a separate process so that boundary stays explicit.

> **If you are running under WSL** and `sudo` prompts for a password in a script,
> you can instead invoke the distro as root from Windows
> (`wsl -d Ubuntu -u root -- bash -c '...'`). This uses WSL's own user selection
> and changes nothing about the system's security configuration — no sudoers
> edits, no passwordless rules.

---

## 3. Topology

```
    h1 --.                                  .-- h3
          +-- s1 ------- s2 ------- s4 -----+
    h2 --'     |                    |        '-- h4
               '--- s3 ---- s5 -----'

  Path A:  s1 -> s2 -> s4         2 hops, 1 ms/link,  5 Mbit/s
  Path B:  s1 -> s3 -> s5 -> s4   3 hops, 2 ms/link, 10 Mbit/s
```

Five switches, four hosts, two **link-disjoint** paths between the edge switches.

The asymmetry is the whole experiment. Path A is shorter but narrower, so it is
genuinely the better choice at low load and genuinely the worse one at high load.
A fixed policy cannot be right in both regimes; a state-conditioned policy can.
Hop-count shortest-path routing always picks A, which is what gives the learned
variants something real to improve on — and the `light` workload checks they do
not simply learn "always take the big pipe".

`topo/spec.py` is the single source of truth for addresses, port numbers and
paths. Both the Mininet builder and the controller import it, and the runner
aborts if the live port numbering does not match it — a silent port mismatch
would leave every routing result meaningless while `pingall` still passed.

---

## 4. How the agent works

**State** (144 discrete states) — path A utilisation (4 bins) x path B
utilisation (4 bins) x flow-table occupancy (3 bins) x table-miss rate (3 bins).

**Action** — variant B: which path. Variant C: `(path, idle timeout)` where the
timeout is one of 5 s / 15 s / 60 s.

**Reward** — computed **only from OpenFlow counters**:

```
r = 1.00 * throughput
  - 0.40 * max link utilisation      (congestion / queueing proxy)
  - 0.60 * packet loss
  - 0.25 * flow-table occupancy
  - 0.15 * reactive controller messages
```

The agent never sees the ping or iperf3 measurements used to score the
experiment. That separation is deliberate: if the reward were built from the same
numbers the results tables report, a good score would only show that the
optimiser optimised its own objective. Because the evaluation is independent, an
improvement in the reported metrics is an out-of-sample consequence of the policy.

The occupancy and controller-message weights are non-zero on purpose. If they
were zero, variant C's extra timeout actions could not pay off and the comparison
would be rigged against it before a single run.

**Decision cadence** — the agent acts **once per polling interval** (1 s), not per
packet. Each interval is one MDP step: state `s_t`, one action applied to every
flow set up during the interval, scalar reward `r_t` measured from that interval,
next state `s_{t+1}`. Per-packet actions would make it impossible to attribute a
throughput measurement to any individual decision.

---

## 5. Workloads

| Name | What it does | What it is for |
|---|---|---|
| `light` | one 1 Mbit/s flow | path A is genuinely best; checks the agents do not over-use path B |
| `heavy` | two saturating TCP flows | more load than path A can carry; adaptive routing should pay |
| `churn` | 24 short flows, distinct 5-tuples | stresses the flow table, where timeout control matters |
| `failure` | `heavy` plus a mid-run cut of link s1-s2, then repair | reconvergence and recovery time |

All are driven by `random.Random(seed)`, so a given `(workload, seed)` offers
**identical traffic to all three variants**.

---

## 6. Repository layout

```
topo/spec.py              single source of truth: ports, addresses, paths
topo/diamond.py           Mininet builder + live port-mapping verification
controller/launcher.py    replacement for the missing osken-manager CLI
controller/base_app.py    all non-policy logic (OF1.3, ARP proxy, stats, failover)
controller/baseline.py    variant A
controller/rl_route.py    variant B      } differ only in the action space
controller/rl_joint.py    variant C      }
controller/rl_mixin.py    shared Q-learning plumbing for B and C
agent/                    Q-learning, state discretisation, actions, reward
telemetry/metrics.py      raw counter capture and per-interval derivation
experiments/              runner, workloads, controller process, smoke test
analysis/                 parsers, statistics, aggregation, verification, plots
sim/offline_env.py        SIMULATED toy model - tests only, never a result
tests/                    67 unit tests (no root, no Mininet)
results/runs/<run>/       one directory per run; raw data, never edited
analysis/out/             derived tables and figures; regenerated, never edited
docs/                     limitations and assumptions
```

---

## 7. What a run directory contains

```
results/runs/20261023-080215-A-light-seed1/
  config.json       everything needed to reproduce: git commit, package
                    versions, seed, reward weights, agent hyperparameters
  manifest.json     which artefacts actually exist, and whether the run is
                    complete; the analysis excludes incomplete runs and
                    reports how many it excluded
  workload.json     the seeded traffic schedule as actually executed
  controller.log    the controller's own output
  policy.json       learned greedy policy and state coverage (B and C)
  agent.qtable.json the learned Q-table
  raw/
    snapshots.csv          per-interval derived metrics
    decisions.csv          every policy step: state, action, reward, Q values
    raw_port_stats.csv     untouched OpenFlow port counters
    raw_flow_stats.csv     untouched flow-table counters
    raw_table_stats.csv    untouched table counters
    controller_counters.csv PacketIn / FlowMod / etc. counted at the controller
    events.csv             switch connects, link changes, rule expiries
    flow_dumps/*.txt       ovs-ofctl dump-flows snapshots every 5 s
    iperf3_*.json          iperf3's own --json output, one file per flow
    ping_*.txt             raw ping output with -D timestamps and -O loss lines
```

**Run directories are never modified after the run.** `analysis/` reads them and
writes elsewhere, so every published number traces back to an untouched
measurement file and a parser fix never costs an experiment.

---

## 8. Verifying the agent actually changes switch behaviour

This is the claim most easily faked, so it is checked against the **switches**,
not the controller's logs. A controller can log a decision it never installed; a
flow table cannot lie about what it is holding.

```bash
python3 -m analysis.verify_behaviour
```

It parses `ovs-ofctl dump-flows` snapshots and tests five things:

1. **Paths used** — do installed rules use more than one egress port?
2. **Timeouts used** — does C install several `idle_timeout` values, and do A
   and B install exactly one (as they claim)?
3. **Decision-to-rule agreement** — each rule's `duration` field gives its
   install time, so every rule is checked against the decision that was *in
   force when it was installed*, not the latest one.
4. **Variant divergence** — do the variants differ from each other in the
   dataplane, or are they the same program with different labels?
5. **Null control** — a frozen, all-zero agent must collapse to the
   deterministic tie-break:

```bash
sudo python3 -m experiments.run_experiment --variant C --workload heavy \
     --seed 1 --frozen-agent --tag nullcontrol
```

If the trained agent behaves identically to the frozen one, the learned values
are doing nothing. There is also an **action-shuffle** diagnostic
(`--shuffle-actions`) which permutes the action→(path, timeout) mapping: if the
metrics do not change under shuffling, the actions are not reaching the dataplane
and any apparent learning is an artefact.

---

## 9. Results integrity

- Raw first: every counter is written out verbatim before anything is derived.
- Missing stays missing: a metric that could not be measured is recorded as
  missing and excluded from statistics; `n` is reported in every table and on
  every figure. Nothing is interpolated or defaulted to zero.
- Invalid intervals (counter resets) are dropped, not clamped.
- Five seeds per cell; tables report medians with interquartile ranges and means
  with 95% confidence intervals. With n=5 these are wide, and the report says so
  rather than implying significance it cannot support.
- Simulated results from `sim/` are labelled as simulated everywhere and never
  appear in the results tables.
- No number in the report is typed by hand; all are generated by `analysis/`.

See `docs/LIMITATIONS.md` for what this setup cannot measure and
`docs/ASSUMPTIONS.md` for the choices behind the experiment design.

---

## 10. Troubleshooting

**"port 6653 is already in use"** — a controller from an interrupted run is still
holding it. The runner kills stale launchers automatically; to do it by hand:

```bash
pkill -f controller.launcher
```

This matters more than it looks: an orphaned controller used to cause the next
run's controller to fail to bind while Mininet happily connected to the *old*
process, so the run silently measured stale code. The launcher now exits with
status 4 if its listener dies, and the runner refuses to start against a
pre-bound port.

**Stale Mininet state** — `sudo mn -c`. The runner does this before and after
every run.

**`sch_htb: quantum ... is big` warnings** — cosmetic, from `tc` at the low link
rates we configure deliberately. Shaping still applies.

**Permission denied on `results/`** — runs execute as root; the runner chowns
output back to the project owner afterwards. If a run was interrupted before that
step, `sudo chown -R "$USER" results/`.
