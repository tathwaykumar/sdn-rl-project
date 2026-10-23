"""Repeatable, seeded traffic workloads.

Every workload is driven by a ``random.Random(seed)`` instance, so a given
(workload, seed) produces the same flow arrival times and port numbers on every
run and across all three variants.  That is what makes A/B/C comparable: the
variants see identical offered traffic, and any difference in the results comes
from the controller rather than from different traffic.

All measurement output is written raw and unparsed:

* ``iperf3_*.json``  -- iperf3's own ``--json`` output, one file per flow
* ``ping_*.txt``     -- raw ``ping`` output with ``-D`` timestamps

Nothing is summarised here.  ``analysis/aggregate.py`` parses these files later,
so a parsing bug can be fixed without re-running a single experiment.

Workloads
---------
``light``   one modest flow. Path A (short, 5 Mbit) is genuinely the better
            choice here, so this workload checks that the agents do not simply
            learn "always take the big pipe".
``heavy``   two saturating flows. Path A cannot carry them; an adaptive policy
            should spill onto B.
``churn``   many short flows with distinct 5-tuples. Stresses the flow table,
            which is where variant C's timeout control should matter.
``failure`` ``heavy`` plus a mid-run link failure on path A, then restoration.
            Measures reconvergence.
"""

from __future__ import annotations

import os
import random
import threading
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from topo import spec

#: iperf3 server ports. One port per concurrent flow: a single iperf3 server
#: port serialises connections, which would silently turn concurrent flows into
#: sequential ones and destroy the congestion we are trying to create.
BASE_PORT = 5201


@dataclass
class WorkloadResult:
    name: str
    seed: int
    duration_s: float
    started_at: float
    ended_at: float
    flows: List[Dict[str, object]] = field(default_factory=list)
    events: List[Dict[str, object]] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "seed": self.seed,
            "duration_s": self.duration_s,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "flows": self.flows,
            "events": self.events,
            "errors": self.errors,
        }


class Workload:
    """Base class. Subclasses implement :meth:`run`."""

    name = "abstract"
    #: Nominal duration in seconds, excluding setup and drain.
    duration_s = 90

    def __init__(self, seed: int, raw_dir: str, duration_s: Optional[int] = None):
        self.seed = seed
        self.rng = random.Random(seed)
        self.raw_dir = raw_dir
        if duration_s is not None:
            self.duration_s = duration_s
        os.makedirs(raw_dir, exist_ok=True)

    # ------------------------------------------------------------- helpers
    def _server_cmd(self, port: int) -> str:
        # -1 would exit after one connection; we keep servers up for the whole
        # run so late-arriving churn flows still find a listener.
        return f"iperf3 -s -p {port} --forceflush > /dev/null 2>&1 &"

    def _start_servers(self, net, ports_by_host: Dict[str, List[int]]) -> None:
        for host_name, ports in ports_by_host.items():
            host = net.get(host_name)
            for port in ports:
                host.cmd(self._server_cmd(port))
        # iperf3 needs a moment to bind before clients connect; without it the
        # first client can fail with "connection refused" and the flow is lost.
        time.sleep(1.0)

    def _stop_servers(self, net) -> None:
        for h in spec.HOSTS:
            net.get(h.name).cmd("pkill -f 'iperf3 -s' 2>/dev/null")

    def _start_latency_probe(self, net, src: str, dst_ip: str, duration: float):
        """Continuous ping; raw output kept for latency and recovery analysis.

        Three flags matter here, each for a reason learned the hard way:

        ``-D``  timestamps every line, which is what lets the recovery analysis
                locate an outage to the nearest probe interval.
        ``-O``  prints an explicit "no answer yet" line for every lost probe.
                Without it, ping prints nothing for a loss and an outage is
                indistinguishable from the probe having simply stopped.
        ``-w``  gives ping its own deadline so it exits and flushes on its own.
                Terminating it instead loses whatever is still sitting in the
                block-buffered pipe -- which silently truncated the tail of the
                measurement, exactly across the window we care about.

        ``stdbuf -oL`` forces line buffering as a second line of defence, so
        the file is usable even if the run is killed.
        """
        path = os.path.join(self.raw_dir, f"ping_{src}.txt")
        host = net.get(src)
        deadline = int(duration) + 2
        return host.popen(
            f"stdbuf -oL ping -D -O -i 0.2 -w {deadline} -W 1 {dst_ip}",
            shell=True,
            stdout=open(path, "wb"),
            stderr=open(os.path.join(self.raw_dir, f"ping_{src}.err"), "wb"),
        )

    def _stop_probe(self, probe, res: "WorkloadResult") -> None:
        """Let the probe exit on its own so its output is flushed."""
        if probe is None:
            return
        try:
            probe.wait(timeout=20)
            return
        except Exception:
            res.errors.append("latency probe did not exit on its deadline")
        for killer in ("terminate", "kill"):
            try:
                getattr(probe, killer)()
                probe.wait(timeout=3)
                return
            except Exception:
                continue

    def _iperf_client(
        self,
        net,
        src: str,
        dst_ip: str,
        port: int,
        duration: float,
        bitrate: Optional[str] = None,
        tag: str = "",
    ):
        """Launch one iperf3 client, writing its own raw JSON file."""
        label = tag or f"{src}_p{port}"
        path = os.path.join(self.raw_dir, f"iperf3_{label}.json")
        rate = f"-b {bitrate}" if bitrate else ""
        host = net.get(src)
        cmd = (
            f"iperf3 -c {dst_ip} -p {port} -t {duration:.1f} "
            f"{rate} --json --connect-timeout 3000"
        )
        proc = host.popen(
            cmd, shell=True,
            stdout=open(path, "wb"),
            stderr=open(os.path.join(self.raw_dir, f"iperf3_{label}.err"), "wb"),
        )
        return proc, {"label": label, "src": src, "dst_ip": dst_ip, "port": port,
                      "duration_s": duration, "bitrate": bitrate, "json": os.path.basename(path)}

    def _wait_all(self, procs, res: "WorkloadResult", grace_s: float = 20.0) -> None:
        """Wait for clients, but never indefinitely.

        iperf3 writes its ``--json`` report only when the test finishes, and it
        can block forever if its control connection is disrupted -- which is
        exactly what a mid-run link failure does. An unbounded ``wait()`` there
        hangs the whole experiment matrix. Stragglers are killed and recorded
        as errors so the analysis can exclude them instead of treating a
        truncated file as a measurement.
        """
        deadline = time.time() + self.duration_s + grace_s
        for proc in procs:
            remaining = max(0.5, deadline - time.time())
            try:
                proc.wait(timeout=remaining)
            except Exception:
                res.errors.append("iperf3 client exceeded its deadline; killed")
                for killer in ("terminate", "kill"):
                    try:
                        getattr(proc, killer)()
                        proc.wait(timeout=3)
                        break
                    except Exception:
                        continue

    def run(self, net, link_control=None) -> WorkloadResult:
        raise NotImplementedError


class LightWorkload(Workload):
    """One 1 Mbit flow. Path A is the correct choice; tests over-reaction."""

    name = "light"

    def run(self, net, link_control=None) -> WorkloadResult:
        start = time.time()
        res = WorkloadResult(self.name, self.seed, self.duration_s, start, start)
        port = BASE_PORT
        self._start_servers(net, {"h3": [port]})
        probe = self._start_latency_probe(net, "h1", spec.HOST_BY_NAME["h3"].ip,
                                          self.duration_s)
        proc, meta = self._iperf_client(
            net, "h1", spec.HOST_BY_NAME["h3"].ip, port,
            self.duration_s, bitrate="1M", tag="light_h1_h3",
        )
        res.flows.append(meta)
        self._wait_all([proc], res)
        self._stop_probe(probe, res)
        self._stop_servers(net)
        res.ended_at = time.time()
        return res


class HeavyWorkload(Workload):
    """Two saturating TCP flows: more offered load than path A can carry."""

    name = "heavy"

    def run(self, net, link_control=None) -> WorkloadResult:
        start = time.time()
        res = WorkloadResult(self.name, self.seed, self.duration_s, start, start)
        p1, p2 = BASE_PORT, BASE_PORT + 1
        self._start_servers(net, {"h3": [p1], "h4": [p2]})
        probe = self._start_latency_probe(net, "h1", spec.HOST_BY_NAME["h3"].ip,
                                          self.duration_s)

        procs = []
        # Small seeded stagger so the two flows do not start on the same
        # millisecond every run; identical starts make TCP synchronise in an
        # unrealistic way.
        for src, dst, port, tag in (
            ("h1", "h3", p1, "heavy_h1_h3"),
            ("h2", "h4", p2, "heavy_h2_h4"),
        ):
            delay = self.rng.uniform(0.0, 1.0)
            time.sleep(delay)
            proc, meta = self._iperf_client(
                net, src, spec.HOST_BY_NAME[dst].ip, port,
                self.duration_s - delay, tag=tag,
            )
            meta["start_delay_s"] = round(delay, 3)
            procs.append(proc)
            res.flows.append(meta)

        self._wait_all(procs, res)
        self._stop_probe(probe, res)
        self._stop_servers(net)
        res.ended_at = time.time()
        return res


class ChurnWorkload(Workload):
    """Many short flows with distinct 5-tuples -- a flow-table stress test.

    Each flow is a separate 5-tuple, so each one costs a table miss, a
    controller round trip and a set of rules on every switch along its path.
    This is the workload where the idle-timeout choice has real consequences,
    and therefore where variant C has something to prove.
    """

    name = "churn"
    n_flows = 24
    flow_duration_s = 4.0

    def run(self, net, link_control=None) -> WorkloadResult:
        start = time.time()
        res = WorkloadResult(self.name, self.seed, self.duration_s, start, start)

        ports = [BASE_PORT + i for i in range(8)]
        self._start_servers(net, {"h3": ports[:4], "h4": ports[4:]})
        probe = self._start_latency_probe(net, "h1", spec.HOST_BY_NAME["h3"].ip,
                                          self.duration_s)

        # Seeded arrival schedule, computed up front so it is identical across
        # variants and is recorded before the run rather than reconstructed.
        schedule = []
        for i in range(self.n_flows):
            arrival = self.rng.uniform(0, self.duration_s - self.flow_duration_s - 1)
            src = self.rng.choice(["h1", "h2"])
            dst = "h3" if src == "h1" else "h4"
            port = self.rng.choice(ports[:4] if dst == "h3" else ports[4:])
            schedule.append((arrival, src, dst, port, i))
        schedule.sort()
        res.events.append({"kind": "schedule",
                           "flows": [{"at": round(a, 2), "src": s, "dst": d, "port": p}
                                     for a, s, d, p, _ in schedule]})

        procs = []
        for arrival, src, dst, port, idx in schedule:
            wait = arrival - (time.time() - start)
            if wait > 0:
                time.sleep(wait)
            proc, meta = self._iperf_client(
                net, src, spec.HOST_BY_NAME[dst].ip, port,
                self.flow_duration_s, bitrate="2M",
                tag=f"churn{idx:02d}_{src}_{dst}_p{port}",
            )
            meta["arrival_s"] = round(arrival, 3)
            procs.append(proc)
            res.flows.append(meta)

        self._wait_all(procs, res)
        self._stop_probe(probe, res)
        self._stop_servers(net)
        res.ended_at = time.time()
        return res


class FailureWorkload(Workload):
    """``heavy`` traffic with a mid-run link failure on path A, then repair.

    The failed link is on the path conventional routing prefers, so every
    variant is forced to react.  The continuous ping gives the ground-truth
    outage window; the controller's own ``events.csv`` gives the time it
    noticed and reacted.  Reporting both keeps "recovery time" an observation
    rather than an inference.
    """

    name = "failure"
    #: Failure and repair are expressed as fractions of the run so that short
    #: smoke runs still exercise both transitions. Absolute offsets would make
    #: a 30-second test run silently skip the failure entirely and look fine.
    fail_at_frac = 0.45
    repair_at_frac = 0.72

    @property
    def fail_at_s(self) -> float:
        return self.duration_s * self.fail_at_frac

    @property
    def repair_at_s(self) -> float:
        return self.duration_s * self.repair_at_frac

    #: Traffic is split into short back-to-back segments rather than one long
    #: flow. iperf3 emits its JSON report only when a test finishes, so a
    #: single flow spanning the outage loses the entire throughput measurement
    #: for that host pair. With segments, only the one straddling the outage is
    #: lost and every other segment still reports.
    segment_s = 6.0

    def _segment_loop(self, net, src: str, dst: str, port: int,
                      res: "WorkloadResult", lock: threading.Lock) -> None:
        dst_ip = spec.HOST_BY_NAME[dst].ip
        started = time.time()
        index = 0
        while time.time() - started < self.duration_s - 1.0:
            remaining = self.duration_s - (time.time() - started)
            seg = min(self.segment_s, remaining)
            if seg < 1.0:
                break
            offset = time.time() - started
            proc, meta = self._iperf_client(
                net, src, dst_ip, port, seg,
                tag=f"fail_{src}_{dst}_seg{index:02d}",
            )
            meta["segment_index"] = index
            meta["offset_s"] = round(offset, 3)
            with lock:
                res.flows.append(meta)
            try:
                proc.wait(timeout=seg + 15)
            except Exception:
                with lock:
                    res.errors.append(
                        f"segment {index} on {src}->{dst} exceeded its deadline "
                        "and was killed (expected for the segment spanning the "
                        "outage; excluded from throughput by the analysis)"
                    )
                for killer in ("terminate", "kill"):
                    try:
                        getattr(proc, killer)()
                        proc.wait(timeout=3)
                        break
                    except Exception:
                        continue
            index += 1

    def run(self, net, link_control=None) -> WorkloadResult:
        start = time.time()
        res = WorkloadResult(self.name, self.seed, self.duration_s, start, start)
        if link_control is None:
            res.errors.append("no link_control provided; failure not injected")

        p1, p2 = BASE_PORT, BASE_PORT + 1
        self._start_servers(net, {"h3": [p1], "h4": [p2]})
        probe = self._start_latency_probe(net, "h1", spec.HOST_BY_NAME["h3"].ip,
                                          self.duration_s)

        lock = threading.Lock()
        threads = [
            threading.Thread(target=self._segment_loop,
                             args=(net, src, dst, port, res, lock), daemon=True)
            for src, dst, port in (("h1", "h3", p1), ("h2", "h4", p2))
        ]
        for t in threads:
            t.start()

        a, b = spec.RECOVERY_LINK
        for when, action in ((self.fail_at_s, "down"), (self.repair_at_s, "up")):
            wait = when - (time.time() - start)
            if wait > 0:
                time.sleep(wait)
            if link_control is not None:
                ts = time.time()
                link_control(a, b, action)
                res.events.append({
                    "kind": f"link_{action}",
                    "link": f"{a}-{b}",
                    "t": ts,
                    "offset_s": round(ts - start, 3),
                })

        for t in threads:
            t.join(timeout=self.duration_s + 40)
            if t.is_alive():
                res.errors.append("segment thread did not finish in time")
        self._stop_probe(probe, res)
        self._stop_servers(net)
        res.ended_at = time.time()
        return res


WORKLOADS = {
    w.name: w for w in (LightWorkload, HeavyWorkload, ChurnWorkload, FailureWorkload)
}


def build_workload(name: str, seed: int, raw_dir: str,
                   duration_s: Optional[int] = None) -> Workload:
    if name not in WORKLOADS:
        raise ValueError(f"unknown workload {name!r}; have {sorted(WORKLOADS)}")
    return WORKLOADS[name](seed=seed, raw_dir=raw_dir, duration_s=duration_s)
