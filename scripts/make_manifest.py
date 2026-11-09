"""Records exactly what produced the artifacts: tool versions and file hashes.

Usage: python scripts/make_manifest.py
Writes artifacts/MANIFEST.json. Generated logs are hashed too, so a later reader can tell
whether a file changed after this run.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HASHED = {
    "specs": ["specs/*.tla", "specs/configs/*.cfg"],
    "source": ["src/veriswarm/*.py", "scripts/*.py", "tests/*.py", "tasks.py", "Makefile", "pyproject.toml",
               "requirements.lock", "requirements-llm.lock", "docs/LLM_PREREGISTRATION.md"],
    "lean": ["lean/VeriSwarm/*.lean", "lean/*.lean", "lean/lakefile.toml", "lean/lean-toolchain"],
    "tlc_logs": ["artifacts/tlc/*.log", "artifacts/tlc/timed_out/*.log", "artifacts/tlc/sweep/*.log"],
    "traces": ["artifacts/traces/*.json"],
    "raw": ["artifacts/raw/*", "artifacts/raw/llm/*.json", "artifacts/raw/llm_smoke/*.json",
            "artifacts/raw/llm_pairs/*.json", "artifacts/raw/llm_pairs_smoke/*.json",
            "artifacts/raw/llm_pairs_qwen/*.json", "artifacts/raw/llm_pairs_qwen_smoke/*.json",
            "artifacts/raw/llm_pairs_llama/*.json", "artifacts/raw/llm_pairs_llama_smoke/*.json",
            "artifacts/raw/llm_pairs_grid/*.json", "artifacts/raw/llm_pairs_grid_smoke/*.json"],
    "summary": ["artifacts/summary/*.json"],
    "figures": ["artifacts/figures/*.pdf", "artifacts/figures/*.png"],
    "report": ["report/report.tex", "report/references.bib", "report/report.pdf", "report/generated/*.tex", "submission/*",
               "docs/explainer/*.html"],
}


def version(cmd: list[str], cwd: Path = ROOT) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120, cwd=cwd)
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unavailable: {exc}"
    text = (out.stdout or out.stderr).strip()
    return text.splitlines()[0] if text else "unknown"


def installed(package: str) -> str:
    from importlib import metadata

    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return "not installed"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    groups = {}
    for group, patterns in HASHED.items():
        files = {}
        for pattern in patterns:
            for path in sorted(ROOT.glob(pattern)):
                if path.is_file():
                    files[path.relative_to(ROOT).as_posix()] = sha256(path)
        groups[group] = files

    tla_lock = json.loads((ROOT / "tools" / "tla2tools.lock.json").read_text(encoding="utf-8"))
    jar = ROOT / "tools" / "tla2tools.jar"
    manifest = {
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "java": version(["java", "-version"]),
        # Run inside lean/ so elan uses the pinned toolchain, not its global default.
        "lean": version(["lean", "--version"], cwd=ROOT / "lean"),
        "tectonic": version(["tectonic", "--version"]),
        "anthropic_sdk": installed("anthropic"),
        "huggingface_hub": installed("huggingface_hub"),
        "tla2tools": {**tla_lock, "jar_present": jar.exists(), "jar_sha256_verified": jar.exists() and sha256(jar) == tla_lock["sha256"]},
        "file_counts": {k: len(v) for k, v in groups.items()},
        "files": groups,
    }
    (ROOT / "artifacts" / "MANIFEST.json").write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")
    print("wrote artifacts/MANIFEST.json:", manifest["file_counts"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
