from __future__ import annotations

import os
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    override = os.getenv("GAMEFUNDS_DATA_DIR", "").strip()
    if override:
        return Path(override)
    return PACKAGE_ROOT / "data"


def default_db_path() -> Path:
    override = os.getenv("GAMEFUNDS_DB_PATH", "").strip()
    if override:
        return Path(override)
    return data_dir() / "gamefunds.db"


def rubrics_path() -> Path:
    return data_dir() / "rubrics.json"


def guides_dir() -> Path:
    return data_dir() / "guides"


def help_dir() -> Path:
    return data_dir() / "help"


def tool_calls_log_path() -> Path:
    return data_dir() / "tool_calls.log"
