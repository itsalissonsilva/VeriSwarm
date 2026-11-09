"""Runs the TLC matrix, preserving logs and exit codes, and checks every prediction.

Usage: python scripts/check_models.py [--groups a,b] [--sweep] [--jobs N] [--timeout S]
Exit status is non-zero if any job has an unexpected outcome or a tool error.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from veriswarm.matrix import TIMED_OUT_AND_REPLACED, all_jobs, schedule_sweep_jobs  # noqa: E402
from veriswarm.tlc import java_version, run_job  # noqa: E402

LOG_DIR = ROOT / "artifacts" / "tlc"
TRACE_DIR = ROOT / "artifacts" / "traces"
CFG_DIR = ROOT / "specs" / "configs"


def prune_stale(current_ids: set[str]) -> None:
    """Removes this script's own outputs for jobs no longer in the matrix.

    Only top-level generated files with these exact suffixes are touched, so
    artifacts/tlc/timed_out/ and artifacts/tlc/sweep/ are left alone.
    """
    for directory, suffix in ((LOG_DIR, ".log"), (CFG_DIR, ".cfg"), (TRACE_DIR, ".tlc.json")):
        for path in directory.iterdir():
            if path.is_file() and path.name.endswith(suffix):
                if path.name[: -len(suffix)] not in current_ids:
                    path.unlink()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--groups", default="", help="comma-separated job groups (default: all)")
    ap.add_argument("--sweep", action="store_true", help="also run the per-schedule V0 sweep")
    ap.add_argument("--sweep-only", action="store_true", help="run only the per-schedule sweep")
    ap.add_argument("--sweep-len", type=int, default=4)
    ap.add_argument("--jobs", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=600)
    args = ap.parse_args()

    jobs = [] if args.sweep_only else all_jobs()
    if args.groups:
        wanted = set(args.groups.split(","))
        jobs = [j for j in jobs if j.group in wanted]
    sweep = schedule_sweep_jobs("V0", args.sweep_len, 2) if (args.sweep or args.sweep_only) else []

    # Heavy jobs run one at a time with several TLC workers. The rest run side by
    # side with one worker each, which keeps witnesses deterministic.
    light = [j for j in jobs if j.workers == 1]
    heavy = [j for j in jobs if j.workers > 1]

    print(f"java: {java_version()}")
    print(f"{len(light)} light jobs, {len(heavy)} heavy jobs, {len(sweep)} sweep jobs")

    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="veriswarm_tlc_") as tmp:
        meta = Path(tmp)

        def work(job, log_dir, cfg_dir, trace_dir):
            result, _ = run_job(job, log_dir, cfg_dir, trace_dir, meta, timeout_s=args.timeout)
            mark = "ok " if result.as_expected else "!! "
            size = f"{result.distinct_states} states" if result.distinct_states is not None else "-"
            print(f"{mark}{result.job_id:42s} {result.status:10s} {size:>16s} {result.runtime_seconds:7.1f}s", flush=True)
            return result

        with ThreadPoolExecutor(args.jobs) as pool:
            light_futures = [pool.submit(work, j, LOG_DIR, CFG_DIR, TRACE_DIR) for j in light]
            sweep_futures = [
                pool.submit(work, j, LOG_DIR / "sweep", LOG_DIR / "sweep" / "cfg", LOG_DIR / "sweep" / "traces")
                for j in sweep
            ]
            results = [f.result() for f in light_futures]
            sweep_results = [f.result() for f in sweep_futures]
        results += [work(j, LOG_DIR, CFG_DIR, TRACE_DIR) for j in heavy]

    wall = time.perf_counter() - started
    summary_dir = ROOT / "artifacts" / "summary"
    summary_dir.mkdir(parents=True, exist_ok=True)

    if results:
        existing = {}
        out = summary_dir / "tlc_results.json"
        if args.groups and out.exists():
            existing = {r["job_id"]: r for r in json.loads(out.read_text(encoding="utf-8"))["results"]}
        for r in results:
            existing[r.job_id] = r.to_dict()
        out.write_text(
            json.dumps(
                {
                    "java": java_version(),
                    "wall_seconds_last_invocation": round(wall, 1),
                    "timed_out_and_replaced": TIMED_OUT_AND_REPLACED,
                    "results": [existing[k] for k in sorted(existing)],
                },
                indent=1,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
    if sweep_results:
        (summary_dir / "tlc_schedule_sweep.json").write_text(
            json.dumps(
                {
                    "java": java_version(),
                    "wall_seconds_last_invocation": round(wall, 1),
                    "results": [r.to_dict() for r in sweep_results],
                },
                indent=1,
                sort_keys=True,
            ),
            encoding="utf-8",
        )

    if not args.groups and not args.sweep_only:
        prune_stale({j.id for j in jobs})

    everything = results + sweep_results
    bad = [r for r in everything if not r.as_expected]
    tool_errors = [r for r in everything if r.status == "tool_error"]
    print(f"\n{len(everything)} jobs in {wall:.1f}s; {len(bad)} unexpected; {len(tool_errors)} tool errors")
    for r in bad:
        print(
            f"  UNEXPECTED {r.job_id}: predicted {r.expect_status}/{r.expect_property}, "
            f"got {r.status}/{r.violated_property}. {r.detail} See {r.log_path}"
        )
    return 1 if bad or tool_errors else 0


if __name__ == "__main__":
    shutil.which("java") or sys.exit("java was not found on PATH; TLC cannot run")
    raise SystemExit(main())
