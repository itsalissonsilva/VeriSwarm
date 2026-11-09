"""Compares the Lean model's executable semantics with the Python semantics.

Reads artifacts/raw/lean_export.jsonl, written by scripts/check_lean.py. This ties the
proved system to the simulator on a finite set of runs. It is differential testing,
not a proof that the two systems are the same.
"""

import json
from pathlib import Path

import pytest

from veriswarm.config import MAINT, ModelConfig
from veriswarm.semantics import current_slot, initial_state, is_terminal, step

EXPORT = Path(__file__).resolve().parents[1] / "artifacts" / "raw" / "lean_export.jsonl"


def python_responses(row):
    cfg = ModelConfig("V2", schedule=tuple(row["schedule"]), max_rounds=row["rounds"], track_audit=False)
    s = initial_state(cfg)
    ua, ub = iter(row["ua"]), iter(row["ub"])
    while not is_terminal(cfg, s):
        who = current_slot(cfg, s)
        if who == MAINT:
            s = step(cfg, s)
        elif who == "A":
            s = step(cfg, s, next(ua))
        else:
            s = step(cfg, s, next(ub))
    return [e.resp for e in s.obs]


def test_lean_and_python_agree_on_every_exported_run():
    if not EXPORT.exists():
        pytest.skip("run scripts/check_lean.py first")
    rows = [json.loads(line) for line in EXPORT.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(rows) == 264  # 8 + 64 + 64 + 64 + 64 enumerated action sequences
    for row in rows:
        assert python_responses(row) == row["resps"], row


def test_lean_export_covers_the_expected_schedules():
    if not EXPORT.exists():
        pytest.skip("run scripts/check_lean.py first")
    rows = [json.loads(line) for line in EXPORT.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert {(r["schedule"], r["rounds"]) for r in rows} == {
        ("ABM", 1), ("ABM", 2), ("ABABM", 1), ("AB", 2), ("BAM", 2)
    }
