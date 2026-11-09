#!/usr/bin/env python3
"""Portable task runner. The Makefile calls this, and it also works where make is absent.

Usage: python tasks.py <target> [<target> ...]
Targets: setup test models replay correspondence lean experiments figures report
         manifest reproduce demo llm llm_summary design explainer lean_core
"""

from __future__ import annotations

import os
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def python() -> str:
    return str(PY) if PY.exists() else sys.executable


def run(*args: str, py: bool = True, cwd: Path = ROOT) -> None:
    cmd = ([python()] if py else []) + list(args)
    print(f"\n$ {' '.join(cmd)}", flush=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    subprocess.run(cmd, cwd=cwd, check=True, env=env)


def setup() -> None:
    if not PY.exists():
        print("creating .venv")
        venv.create(VENV, with_pip=True)
    run("-m", "pip", "install", "--quiet", "-r", "requirements.lock")
    run("-m", "pip", "install", "--quiet", "--no-deps", "-e", ".")
    run("scripts/fetch_tools.py")


def test() -> None:
    run("scripts/run_tests.py")


def models() -> None:
    run("scripts/check_models.py", "--sweep")


def replay() -> None:
    run("scripts/replay_trace.py")


def correspondence() -> None:
    run("scripts/check_correspondence.py")


def lean() -> None:
    run("scripts/check_lean.py")


def lean_core() -> None:
    """Runs the partitioned gateway on the compiled, proved Lean core and checks it."""
    run("scripts/check_lean_core.py")


def experiments() -> None:
    run("scripts/run_experiments.py")


def figures() -> None:
    run("scripts/make_figures.py")


def report() -> None:
    run("scripts/build_report.py")


def manifest() -> None:
    run("scripts/make_manifest.py")


def demo() -> None:
    run("scripts/demo.py")


def explainer() -> None:
    """Rebuilds the interactive explainer page, docs/explainer/index.html, from the artifacts."""
    run("scripts/make_explainer.py")


def llm() -> None:
    """Separate and optional: prints the plan. Spending needs `python scripts/run_llm.py --yes`."""
    run("scripts/run_llm.py")


def design() -> None:
    """All 96 gateway designs through the design checker; uses TLC verdicts for named variants."""
    run("scripts/check_design.py", "--space")


def llm_summary() -> None:
    """Re-derives the LLM statistics from recorded episodes. Makes no API call."""
    run("scripts/summarize_llm.py")


def reproduce() -> None:
    """Every core check and analysis, from fixed configurations. No model is called:
    recorded LLM episodes, if any, are summarized again from their files."""
    models()
    replay()
    correspondence()
    lean()
    lean_core()
    experiments()
    design()
    llm_summary()
    test()
    figures()
    report()
    manifest()


TARGETS = {
    f.__name__: f
    for f in (setup, test, models, replay, correspondence, lean, experiments, figures,
              report, manifest, reproduce, demo, llm, llm_summary, design, explainer, lean_core)
}


def main(argv: list[str]) -> int:
    if not argv or any(a not in TARGETS for a in argv):
        print(__doc__)
        return 2
    try:
        for target in argv:
            TARGETS[target]()
    except subprocess.CalledProcessError as exc:
        print(f"\nFAILED ({exc.returncode}): {' '.join(map(str, exc.cmd))}", file=sys.stderr)
        return exc.returncode or 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
