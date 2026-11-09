"""Regenerates every measurement from fixed seeds.

Usage: python scripts/run_experiments.py
Writes raw JSONL to artifacts/raw and summaries to artifacts/summary.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from veriswarm import experiments as ex  # noqa: E402

RAW = ROOT / "artifacts" / "raw"
SUMMARY = ROOT / "artifacts" / "summary"


def write(name: str, value) -> None:
    (SUMMARY / f"{name}.json").write_text(json.dumps(value, indent=1, sort_keys=True), encoding="utf-8")


def main() -> int:
    RAW.mkdir(parents=True, exist_ok=True)
    SUMMARY.mkdir(parents=True, exist_ok=True)
    steps = [
        ("exact_leakage", ex.experiment_exact_leakage, ()),
        ("transmission", ex.experiment_transmission, (RAW,)),
        ("benign", ex.experiment_benign, (RAW,)),
        ("noise", ex.experiment_noise, ()),
        ("monitors", ex.experiment_monitors, ()),
        ("broadcast", ex.experiment_broadcast, ()),
        ("schedule_family", ex.experiment_schedule_family, ()),
        ("fixed_policy_histories", ex.cross_check_values, ()),
    ]
    for name, fn, args in steps:
        started = time.perf_counter()
        write(name, fn(*args))
        print(f"{name:26s} {time.perf_counter() - started:7.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
