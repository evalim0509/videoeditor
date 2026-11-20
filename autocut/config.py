"""Paths and global settings.

All runtime data lives under AUTOCUT_HOME (default: <repo>/data). Model weights are kept there too
(Hugging Face / torch caches are redirected), so the app is self-contained and works offline once
the weights have been downloaded.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(__file__).resolve().parent
ASSETS_DIR = PACKAGE_DIR / "assets"
DEFAULT_CONFIG_DIR = PACKAGE_DIR / "config"

VIDEO_EXTS = {".mov", ".mp4", ".m4v", ".mkv", ".avi", ".webm", ".mts", ".m2ts", ".3gp", ".hevc"}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac", ".webm", ".caf"}

PROXY_SHORT_SIDE = 480        # "480p" proxy: short side 480 px
ANALYSIS_SAMPLE_RATE = 32000  # mono WAV extracted for analysis (PANNs native rate)


class Settings:
    def __init__(self, home: Path | None = None):
        self.home = Path(home or os.environ.get("AUTOCUT_HOME") or (REPO_ROOT / "data")).resolve()
        self.db_path = self.home / "autocut.db"
        self.proxies_dir = self.home / "proxies"
        self.models_dir = self.home / "models"
        self.projects_dir = self.home / "projects"
        self.presets_dir = self.home / "presets"
        self.config_dir = self.home / "config"  # user-editable YAML copies live here

    def ensure(self) -> "Settings":
        for d in (self.home, self.proxies_dir, self.models_dir, self.projects_dir,
                  self.presets_dir, self.config_dir):
            d.mkdir(parents=True, exist_ok=True)
        # Keep every downloaded model inside our data dir.
        os.environ.setdefault("HF_HOME", str(self.models_dir / "huggingface"))
        os.environ.setdefault("TORCH_HOME", str(self.models_dir / "torch"))
        return self

    def proxy_dir_for(self, file_hash: str) -> Path:
        return self.proxies_dir / file_hash[:16]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings().ensure()


def reset_settings(home: Path | None = None) -> Settings:
    """Used by tests to point everything at a temporary directory."""
    get_settings.cache_clear()
    if home is not None:
        os.environ["AUTOCUT_HOME"] = str(home)
    return get_settings()
