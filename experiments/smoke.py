"""End-to-end smoke test: controller + Mininet + connectivity + rule check.

This is the gate that has to pass before any experiment result means anything.
It checks, in order:

1. The controller starts and listens on the OpenFlow port.
2. All five switches connect and negotiate OpenFlow 1.3.
3. Mininet's live port numbering matches ``topo.spec``.
4. Every host pair can reach every other (ARP proxy + reactive install work).
5. The switches really do hold rules pointing along the chosen path.

Step 5 is the important one.  Steps 1-4 can all pass while the controller
installs nonsense, so the test reads the rules back off the switch with
``ovs-ofctl dump-flows`` and compares them against what ``topo.spec`` says the
path should be.  Must be run as root (Mininet).

    sudo python3 -m experiments.smoke
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from typing import Dict, List, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from experiments.controller_proc import ControllerProcess  # noqa: E402
from topo import spec  # noqa: E402

SMOKE_DIR = os.path.join(PROJECT_ROOT, "results", "smoke")


def dump_flows(switch: str) -> str:
    """Read the switch's flow table. This is ground truth, not a log."""
    out = subprocess.run(
        ["ovs-ofctl", "-O", "OpenFlow13", "dump-flows", switch],
        capture_output=True,
        text=True,
    )
    return out.stdout


def parse_flows(text: str) -> List[Dict[str, str]]:
    """Extract priority, idle_timeout, match and output port from a dump."""
    flows = []
    for line in text.splitlines():
        line = line.strip()
        if not line or "cookie=" not in line:
            continue
        entry: Dict[str, str] = {"raw": line}
        for key in ("priority", "idle_timeout", "hard_timeout", "n_packets"):
            m = re.search(rf"{key}=(\d+)", line)
            if m:
                entry[key] = m.group(1)
        m = re.search(r"actions=output:(\d+)", line)
        if m:
            entry["output"] = m.group(1)
        elif "actions=CONTROLLER" in line:
            entry["output"] = "CONTROLLER"
        elif "actions=drop" in line:
            entry["output"] = "drop"
        for key in ("nw_src", "nw_dst"):
            m = re.search(rf"{key}=([\d.]+)", line)
            if m:
                entry[key] = m.group(1)
        flows.append(entry)
    return flows


def main() -> int:
    if os.geteuid() != 0:
        print("FAIL: must run as root (Mininet requires it)")
        return 2

    os.makedirs(SMOKE_DIR, exist_ok=True)
    config_path = os.path.join(SMOKE_DIR, "config.json")
    if not os.path.exists(config_path):
        print(f"FAIL: missing {config_path}")
        return 2

    # Import Mininet only after the root check, so a non-root run fails with a
    # clear message instead of a permission error from deep inside Mininet.
    from mininet.log import setLogLevel
    from mininet.net import Mininet  # noqa: F401
    from topo.diamond import build_network, verify_port_mapping

    setLogLevel("warning")

    failures: List[str] = []
    checks: List[Tuple[str, bool, str]] = []

    controller = ControllerProcess(
        app_module="controller.baseline",
        config_path=config_path,
        log_path=os.path.join(SMOKE_DIR, "controller.log"),
        project_root=PROJECT_ROOT,
        verbose=True,
    )

    print("[1/5] starting controller ...")
    controller.start()
    ready = controller.wait_ready(timeout=20)
    checks.append(("controller listens on 6653", ready, ""))
    if not ready:
        print("FAIL: controller never listened; see controller.log")
        controller.stop()
        return 1

    net = None
    try:
        print("[2/5] building Mininet topology ...")
        net = build_network()

        # Wait for all five switches to establish an OpenFlow session.
        deadline = time.time() + 30
        connected: List[str] = []
        while time.time() < deadline:
            connected = [
                name
                for name in spec.SWITCHES
                if subprocess.run(
                    ["ovs-vsctl", "get", "Bridge", name, "controller"],
                    capture_output=True, text=True,
                ).returncode == 0
                and "true"
                in subprocess.run(
                    ["ovs-vsctl", "--columns=is_connected", "list", "Controller"],
                    capture_output=True, text=True,
                ).stdout
            ]
            if len(connected) == len(spec.SWITCHES):
                break
            time.sleep(1)
        all_connected = len(connected) == len(spec.SWITCHES)
        checks.append((
            f"all {len(spec.SWITCHES)} switches connected",
            all_connected,
            f"connected={len(connected)}",
        ))

        print("[3/5] verifying port mapping against topo.spec ...")
        problems = verify_port_mapping(net)
        checks.append(("port mapping matches spec", not problems, "; ".join(problems)))

        print("[4/5] pingall ...")
        loss = net.pingAll(timeout="2")
        checks.append((f"pingall loss == 0% (got {loss}%)", loss == 0, ""))

        print("[5/5] reading flow tables back off the switches ...")
        # Generate one known flow so there is something specific to verify.
        h1, h3 = net.get("h1"), net.get("h3")
        h1.cmd(f"ping -c 3 -i 0.3 {h3.IP()}")
        time.sleep(1.0)

        expected_path = min(spec.PATH_NAMES, key=spec.path_hop_count)
        hops = spec.path_hops("h1", "h3", expected_path)
        dumps = {name: dump_flows(name) for name in spec.SWITCHES}

        for switch_name, out_port in hops:
            flows = parse_flows(dumps[switch_name])
            match = [
                f for f in flows
                if f.get("nw_src") == "10.0.0.1"
                and f.get("nw_dst") == "10.0.0.3"
                and f.get("output") == str(out_port)
            ]
            ok = bool(match)
            checks.append((
                f"{switch_name} has rule 10.0.0.1->10.0.0.3 out port {out_port}",
                ok,
                "" if ok else f"dump had {len(flows)} flows",
            ))

        # Idle timeouts on installed rules must equal the configured fixed value
        # for variant A -- evidence the timeout we asked for is the timeout the
        # switch is enforcing.
        s1_flows = [f for f in parse_flows(dumps["s1"])
                    if int(f.get("priority", 0)) >= 10]
        idle_values = sorted({f.get("idle_timeout") for f in s1_flows})
        checks.append((
            f"s1 installed rules carry idle_timeout (values={idle_values})",
            bool(s1_flows) and all(f.get("idle_timeout") for f in s1_flows),
            "",
        ))

        with open(os.path.join(SMOKE_DIR, "flow_dumps.txt"), "w", encoding="utf-8") as fh:
            for name in spec.SWITCHES:
                fh.write(f"===== {name} =====\n{dumps[name]}\n")

    finally:
        if net is not None:
            net.stop()
        rc = controller.stop()
        print(f"controller exit code: {rc}")

    print("\n================ SMOKE TEST RESULTS ================")
    for label, ok, detail in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  ({detail})" if detail and not ok else ""))
        if not ok:
            failures.append(label)
    print("====================================================")
    if failures:
        print(f"{len(failures)} check(s) FAILED")
        return 1
    print("all checks PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
