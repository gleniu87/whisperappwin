"""Filesystem locations for user-owned state.

Everything mutable lives under %APPDATA%\\WhisperDictateWin so the repo stays
clean and the app survives being moved or reinstalled.
"""

from __future__ import annotations

import os
from pathlib import Path

DATA_DIR_NAME = "WhisperDictateWin"


def data_dir() -> Path:
    """Per-user data directory, created on first access."""
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Roaming"
    path = root / DATA_DIR_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return data_dir() / "config.toml"


def history_path() -> Path:
    return data_dir() / "history.jsonl"


def log_path() -> Path:
    return data_dir() / "whisperdictate.log"


def model_cache_dir() -> Path:
    """Where CT2 model snapshots are downloaded.

    Kept out of the roaming profile: a turbo model is ~1.6 GB and roaming
    profiles get synced on domain-joined machines.
    """
    base = os.environ.get("LOCALAPPDATA")
    root = Path(base) if base else Path.home() / "AppData" / "Local"
    path = root / DATA_DIR_NAME / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path
