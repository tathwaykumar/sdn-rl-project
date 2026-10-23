"""Compare the trained / frozen / shuffled control conditions.

Reads the runs tagged by ``experiments/run_diagnostics.py`` and answers two
questions that the main results cannot answer on their own:

1. **Do the learned values matter?** A frozen, all-zero agent runs the entire
   RL code path but has nothing learned to act on, so it must collapse to the
   deterministic tie-break -- a single action, used throughout. If the trained
   agent's action distribution is indistinguishable from the frozen one, then
   whatever the main results show is not coming from learning.

2. **Do the actions reach the dataplane?** Under a permuted action mapping the
   agent's chosen action index means something different at the switch. If the
   installed rules look the same as in the unshuffled run, the actions are
   decorative.

Both are answered from the flow-table dumps and decision logs, not from any
claim the controller makes about itself.

    python3 -m analysis.diagnostics
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from typing import Dict, List, Optional

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from analysis.parsers import discover_runs, read_csv, to_float  # noqa: E402
from analysis.verify_behaviour import observed_paths  # noqa: E402

CONDITION_TAGS = ("trained", "frozen", "shuffled")


def condition_of(run_dir: str) -> Optional[str]:
    base = os.path.basename(run_dir)
    for tag in CONDITION_TAGS:
        if base.endswith(f"diag-{tag}"):
            return tag
    return None


def summarise_condition(runs) -> Dict[str, object]:
    action_counter: Counter = Counter()
    path_counter: Counter = Counter()
    timeout_counter: Counter = Counter()
    rewards: List[float] = []
    updates: List[float] = []
    nonzero: List[float] = []
    n_runs = 0

    for run in runs:
        n_runs += 1
        rows = read_csv(os.path.join(run.raw_dir, "decisions.csv"))
        for row in rows:
            if row.get("action"):
                action_counter[row["action"]] += 1
            r = to_float(row.get("reward", ""))
            if r is not None:
                rewards.append(r)
        paths, timeouts, _ = observed_paths(run)
        path_counter.update(paths)
        timeout_counter.update(timeouts)

        policy_path = os.path.join(run.path, "policy.json")
        if os.path.exists(policy_path):
            try:
                with open(policy_path, encoding="utf-8") as fh:
                    policy = json.load(fh)
                updates.append(float(policy.get("updates", 0)))
                nonzero.append(float(policy.get("nonzero_q_entries", 0)))
            except (json.JSONDecodeError, TypeError, ValueError):
                pass

    return {
        "runs": n_runs,
        "distinct_actions_chosen": len(action_counter),
        "action_counts": dict(action_counter),
        "installed_path_counts": dict(path_counter),
        "installed_timeout_counts": {str(k): v for k, v in sorted(timeout_counter.items())},
        "distinct_installed_timeouts": len(timeout_counter),
        "mean_reward": (sum(rewards) / len(rewards)) if rewards else None,
        "q_updates_total": sum(updates) if updates else 0.0,
        "nonzero_q_entries_total": sum(nonzero) if nonzero else 0.0,
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runs", default=os.path.join(PROJECT_ROOT, "results", "runs"))
    p.add_argument("--out", default=os.path.join(PROJECT_ROOT, "analysis", "out"))
    args = p.parse_args(argv)

    grouped: Dict[str, List] = {tag: [] for tag in CONDITION_TAGS}
    # These are the only runs this module cares about, so ask for them
    # explicitly -- discover_runs() filters them out by default.
    for run in discover_runs(args.runs, include_diagnostics=True):
        tag = condition_of(run.path)
        if tag and run.complete:
            grouped[tag].append(run)

    if not any(grouped.values()):
        print("no diagnostic runs found; run experiments/run_diagnostics.py first")
        return 1

    summary = {tag: summarise_condition(runs) for tag, runs in grouped.items() if runs}

    claims: List[Dict[str, object]] = []

    trained = summary.get("trained")
    frozen = summary.get("frozen")
    shuffled = summary.get("shuffled")

    def add(name: str, ok: Optional[bool], detail: str) -> None:
        claims.append({
            "claim": name,
            "result": "PASS" if ok else ("FAIL" if ok is False else "NOT TESTABLE"),
            "detail": detail,
        })

    if frozen:
        add("Frozen agent performs no Q updates",
            frozen["q_updates_total"] == 0,
            f"updates={frozen['q_updates_total']:.0f}, "
            f"nonzero Q entries={frozen['nonzero_q_entries_total']:.0f}")
        add("Frozen agent collapses to a single action",
            frozen["distinct_actions_chosen"] == 1,
            f"distinct actions chosen: {frozen['action_counts']}")
    else:
        add("Frozen agent performs no Q updates", None, "no frozen runs")

    if trained and frozen:
        add("Trained agent explores more actions than the frozen control",
            trained["distinct_actions_chosen"] > frozen["distinct_actions_chosen"],
            f"trained={trained['distinct_actions_chosen']} distinct actions, "
            f"frozen={frozen['distinct_actions_chosen']}")
        add("Trained and frozen differ in what they install",
            trained["installed_timeout_counts"] != frozen["installed_timeout_counts"]
            or trained["installed_path_counts"] != frozen["installed_path_counts"],
            f"trained paths={trained['installed_path_counts']} "
            f"timeouts={trained['installed_timeout_counts']} | "
            f"frozen paths={frozen['installed_path_counts']} "
            f"timeouts={frozen['installed_timeout_counts']}")

    if trained and shuffled:
        add("Shuffling the action mapping changes dataplane behaviour",
            trained["installed_path_counts"] != shuffled["installed_path_counts"]
            or trained["installed_timeout_counts"] != shuffled["installed_timeout_counts"],
            f"trained paths={trained['installed_path_counts']} "
            f"timeouts={trained['installed_timeout_counts']} | "
            f"shuffled paths={shuffled['installed_path_counts']} "
            f"timeouts={shuffled['installed_timeout_counts']}")

    os.makedirs(args.out, exist_ok=True)
    out_path = os.path.join(args.out, "diagnostics.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump({"by_condition": summary, "claims": claims}, fh,
                  indent=2, sort_keys=True)

    print("\nCONTROL EXPERIMENTS")
    print("=" * 72)
    for tag in CONDITION_TAGS:
        s = summary.get(tag)
        if not s:
            continue
        print(f"\n{tag}  ({s['runs']} runs)")
        print(f"  distinct actions chosen : {s['distinct_actions_chosen']}")
        print(f"  Q updates               : {s['q_updates_total']:.0f}")
        print(f"  installed paths         : {s['installed_path_counts']}")
        print(f"  installed timeouts      : {s['installed_timeout_counts']}")
        mean_reward = s["mean_reward"]
        reward_text = "n/a" if mean_reward is None else f"{mean_reward:.4f}"
        print(f"  mean reward             : {reward_text}")

    print("\nCLAIMS")
    print("=" * 72)
    failed = 0
    for c in claims:
        print(f"  [{c['result']:>12}] {c['claim']}")
        print(f"                 {c['detail']}")
        if c["result"] == "FAIL":
            failed += 1
    print("=" * 72)
    print(f"report -> {out_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
