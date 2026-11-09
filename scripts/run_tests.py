"""Runs pytest and records the counts for the report.

Usage: python scripts/run_tests.py [pytest args]
Writes artifacts/summary/pytest.json and exits with pytest's exit code.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    # A project-local base directory, since the default one under the system temp folder
    # can be unreadable if another user or a sandbox created it first.
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--color=no", f"--basetemp={ROOT / '.pytest_tmp'}", *sys.argv[1:]],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    print(proc.stdout[-4000:])
    print(proc.stderr[-1000:], file=sys.stderr)

    tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
    counts = {key: int(n) for n, key in re.findall(r"(\d+) (passed|failed|skipped|errors?|deselected)", tail)}
    summary = {
        "passed": counts.get("passed", 0),
        "failed": counts.get("failed", 0) + counts.get("error", 0) + counts.get("errors", 0),
        "skipped": counts.get("skipped", 0),
        "exit_code": proc.returncode,
        "last_line": tail,
    }
    out = ROOT / "artifacts" / "summary" / "pytest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=1, sort_keys=True), encoding="utf-8")
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main())
