"""Replays every TLC witness through the Python semantics and records the agreement.

Usage: python scripts/replay_trace.py
Writes artifacts/summary/replay_results.json and one paired table per witness.
Exit status is non-zero if any witness fails to replay exactly.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from veriswarm.traces import (  # noqa: E402
    ReplayMismatch,
    load_trace,
    replay_pair_trace,
    replay_single_trace,
)

TRACE_DIR = ROOT / "artifacts" / "traces"
SUMMARY = ROOT / "artifacts" / "summary"


def main() -> int:
    SUMMARY.mkdir(parents=True, exist_ok=True)
    rows, failures = [], []
    paths = sorted(TRACE_DIR.glob("*.tlc.json"))
    if not paths:
        print("no traces found; run scripts/check_models.py first")
        return 1

    for path in paths:
        trace = load_trace(path)
        paired = "l" in trace["states"][0]["vars"]
        row = {
            "job_id": trace["job_id"],
            "paired": paired,
            "violated_property": trace["violated_property"],
            "tlc_states_in_trace": len(trace["states"]),
        }
        try:
            if paired:
                report = replay_pair_trace(trace)
                row.update(
                    {
                        "status": "replayed_exactly",
                        "variant": report.variant,
                        "schedule": report.schedule,
                        "left_secret": report.left_secret,
                        "right_secret": report.right_secret,
                        "fields_compared": report.fields_compared,
                        "first_divergent_step": report.first_divergent_step,
                        "steps": [asdict(s) for s in report.steps],
                        "left_view": report.left_view,
                        "right_view": report.right_view,
                    }
                )
            else:
                row.update({"status": "replayed_exactly", "slots_replayed": replay_single_trace(trace)})
        except ReplayMismatch as exc:
            row.update({"status": "mismatch", "detail": str(exc)})
            failures.append(trace["job_id"])
        rows.append(row)
        print(f"{row['status']:18s} {trace['job_id']}")

    (SUMMARY / "replay_results.json").write_text(
        json.dumps({"witnesses": len(rows), "failures": failures, "rows": rows}, indent=1, sort_keys=True),
        encoding="utf-8",
    )
    print(f"\n{len(rows)} witnesses replayed; {len(failures)} mismatches")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
