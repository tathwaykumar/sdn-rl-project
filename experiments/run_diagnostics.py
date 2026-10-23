"""Control experiments: does the *learning* matter, or just the plumbing?

The main matrix shows whether variants differ. It cannot, by itself, show that
the difference comes from the learned Q-values rather than from some incidental
property of the RL code path. These three conditions separate those:

``trained``  variant C exactly as in the main experiment.
``frozen``   variant C with learning and exploration disabled and an all-zero
             Q-table. It still selects actions, still installs rules, still
             pays every overhead the RL code path has -- it simply has nothing
             learned to act on, so it collapses to the deterministic tie-break.
             If ``trained`` and ``frozen`` behave identically, the learned
             values are doing nothing and any apparent benefit is the plumbing.
``shuffled`` variant C with the action -> (path, timeout) mapping permuted.
             The agent learns normally, but its chosen action index maps to a
             different dataplane effect. If the *dataplane behaviour* does not
             change under shuffling, the actions are not reaching the switches
             and the whole mechanism is decorative.

Each condition is run on the same workload with the same seeds, so the only
difference is the agent's treatment.

    sudo python3 -m experiments.run_diagnostics --workload heavy --seeds 3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Dict, List

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from experiments.run_experiment import run_single  # noqa: E402

CONDITIONS = {
    "trained": dict(frozen_agent=False, shuffle_actions=False),
    "frozen": dict(frozen_agent=True, shuffle_actions=False),
    "shuffled": dict(frozen_agent=False, shuffle_actions=True),
}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--workload", default="heavy")
    p.add_argument("--variant", default="C", choices=["B", "C"])
    p.add_argument("--seeds", type=int, default=3)
    p.add_argument("--duration", type=int, default=90)
    p.add_argument("--out", default=None)
    args = p.parse_args(argv)

    if os.geteuid() != 0:
        print("ERROR: must run as root (Mininet requires it)")
        return 2

    results: List[Dict[str, object]] = []
    total = len(CONDITIONS) * args.seeds
    done = 0
    for seed in range(1, args.seeds + 1):
        for condition, flags in CONDITIONS.items():
            done += 1
            print(f"\n[{done}/{total}] {condition} seed={seed}")
            res = run_single(
                variant=args.variant,
                workload_name=args.workload,
                seed=seed,
                duration_s=args.duration,
                out_root=args.out,
                tag=f"diag-{condition}",
                **flags,
            )
            results.append({
                "condition": condition,
                "seed": seed,
                "run_dir": res["run_dir"],
                "complete": res["manifest"]["complete"],
            })

    out_root = args.out or os.path.join(PROJECT_ROOT, "results", "runs")
    summary_path = os.path.join(out_root, "diagnostics-summary.json")
    with open(summary_path, "w", encoding="utf-8") as fh:
        json.dump({
            "variant": args.variant,
            "workload": args.workload,
            "seeds": args.seeds,
            "duration_s": args.duration,
            "conditions": sorted(CONDITIONS),
            "runs": results,
        }, fh, indent=2, sort_keys=True)

    complete = sum(1 for r in results if r["complete"])
    print(f"\n=== DIAGNOSTICS DONE: {complete}/{total} complete")
    print(f"summary -> {summary_path}")
    print("\nCompare with:  python3 -m analysis.diagnostics")
    return 0


if __name__ == "__main__":
    sys.exit(main())
