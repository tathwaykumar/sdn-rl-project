# Limitations

Written up front rather than at the end, because a limitation discovered after
the results are in tends to get written to flatter them.

Everything here constrains how far the measurements in this project can be
pushed. None of it is a reason to distrust the *relative* comparison between
variants A, B and C — that comparison is run on one host, with identical seeded
traffic, in the same session. All of it is a reason to be careful about absolute
numbers and about generalising to real networks.

---

## 1. WSL2 is an emulation environment, not a testbed

**Shared, non-real-time scheduling.** The whole experiment runs inside a WSL2 VM
on a Windows host with a desktop workload beside it. Mininet hosts are network
namespaces sharing one kernel and one CPU pool, and nothing is isolated or
pinned. Latency and jitter figures therefore carry scheduling noise that a
dedicated testbed would not have, and tail latency (p95) is noisier than the
median.

**Consequence.** Absolute latency numbers are not comparable with published
hardware results and should not be quoted as if they were. Within-session
comparisons between variants remain valid because all variants are subject to
the same noise, which is why every workload is run for all three variants with
the same seeds.

**No physical links.** Every link property — 5/10 Mbit/s capacity, 1/2 ms delay,
100-packet queues — is imposed by `tc`/HTB on a veth pair. There is no
propagation delay, no serialisation on a real NIC, no physical loss. The
`sch_htb: quantum ... is big` warning that `tc` emits at these low rates is
cosmetic, but it is a reminder that the shaper is being used near the edge of
its comfortable range.

**Clock.** Timestamps come from the WSL2 guest clock, which can drift against the
Windows host. Measurements are all intra-run deltas, so drift does not corrupt
them, but cross-run absolute timestamps should not be compared at sub-second
resolution.

---

## 2. The topology is small and fixed

Five switches, four hosts, two candidate paths, one bottleneck pair. The routing
decision is binary (path A or path B).

**What this means.** The result is a statement about a two-path choice under
controlled asymmetry. It is *not* evidence about how tabular Q-learning scales to
a datacentre fabric, where the action space grows combinatorially and a table
stops being a viable representation. Nothing here tests topology discovery,
multi-controller coordination, or equal-cost multipath.

The topology was chosen so the routing decision is *meaningful* (path A is better
at low load, worse at high load) rather than realistic. A realistic topology with
a non-obvious optimum would have made it impossible to tell a learned policy from
a lucky one.

---

## 3. Short runs, few seeds

Each run is 90 seconds at a 1 s decision interval, so **about 90 learning steps**,
with five seeds per (variant, workload) cell.

**Consequences, in order of severity:**

- **The agent is still learning when the run ends.** Epsilon decays over the
  first half of the run; the second half is closer to the converged policy, which
  is why every table is reported for both the full run and the second half. This
  is a learning-during-evaluation setup, not train-then-test. A policy that would
  be better after an hour of experience is not measured here.
- **State coverage is partial.** 144 states and ~90 steps per run means most
  states are never visited in a single run, and visited ones are visited a
  handful of times. `policy.json` records the coverage actually achieved; read it
  before taking any single state's learned value seriously.
- **n = 5 is a small sample.** The reported 95% confidence intervals are wide and
  are computed with a t critical value, not a normal one. Where intervals
  overlap, the honest reading is "not distinguishable at this sample size",
  not "no difference". No significance test is claimed, because with five runs
  per cell none would be worth claiming.
- **Each run starts from an empty Q-table.** There is no transfer between runs or
  seeds. That keeps seeds independent, at the cost of making every run a
  cold start.

---

## 4. What the reward can and cannot see

The reward is computed **only** from OpenFlow counters, because that is what a
real controller has. It therefore uses *maximum link utilisation as a proxy for
congestion and queueing delay* — OpenFlow statistics cannot measure latency.

**Consequence.** The agent optimises a proxy. If utilisation and latency come
apart (for instance, a lightly-utilised but badly-queued link), the agent is
blind to it. The evaluation measures real latency with `ping`, independently, so
this shows up as a weaker correlation between reward and reported latency rather
than as a hidden error — but the agent cannot fix what it cannot see.

The reward weights (throughput 1.0, utilisation 0.4, loss 0.6, occupancy 0.25,
controller messages 0.15) were chosen by hand and are **not** tuned. They are
recorded in every run's `config.json`. Different weights would produce a
different policy, and the project does not establish that these are good weights
— only that the results shown were produced by these.

---

## 5. Measurement caveats, metric by metric

| Metric | How it is measured | Caveat |
|---|---|---|
| Throughput | edge-switch egress byte counters, cross-checked against iperf3 `--json` | OF counters include all host-bound traffic, including ICMP probes; iperf3 counts only its own flows. The two agree closely but are not identical by construction. |
| Latency | `ping -D -O -i 0.2`, median and p95 | Single probe stream (h1→h3). Not a full path-latency matrix. Subject to WSL2 scheduling noise. |
| Packet loss | ping loss fraction; OF `tx_dropped` on core ports | Two different things: ping loss is end-to-end for one flow, OF drops are per-link queue overflow. They are reported separately and should not be conflated. |
| Max link utilisation | per-interval egress bytes / configured capacity | Capacity is the *configured* HTB rate, not a measured one; HTB does not deliver exactly its nominal rate. |
| Flow-table occupancy | count of priority≥10 rules | Excludes the table-miss and IPv6 rules, which are controller plumbing, not flows. A different convention would give different absolute numbers. |
| Rule-hit ratio | `(lookups − table-miss packets) / lookups` | `lookup_count` also counts the few IPv6 frames caught by the priority-1 discard rule, a negligible but non-zero inflation of the denominator. |
| Table misses | packets matched by the priority-0 entry | In OF1.3 the table-miss entry is a real rule, so `lookup_count − matched_count` is ≈0 and unusable. Cross-validated against controller-side `PacketIn` counts, which matched exactly (43 = 43) in the validation run. |
| Controller messages | counted inside the controller | Only reactive messages (PacketIn + FlowMod) enter the reward; periodic stats polling is fixed overhead the policy cannot influence. Totals for both are recorded. |
| Recovery time | time from link-down to the next successful ping reply | Resolution is bounded by the 0.2 s probe interval, so recovery faster than ~200 ms reads as ~200 ms. Reported alongside the controller's own event timestamps. |

---

## 6. Design decisions that constrain the results

- **ARP is proxied by the controller**, not flooded. The topology contains a loop,
  so flooding broadcast traffic would cause a storm. This removes ARP from the
  measurement entirely, which is convenient but less realistic than a network
  that must handle broadcast.
- **IPv6 is discarded** at priority 1. Mininet hosts emit router solicitations
  and multicast listener reports; without this they become PacketIns and pollute
  the controller-load and table-miss measurements with traffic unrelated to the
  experiment.
- **Both directions of a flow are installed together, on the same path.** A real
  controller may well do this, but it means forward and reverse traffic are
  always symmetric, which is a simplification and is what makes the RTT
  measurement interpretable at all.
- **Flows are matched on the full 5-tuple.** This maximises flow-table pressure
  (one rule per connection per switch), which is what makes occupancy and
  timeouts interesting. Coarser aggregation would reduce the effect being
  studied.
- **On link failure the controller flushes its routed rules.** This makes
  reconvergence fast and measurable, but it is a deliberately aggressive
  strategy; a production controller might reroute incrementally.
- **The baseline (variant A) reconverges on failure.** It recomputes over the
  remaining paths rather than black-holing. Without that it would lose the
  recovery comparison by construction and the result would be meaningless.

---

## 7. Software-stack caveats

- **os-ken 4.1.1 is used as a library.** The `osken-manager` CLI no longer exists
  in either the distro package or upstream, so `controller/launcher.py` drives
  `AppManager` directly. This is a supported public API, but it is not the path
  most Ryu/os-ken tutorials take, so behaviour may differ from documented
  `ryu-manager` defaults.
- **os-ken's hub defaults to native OS threads**, not eventlet greenthreads
  (`OSKEN_HUB_TYPE` defaults to `native`). Event handlers and the polling loop
  therefore run on different real threads and share state under an explicit lock.
  This was found during development, not assumed.
- **os-ken 4.1.1 emits a `SyntaxWarning` on Python 3.14** (`'break' in a
  'finally' block`, in `vrrp.py`). It is harmless and unrelated to anything used
  here; it is suppressed in run logs only so it does not head every file.
- **Python 3.14 is newer than os-ken upstream formally claims** (classifiers stop
  at 3.13). The Ubuntu package is built against 3.14 and the full test suite and
  all integration gates pass, but this combination is not upstream-validated.

---

## 8. Threats to validity, stated plainly

1. **The topology was designed so that adaptive routing can win.** That is
   legitimate — you cannot measure an effect in a setting where it cannot occur —
   but it means the result is "joint RL optimisation helps *in a network built to
   have a load-dependent optimum*", not "RL routing helps in general".
2. **Variant C has a larger action space than B with the same learning budget.**
   More actions with the same number of steps means less experience per action.
   If C does not beat B, that is at least partly an exploration-budget artefact
   and not purely evidence that joint control fails to help.
3. **All three variants share one codebase and differ only in `policy_step()`.**
   This removes implementation confounds, but it also means a bug in the shared
   path affects all three equally and would not show up as a difference between
   them.
4. **The evaluation and the reward are separated, but both are produced by the
   same system.** An error in `telemetry/metrics.py` would affect the reward and
   the OF-derived results together. This is why host-side iperf3 and ping
   measurements are reported alongside the controller's own numbers: they come
   from different code and different processes.
