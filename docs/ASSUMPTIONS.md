# Experimental assumptions

The choices a reader has to accept for the results to mean what they claim, and
the reasoning behind each. `docs/LIMITATIONS.md` covers what the setup *cannot*
do; this file covers what it *decided* to do.

Every parameter named here is recorded in each run's `config.json`, so any
result can be traced to the configuration that produced it.

---

## A1. The topology has a load-dependent optimum, by construction

| | Path A | Path B |
|---|---|---|
| hops | 2 | 3 |
| per-link delay | 1 ms | 2 ms |
| bottleneck capacity | 5 Mbit/s | 10 Mbit/s |

**Assumption:** a network in which the best route depends on current load is the
right setting for testing load-adaptive routing.

**Why:** if one path dominated at every load, the optimal policy would be a
constant, a static configuration would match it, and the experiment could not
distinguish a learned policy from a fixed one. The asymmetry makes "which path
is better" a genuine function of state.

**What it costs:** the finding is about networks with this property. It is not
evidence that RL routing helps where no such tension exists.

---

## A2. Shortest-path-by-hop-count is a fair conventional baseline

**Assumption:** variant A represents conventional routing honestly.

**Why:** fewest-hops is what OSPF-style shortest-path routing and essentially
every default SDN forwarding application does. Variant A also *reconverges on
link failure* rather than black-holing, so it is not a straw man in the recovery
experiment.

**What it costs:** a traffic-engineering baseline (capacity-aware static
weights, or ECMP) would be a stronger comparator. Variant A is the conventional
baseline the project set out to test against, not the best possible non-learning
policy.

---

## A3. One decision per interval, not per packet

**Assumption:** the agent chooses an action once per 1-second polling interval,
and every flow established during that interval uses it.

**Why:** it makes the MDP well defined. State `s_t` is measured from the
interval's counters, one action `a_t` applies across the interval, the scalar
reward `r_t` is measured from that same interval, and `s_{t+1}` follows. With
per-packet actions, thousands of decisions would share credit for one throughput
measurement and none could be evaluated.

**What it costs:** the agent cannot react within an interval, and flows arriving
just before a bad decision are stuck with it for up to a second.

---

## A4. The reward uses only OpenFlow counters

**Assumption:** the controller may use port, flow and table statistics, and
nothing else.

**Why:** two reasons, one practical and one methodological. Practically, that is
all a real OpenFlow controller has. Methodologically, the experiment is scored
with `ping` and `iperf3` measurements taken by the hosts — if those same numbers
fed the reward, a good score would show only that the optimiser optimised its own
objective. Keeping them apart makes a reported improvement an out-of-sample
consequence of the learned policy.

**Consequence:** maximum link utilisation stands in for congestion and queueing
delay, because OpenFlow cannot measure latency. The agent optimises a proxy.

---

## A5. Reward weights are fixed, hand-chosen and untuned

```
throughput 1.00 | utilisation 0.40 | loss 0.60 | occupancy 0.25 | controller msgs 0.15
```

**Assumption:** these weights express a reasonable operator preference.

**Why these values:** throughput leads; loss and congestion follow closely;
flow-table occupancy and controller load carry real but smaller weight. The last
two are deliberately **non-zero** — if they were zero, variant C's timeout
actions could not pay off and the B-vs-C comparison would be decided before any
run started.

**What it costs:** they are not tuned, and no claim is made that they are
optimal. Different weights would yield a different policy. They are reported in
full in every `config.json` so results are attributable to them.

---

## A6. Only reactive controller messages count against the policy

**Assumption:** `PacketIn + FlowMod` is the controller load the policy is
responsible for; periodic statistics polling is not.

**Why:** polling is fixed monitoring overhead at 15 requests/second that no
policy choice can change. Including it would swamp the term, flatten it into a
near-constant, and remove the gradient variant C needs to learn timeout control
at all.

**What it costs:** reported "controller load" in the reward is narrower than
total controller traffic. Both totals are recorded in
`raw/controller_counters.csv`.

---

## A7. Timeout classes of 5 s / 15 s / 60 s are meaningfully different

**Assumption:** these three classes produce genuinely different flow-table and
controller behaviour.

**Why:** 5 s is shorter than the gap between flow arrivals in the `churn`
workload (so rules expire between flows), 60 s outlives most flows in every
workload (so rules persist), and 15 s sits between. The fixed timeout used by
variants A and B is 15 s, the middle class — so C is not given an unfair
advantage by having the only sensible value available to it.

**What it costs:** three discrete classes, not a continuous choice. A finer or
adaptive range might do better.

---

## A8. 144 states is the right size for the learning budget

State = path-A utilisation (4 bins) × path-B utilisation (4 bins) × flow-table
occupancy (3 bins) × table-miss rate (3 bins).

**Assumption:** this is detailed enough to express a useful policy and small
enough to be learnable in ~90 steps.

**Why:** tabular Q-learning needs each state visited several times before its row
means anything. A richer state would be more expressive and less learnable. Bin
edges (utilisation 0.25/0.50/0.75; occupancy 0.33/0.66; miss rate 5/20 per
second) are round numbers chosen before data collection, not fitted to results.

**What it costs:** coverage is still partial within a single run.
`policy.json` records the coverage actually achieved per run; it should be read
before trusting any individual state's value.

---

## A9. Identical seeded traffic across variants

**Assumption:** `random.Random(seed)` driving flow arrival times, source/
destination choice and port numbers makes the offered load identical across
variants for a given `(workload, seed)`.

**Why:** it is the only way a three-way comparison means anything. Differences in
results must come from the controller, not from different traffic.

**What it costs:** it is *offered* load that is identical, not delivered load —
a variant that routes badly will of course see different actual traffic
behaviour. TCP's response to loss means the realised packet timing is not
byte-identical across variants, and cannot be.

---

## A10. Both directions of a flow take the same path

**Assumption:** forward and reverse rules are installed together, on the same
chosen path.

**Why:** it is what a controller doing flow-pair setup normally does, it halves
controller work, and — critically — it keeps RTT interpretable. If forward and
reverse traffic took different paths, a round-trip time would mix two different
path latencies and could not be attributed to either.

**What it costs:** asymmetric routing is not explored, though it is a real
technique.

---

## A11. The run is the unit of analysis; the second half is reported separately

**Assumption:** reporting both the whole run and its second half is the honest
way to present a policy that is still learning.

**Why:** epsilon decays over the first half of each run, so the first half is
substantially exploration. The second half is closer to the converged policy.
Reporting only the full run would understate the learned policies; reporting only
the second half would hide the cost of exploration. Both are in every table.

**What it costs:** this is not a clean train/test split. A proper one would train
for a while and then evaluate with learning frozen, which the 90-second run
budget did not allow for the full matrix.

---

## A12. A run is usable only if its manifest says it is complete

**Assumption:** runs missing expected artefacts are excluded from analysis and
counted.

**Why:** a run that produced no iperf3 output is a failed run, not a run with
zero throughput. Averaging over whatever survived would quietly bias results
toward whichever variant failed least gracefully.

**What it costs:** nothing, provided the exclusion count is reported — which it
is, in `analysis/out/aggregate_provenance.json` and at the top of the results
tables.
