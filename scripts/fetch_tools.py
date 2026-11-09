"""Fetches the pinned TLA+ tools release and verifies its hash.

Usage: python scripts/fetch_tools.py
Refuses to continue if the downloaded file does not match the recorded SHA-256.
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "tools" / "tla2tools.lock.json"
JAR = ROOT / "tools" / "tla2tools.jar"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    if JAR.exists() and sha256(JAR) == lock["sha256"]:
        print(f"tla2tools.jar {lock['version']} present, hash verified")
        return 0

    print(f"downloading {lock['url']}")
    tmp = JAR.with_suffix(".part")
    urllib.request.urlretrieve(lock["url"], tmp)
    digest = sha256(tmp)
    if digest != lock["sha256"]:
        tmp.unlink()
        print(f"hash mismatch: expected {lock['sha256']}, got {digest}", file=sys.stderr)
        return 1
    tmp.replace(JAR)
    print(f"tla2tools.jar {lock['version']} installed, hash verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
