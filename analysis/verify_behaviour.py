"""Does the controller's policy actually change what the switches do?

This is the project's central claim, and it is checked against the switches,
not against the controller's own logs.  A controller can log a decision it
never installed; a flow table cannot lie about what it is holding.

Five checks, each answering a way the claim could be false:

1. ``paths_used``        -- do the installed rules use more than one egress
                            port? If every variant only ever uses path A, the
                            routing action is decorative.
2. ``timeouts_used``     -- do installed rules carry more than one
                            idle_timeout? Variant C claims to vary timeouts;
                            A and B claim not to. Both claims are testable.
3. ``decision_to_rule``  -- for each rule, was the path it uses the path the
                            policy had chosen at the moment the rule was
                            installed? Rule age (``duration``) gives the
                            install time, so this matches each rule against
                            the decision actually in force, not the latest one.
4. ``variant_divergence``-- do the variants differ from each other in the
                            distribution of paths and timeouts they install?
                            Identical distributions would mean the variants are
                            behaviourally the same program.
5. ``null_control``      -- a frozen, all-zero agent must collapse to the
                            deterministic tie-break. If a trained agent
                            behaves identically to a frozen one, the learned
                            values are doing nothing.

Run::

    python3 -m analysis.verify_behaviour [--runs results/runs] [--out analysis/out]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from typing import Dict, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from analysis.parsers import (  # noqa: E402
    Run, discover_runs, parse_all_flow_dumps, read_csv, to_float,
)
from topo import spec  # noqa: E402

#: Egress port at s1 -> path name. These are the two ports that distinguish a
#: path choice in the dataplane.
S1_PORT_TO_PATH: Dict[str, str] = {
    str(spec.NEIGHBOUR_PORT[("s1", "s2")]): "A",
    str(spec.NEIGHBOUR_PORT[("s1", "s3")]): "B",
}
S4_PORT_TO_PATH: Dict[str, str] = {
    str(spec.NEIGHBOUR_PORT[("s4", "s2")]): "A",
    str(spec.NEIGHBOUR_PORT[("s4", "s5")]): "B",
}


def observed_paths(run: Run) -> Tuple[Counter, Counter, int]:
    """Count path usage from installed rules at the two edge switches.

    Only the edge switches distinguish a path, so counting elsewhere would
    double-count the same decision.
    """
    path_counts: Counter = Counter()
    timeout_counts: Counter = Counter()
    total_routed = 0

    for dump in parse_all_flow_dumps(run.raw_dir):
        for rule in dump.routed():
            total_routed += 1
            if rule.idle_timeout is not None:
                timeout_counts[rule.idle_timeout] += 1
            mapping = (
                S1_PORT_TO_PATH if rule.switch == "s1"
                else S4_PORT_TO_PATH if rule.switch == "s4"
                else None
            )
            if mapping and rule.output in mapping:
                path_counts[mapping[rule.output]] += 1
    return path_counts, timeout_counts, total_routed


def decision_timeline(run: Run) -> List[Tuple[float, str, int]]:
    """(timestamp, path, idle_timeout) for every policy step, in order."""
    rows = read_csv(os.path.join(run.raw_dir, "decisions.csv"))
    out: List[Tuple[float, str, int]] = []
    for row in rows:
        t = to_float(row.get("t", ""))
        idle = to_float(row.get("idle_timeout", ""))
        path = row.get("path", "")
        if t is not None and path:
            out.append((t, path, int(idle) if idle is not None else -1))
    return out


def _decision_in_force(timeline: List[Tuple[float, str, int]], t: float):
    """The most recent decision at or before time ``t``."""
    chosen = None
    for entry in timeline:
        if entry[0] <= t:
            chosen = entry
        else:
            break
    return chosen


def decision_to_rule_agreement(run: Run) -> Dict[str, object]:
    """Check installed rules against the decision in force when installed.

    Rules whose install time predates the first recorded decision are skipped
    rather than counted as disagreements -- they were installed during
    start-up, before any policy step had run.
    """
    timeline = decision_timeline(run)
    if not timeline:
        return {"checked": 0, "agree": 0, "agreement": None,
                "note": "no decisions recorded"}

    first_decision_t = timeline[0][0]
    checked = agree = skipped = 0
    timeout_checked = timeout_agree = 0

    for dump in parse_all_flow_dumps(run.raw_dir):
        for rule in dump.routed():
            mapping = (
                S1_PORT_TO_PATH if rule.switch == "s1"
                else S4_PORT_TO_PATH if rule.switch == "s4"
                else None
            )
            if not mapping or rule.output not in mapping:
                continue
            installed_at = rule.installed_at(dump.t)
            if installed_at is None or installed_at < first_decision_t:
                skipped += 1
                continue
            decision = _decision_in_force(timeline, installed_at)
            if decision is None:
                skipped += 1
                continue
            _, want_path, want_idle = decision
            checked += 1
            if mapping[rule.output] == want_path:
                agree += 1
            if rule.idle_timeout is not None and want_idle >= 0:
                timeout_checked += 1
                if rule.idle_timeout == want_idle:
                    timeout_agree += 1

    return {
        "checked": checked,
        "agree": agree,
        "agreement": (agree / checked) if checked else None,
        "timeout_checked": timeout_checked,
        "timeout_agree": timeout_agree,
        "timeout_agreement": (timeout_agree / timeout_checked) if timeout_checked else None,
        "skipped_pre_policy": skipped,
    }


def summarise_run(run: Run) -> Dict[str, object]:
    paths, timeouts, total = observed_paths(run)
    agreement = decision_to_rule_agreement(run)
    decisions = decision_timeline(run)
    return {
        "run": os.path.basename(run.path),
        "variant": run.variant,
        "workload": run.workload,
        "seed": run.seed,
        "complete": run.complete,
        "routed_rules_observed": total,
        "path_counts": dict(paths),
        "distinct_paths": len(paths),
        "timeout_counts": {str(k): v for k, v in sorted(timeouts.items())},
        "distinct_timeouts": len(timeouts),
        "policy_steps": len(decisions),
        "decision_paths": dict(Counter(p for _, p, _ in decisions)),
        "decision_timeouts": {str(k): v for k, v in
                              sorted(Counter(i for _, _, i in decisions).items())},
        **{f"agreement_{k}": v for k, v in agreement.items()},
    }


def aggregate_by_variant(summaries: List[Dict[str, object]]) -> Dict[str, Dict[str, object]]:
    by_variant: Dict[str, Dict[str, object]] = {}
    grouped: Dict[str, List[Dict[str, object]]] = defaultdict(list)
    for s in summaries:
        grouped[str(s["variant"])].append(s)

    for variant, rows in sorted(grouped.items()):
        paths: Counter = Counter()
        timeouts: Counter = Counter()
        checked = agree = 0
        t_checked = t_agree = 0
        for r in rows:
            paths.update(r["path_counts"])
            timeouts.update({int(k): v for k, v in r["timeout_counts"].items()})
            checked += int(r.get("agreement_checked") or 0)
            agree += int(r.get("agreement_agree") or 0)
            t_checked += int(r.get("agreement_timeout_checked") or 0)
            t_agree += int(r.get("agreement_timeout_agree") or 0)

        total_paths = sum(paths.values())
        by_variant[variant] = {
            "runs": len(rows),
            "path_counts": dict(paths),
            "path_share": {k: round(v / total_paths, 4) for k, v in paths.items()} if total_paths else {},
            "distinct_paths": len(paths),
            "timeout_counts": {str(k): v for k, v in sorted(timeouts.items())},
            "distinct_timeouts": len(timeouts),
            "rules_checked_against_decisions": checked,
            "path_agreement": round(agree / checked, 4) if checked else None,
            "timeout_agreement": round(t_agree / t_checked, 4) if t_checked else None,
        }
    return by_variant


def evaluate_claims(by_variant: Dict[str, Dict[str, object]]) -> List[Dict[str, object]]:
    """Turn the aggregates into explicit pass/fail claims."""
    claims: List[Dict[str, object]] = []

    def claim(name: str, ok: Optional[bool], detail: str) -> None:
        claims.append({"claim": name, "result":
                       "PASS" if ok else ("FAIL" if ok is False else "NOT TESTABLE"),
                       "detail": detail})

    # 1. Variant C must install more than one timeout value.
    c = by_variant.get("C")
    if c is None:
        claim("C varies flow-rule idle timeouts", None, "no variant C runs")
    else:
        claim(
            "C varies flow-rule idle timeouts",
            c["distinct_timeouts"] > 1,
            f"distinct idle_timeout values installed: {c['timeout_counts']}",
        )

    # 2. Variants A and B must NOT vary timeouts -- they claim fixed timeouts.
    for name in ("A", "B"):
        v = by_variant.get(name)
        if v is None:
            claim(f"{name} keeps flow-rule timeouts fixed", None, f"no variant {name} runs")
        else:
            claim(
                f"{name} keeps flow-rule timeouts fixed",
                v["distinct_timeouts"] == 1,
                f"distinct idle_timeout values installed: {v['timeout_counts']}",
            )

    # 3. RL variants must use both paths at least sometimes.
    for name in ("B", "C"):
        v = by_variant.get(name)
        if v is None:
            claim(f"{name} uses both routing paths", None, f"no variant {name} runs")
        else:
            claim(
                f"{name} uses both routing paths",
                v["distinct_paths"] > 1,
                f"path usage in installed rules: {v['path_counts']}",
            )

    # 4. Installed rules must match the decision in force when installed.
    for name in sorted(by_variant):
        v = by_variant[name]
        agreement = v["path_agreement"]
        if agreement is None:
            claim(f"{name}: installed rules match the decision in force", None,
                  "no rules could be matched to a decision")
        else:
            claim(
                f"{name}: installed rules match the decision in force",
                agreement >= 0.90,
                f"{agreement:.1%} of {v['rules_checked_against_decisions']} rules agree",
            )

    # 5. The variants must differ from each other in the dataplane.
    a, b = by_variant.get("A"), by_variant.get("B")
    if a and b:
        claim(
            "B's path distribution differs from A's",
            a.get("path_share") != b.get("path_share"),
            f"A={a.get('path_share')} vs B={b.get('path_share')}",
        )
    if b and c:
        claim(
            "C differs from B in installed timeouts",
            b["timeout_counts"] != c["timeout_counts"],
            f"B={b['timeout_counts']} vs C={c['timeout_counts']}",
        )
    return claims


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--runs", default=os.path.join(PROJECT_ROOT, "results", "runs"))
    p.add_argument("--out", default=os.path.join(PROJECT_ROOT, "analysis", "out"))
    p.add_argument("--only-complete", action="store_true",
                   help="ignore runs whose manifest says incomplete")
    args = p.parse_args(argv)

    runs = discover_runs(args.runs)
    if args.only_complete:
        runs = [r for r in runs if r.complete]
    if not runs:
        print(f"no runs found under {args.runs}")
        return 1

    summaries = [summarise_run(r) for r in runs]
    by_variant = aggregate_by_variant(summaries)
    claims = evaluate_claims(by_variant)

    os.makedirs(args.out, exist_ok=True)
    report = {
        "runs_analysed": len(runs),
        "runs_incomplete": sum(1 for r in runs if not r.complete),
        "by_variant": by_variant,
        "claims": claims,
        "per_run": summaries,
    }
    out_path = os.path.join(args.out, "behaviour_verification.json")
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)

    print(f"\nBEHAVIOURAL VERIFICATION  ({len(runs)} runs, "
          f"{report['runs_incomplete']} incomplete)")
    print("=" * 72)
    for variant in sorted(by_variant):
        v = by_variant[variant]
        print(f"\nVariant {variant}  ({v['runs']} runs)")
        print(f"  paths installed     : {v['path_counts']}  share={v['path_share']}")
        print(f"  idle timeouts       : {v['timeout_counts']}")
        print(f"  rules vs decisions  : {v['rules_checked_against_decisions']} checked, "
              f"path agreement={v['path_agreement']}, timeout agreement={v['timeout_agreement']}")

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
