import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    """Point AUTOCUT_HOME at a fresh temp dir (isolated SQLite + proxies)."""
    from autocut import config, db

    monkeypatch.setenv("AUTOCUT_HOME", str(tmp_path / "home"))
    db._initialised.clear()
    s = config.reset_settings()
    yield s
    config.get_settings.cache_clear()


TEST_FOOTAGE = ROOT / "test_footage"


def real_footage() -> list[Path]:
    from autocut.config import VIDEO_EXTS

    return sorted(p for p in TEST_FOOTAGE.glob("*") if p.suffix.lower() in VIDEO_EXTS)
