"""Verifies the artifact from a fresh copy with a fresh environment.

Usage: python scripts/clean_room.py <empty-target-directory> [--skip-models]

Copies only source files into the target, creating no link back to this checkout: no
.venv, no artifacts, no generated configs, no report outputs. Then it runs setup and
reproduce there and compares every deterministic summary with this checkout's, so a
result that depends on leftover state shows up as a difference.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SOURCE_ITEMS = [
    "README.md", "PREEXISTING.md", "STATUS.md", "Makefile", "tasks.py", "pyproject.toml", "requirements.lock",
    ".gitignore", "veriswarm_hackathon_agent_plan.md", "veriswarm_research_program.md",
    "specs/Semantics.tla", "specs/Workspace.tla", "specs/WorkspacePair.tla",
    "src", "scripts", "tests", "docs", "submission/checklist.md",
    "lean/VeriSwarm", "lean/VeriSwarm.lean", "lean/Axioms.lean", "lean/Export.lean", "lean/GatewayCore.lean",
    "lean/lakefile.toml", "lean/lean-toolchain",
    "report/report.tex", "report/references.bib", "report/metadata.tex", "report/fonts",
    "tools/tla2tools.lock.json", "requirements-llm.lock",
]
# Recorded model outputs are data, not something a rerun can regenerate without paying
# for new calls. They are copied so the clean room re-derives their summary from them.
RECORDED_DATA = ["artifacts/raw/llm", "artifacts/raw/llm_refusal_probe.json", "artifacts/raw/llm_pairs",
                 "artifacts/raw/llm_pairs_qwen", "artifacts/raw/llm_pairs_llama", "artifacts/raw/llm_pairs_grid"]

# Summaries that must match byte for byte. TLC summaries hold timings and paths, so
# they are compared by verdict and state count instead.
DETERMINISTIC = ["transmission", "benign", "exact_leakage", "noise", "monitors", "broadcast",
                 "schedule_family", "fixed_policy_histories", "design_space", "llm", "llm_pairs", "llm_pairs_qwen",
                 "llm_pairs_llama", "llm_pairs_grid", "lean_core"]


def copy_sources(target: Path) -> None:
    for item in SOURCE_ITEMS + [d for d in RECORDED_DATA if (ROOT / d).exists()]:
        src = ROOT / item
        dst = target / item
        if not src.exists():
            raise SystemExit(f"missing source item: {item}")
        if src.is_dir():
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".lake"))
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def run(cmd: list[str], cwd: Path) -> None:
    print(f"\n$ {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, cwd=cwd, check=True)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verdicts(path: Path) -> dict:
    results = json.loads(path.read_text(encoding="utf-8"))["results"]
    return {r["job_id"]: (r["status"], r["violated_property"], r["distinct_states"]) for r in results}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("target")
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()

    target = Path(args.target).resolve()
    if target.exists() and any(target.iterdir()):
        raise SystemExit(f"{target} is not empty")
    target.mkdir(parents=True, exist_ok=True)

    copy_sources(target)
    run([args.python, "tasks.py", "setup"], target)
    run([args.python, "tasks.py", "reproduce"], target)

    here = ROOT / "artifacts" / "summary"
    there = target / "artifacts" / "summary"
    differences = []
    for name in DETERMINISTIC:
        if digest(here / f"{name}.json") != digest(there / f"{name}.json"):
            differences.append(f"{name}.json differs")
    for name in ("tlc_results", "tlc_schedule_sweep"):
        a, b = verdicts(here / f"{name}.json"), verdicts(there / f"{name}.json")
        for key in sorted(set(a) | set(b)):
            if a.get(key) != b.get(key):
                differences.append(f"{name}:{key}: {a.get(key)} vs {b.get(key)}")
    corr_here = json.loads((here / "correspondence.json").read_text(encoding="utf-8"))
    corr_there = json.loads((there / "correspondence.json").read_text(encoding="utf-8"))
    if {r["case_id"]: r["graph_sha256"] for r in corr_here["results"]} != {r["case_id"]: r["graph_sha256"] for r in corr_there["results"]}:
        differences.append("correspondence graph hashes differ")

    result = {
        "target": str(target),
        "deterministic_summaries_compared": DETERMINISTIC,
        "tlc_jobs_compared": len(verdicts(there / "tlc_results.json")) + len(verdicts(there / "tlc_schedule_sweep.json")),
        "differences": differences,
        "identical": not differences,
    }
    (ROOT / "artifacts" / "summary" / "clean_room.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps(result, indent=1))
    return 0 if not differences else 1


if __name__ == "__main__":
    raise SystemExit(main())
