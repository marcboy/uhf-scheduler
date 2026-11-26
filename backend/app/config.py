from __future__ import annotations

import os
import sys
from pathlib import Path

from platformdirs import user_data_dir

APP_NAME = "uhf-scheduler"
APP_VERSION = "0.1.16"
BUILD_TIMESTAMP = "2026-11-25T16:27:00-08:00"


def get_data_dir() -> Path:
    """Resolve portable data directory (env override -> OS app data dir)."""
    env_dir = os.environ.get("UHF_SCHEDULER_DATA_DIR")
    if env_dir:
        path = Path(env_dir).expanduser().resolve()
    else:
        path = Path(user_data_dir(APP_NAME, appauthor=False))
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_db_path() -> Path:
    return get_data_dir() / "scheduler.db"


def get_frontend_dist_dir() -> Path:
    """Resolve compiled SPA directory in dev, Pi deploy, or frozen PyInstaller layout."""
    env_dist = os.environ.get("UHF_SCHEDULER_FRONTEND_DIR")
    if env_dist:
        return Path(env_dist).expanduser().resolve()
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "frontend_dist"
    # backend/app/config.py -> repo_root/frontend/dist
    return Path(__file__).resolve().parents[2] / "frontend" / "dist"


def get_port() -> int:
    try:
        return int(os.environ.get("PORT", os.environ.get("UHF_SCHEDULER_PORT", "8086")))
    except ValueError:
        return 8086


def get_host() -> str:
    return os.environ.get("HOST", os.environ.get("UHF_SCHEDULER_HOST", "0.0.0.0"))
