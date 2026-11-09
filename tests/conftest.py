import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def pytest_collection_modifyitems(config, items):
    jar = ROOT / "tools" / "tla2tools.jar"
    if shutil.which("java") and jar.exists():
        return
    skip = pytest.mark.skip(reason="needs Java and tools/tla2tools.jar; run scripts/fetch_tools.py")
    for item in items:
        if "tlc" in item.keywords:
            item.add_marker(skip)
