"""Checks whether a gateway design lets one principal's actions reach another's observations.

Usage:
  python scripts/check_design.py --backing private
  python scripts/check_design.py --backing private --shared-quota --schedule AMBM
  python scripts/check_design.py --space          # all 96 feature combinations -> artifacts/summary/design_space.json

A design is a combination of the features the ten variants are built from. The check is
the relational property on the Python product exploration (see src/veriswarm/design.py for
what that evidence is and is not). When the features equal a named variant, the TLC verdict
for that variant is printed alongside.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from veriswarm.design import Design, check, design_space  # noqa: E402

OUT = ROOT / "artifacts" / "summary" / "design_space.json"
TLC = ROOT / "artifacts" / "summary" / "tlc_results.json"


def tlc_verdict(variant: str | None) -> str | None:
    if variant is None or not TLC.exists():
        return None
    results = {r["job_id"]: r for r in json.loads(TLC.read_text(encoding="utf-8"))["results"]}
    job = results.get(f"unbounded_{variant}") or results.get(f"pair_{variant}_R2")
    return f"{job['status']} ({job['job_id']})" if job else None


def show(result: dict) -> None:
    verdict = "LEAKS" if result["leaks"] else "no leak found"
    print(f"design: {result['description']}")
    print(f"verdict: {verdict}  (1 round: {'ok' if result['bounded']['1'] else 'leak'}, "
          f"2 rounds: {'ok' if result['bounded']['2'] else 'leak'}, every horizon: "
          f"{'not checked (unbounded counter or clock)' if result['all_horizon'] is None else 'ok' if result['all_horizon'] else 'leak'})")
    leak = result["leakage"]
    if "bits_per_round" in leak:
        print(f"exact leakage: {leak['max_distinct_histories']} distinct receiver histories over 2 rounds, "
              f"{leak['bits_per_round']:.3f} bits per round")
    elif leak.get("capacity_bits_per_round_at_p_0_5") is not None:
        print(f"capacity at noise p=0.5: {leak['capacity_bits_per_round_at_p_0_5']:.3f} bits per round")
    print(f"a sender (or noise) can make the receiver's job fail: {'yes' if result['receiver_can_be_blocked'] else 'no'}")
    if result["witness"]:
        print("witness (two runs, same receiver, different senders):")
        for step in result["witness"]:
            if step["slot"] == "M":
                print("  M  maintenance")
            else:
                print(f"  {step['slot']}  left: {step['left']:<8} -> {step['left_reply']:<5}   right: {step['right']:<8} -> {step['right_reply']}")
    if result["named_variant"]:
        tlc = tlc_verdict(result["named_variant"])
        print(f"same features as {result['named_variant']}; TLC: {tlc or 'not recorded'}")
    else:
        print("no named variant has these features: evidence is the Python exploration only (tested correspondence, not proof)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backing", choices=["shared", "private", "aliased"], default="shared")
    ap.add_argument("--shared-quota", action="store_true", help="all locks draw on one capacity of one")
    ap.add_argument("--exposed-counter", action="store_true", help="replies carry the gateway's global sequence number")
    ap.add_argument("--work-conserving", action="store_true", help="an idle sender's slot is given to the next principal")
    ap.add_argument("--noisy", action="store_true", help="the gateway answers the receiver busy at random")
    ap.add_argument("--receiver-denied", action="store_true")
    ap.add_argument("--schedule", default="ABM", help="public slot order, e.g. ABM or AMBM (M releases every hold)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--space", action="store_true", help="check all 96 combinations and write a summary")
    args = ap.parse_args()

    if args.space:
        rows = []
        for d in design_space():
            r = check(d)
            r["tlc"] = tlc_verdict(r["named_variant"])
            rows.append(r)
        counts = Counter((r["design"]["schedule"], r["leaks"]) for r in rows)
        OUT.write_text(json.dumps({"designs": len(rows), "rows": rows}, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"checked {len(rows)} designs: {sum(r['leaks'] for r in rows)} leak, {sum(not r['leaks'] for r in rows)} do not")
        for (schedule, leaks), n in sorted(counts.items()):
            print(f"  schedule {schedule}: {n} {'leak' if leaks else 'no leak'}")
        print(f"wrote {OUT.relative_to(ROOT)}")
        return 0

    design = Design(args.backing, args.shared_quota, args.exposed_counter, args.work_conserving, args.noisy,
                    args.schedule, args.receiver_denied)
    result = check(design)
    if args.json:
        print(json.dumps(result, indent=1))
    else:
        show(result)
    return 1 if result["leaks"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
