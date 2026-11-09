"""Builds the Lean proofs and records what they rest on.

Usage: python scripts/check_lean.py
Writes artifacts/summary/lean_check.json and artifacts/raw/lean_export.jsonl.
Fails if the build fails, if `sorry` or an `axiom` declaration appears in the sources,
or if a theorem depends on anything beyond Lean's three standard axioms.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEAN = ROOT / "lean"
SUMMARY = ROOT / "artifacts" / "summary"
RAW = ROOT / "artifacts" / "raw"

STANDARD_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
_AXIOM_LINE = re.compile(r"'([^']+)' depends on axioms: \[(.*?)\]", re.DOTALL)
_NO_AXIOMS = re.compile(r"'([^']+)' does not depend on any axioms")
_SORRY = re.compile(r"\bsorry\b|^\s*axiom\b", re.MULTILINE)


def run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=LEAN, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


def main() -> int:
    SUMMARY.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)

    if shutil.which("lake") is None:
        result = {"status": "not_run", "detail": "lake was not found on PATH"}
        (SUMMARY / "lean_check.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
        print(result["detail"])
        return 1

    sources = sorted(LEAN.glob("VeriSwarm/*.lean")) + [LEAN / "VeriSwarm.lean", LEAN / "GatewayCore.lean"]
    forbidden = [str(p.relative_to(ROOT)) for p in sources if _SORRY.search(p.read_text(encoding="utf-8"))]

    build = run(["lake", "build"])
    # The gateway's decision core, compiled from the definitions VeriSwarm/Core.lean proves about.
    core_build = run(["lake", "build", "veriswarm-core"])
    axioms = run(["lake", "env", "lean", "Axioms.lean"])
    export = run(["lake", "env", "lean", "--run", "Export.lean"])

    theorems = {}
    for name, listed in _AXIOM_LINE.findall(axioms.stdout):
        theorems[name] = sorted(a.strip() for a in listed.replace("\n", " ").split(",") if a.strip())
    for name in _NO_AXIOMS.findall(axioms.stdout):
        theorems[name] = []

    expected = len(re.findall(r"^#print axioms", (LEAN / "Axioms.lean").read_text(encoding="utf-8"), re.MULTILINE))
    extra = {t: sorted(set(a) - STANDARD_AXIOMS) for t, a in theorems.items() if set(a) - STANDARD_AXIOMS}

    if export.returncode == 0:
        (RAW / "lean_export.jsonl").write_text(export.stdout, encoding="utf-8")

    version = run(["lean", "--version"]).stdout.strip()
    ok = (
        build.returncode == 0
        and core_build.returncode == 0
        and axioms.returncode == 0
        and export.returncode == 0
        and not forbidden
        and not extra
        and len(theorems) == expected
    )
    result = {
        "status": "checked" if ok else "failed",
        "lean_version": version,
        "toolchain": (LEAN / "lean-toolchain").read_text(encoding="utf-8").strip(),
        "build_exit": build.returncode,
        "core_build_exit": core_build.returncode,
        "sources": {str(p.relative_to(ROOT).as_posix()): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        "sorry_or_axiom_declarations": forbidden,
        "theorems": theorems,
        "theorems_expected": expected,
        "non_standard_axioms": extra,
        "standard_axioms_allowed": sorted(STANDARD_AXIOMS),
        "export_lines": len(export.stdout.splitlines()) if export.returncode == 0 else 0,
        "stderr": (build.stderr + axioms.stderr + export.stderr)[-2000:],
    }
    (SUMMARY / "lean_check.json").write_text(json.dumps(result, indent=1, sort_keys=True), encoding="utf-8")

    print(f"lean: {version}")
    print(f"build exit {build.returncode}; {len(theorems)}/{expected} theorems recorded")
    for name, listed in sorted(theorems.items()):
        print(f"  {name}: {', '.join(listed) if listed else 'no axioms'}")
    print(f"export lines: {result['export_lines']}; forbidden markers: {forbidden or 'none'}; status: {result['status']}")
    if not ok:
        print(build.stdout[-1500:], build.stderr[-1500:], axioms.stderr[-800:], export.stderr[-800:])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
