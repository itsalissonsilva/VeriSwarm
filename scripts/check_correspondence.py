"""Compares TLC's full reachable graph with the Python exploration, both directions.

Usage: python scripts/check_correspondence.py [--jobs N]
Writes artifacts/summary/correspondence.json. Exit status is non-zero on any difference.
This is tested correspondence on listed finite configurations, not a refinement proof.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from veriswarm.correspondence import default_cases, run_case  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=5)
    args = ap.parse_args()

    results = []
    with tempfile.TemporaryDirectory(prefix="veriswarm_corr_") as tmp, ThreadPoolExecutor(args.jobs) as pool:
        work = Path(tmp)

        def one(case):
            case_id, mode, cfg = case
            r = run_case(case_id, mode, cfg, work)
            mark = "match" if r.match else "DIFF "
            print(f"{mark} {case_id:28s} nodes {r.tlc_nodes:6d}/{r.python_nodes:6d} edges {r.tlc_edges:6d}/{r.python_edges:6d}", flush=True)
            return r

        results = list(pool.map(one, default_cases()))

    summary = ROOT / "artifacts" / "summary"
    summary.mkdir(parents=True, exist_ok=True)
    (summary / "correspondence.json").write_text(
        json.dumps(
            {
                "kind": "tested correspondence on finite configurations; not a refinement proof",
                "cases": len(results),
                "matching": sum(r.match for r in results),
                "total_nodes_compared": sum(r.tlc_nodes for r in results),
                "total_edges_compared": sum(r.tlc_edges for r in results),
                "results": [r.to_dict() for r in results],
            },
            indent=1,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    bad = [r for r in results if not r.match]
    print(f"\n{len(results)} cases; {len(bad)} differ")
    for r in bad:
        print(f"  {r.case_id}: {r.example_difference}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
