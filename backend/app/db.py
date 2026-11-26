from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import stat
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .config import get_db_path

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS iptv_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('xtream', 'm3u')),
    url TEXT NOT NULL,
    username TEXT,
    password TEXT,
    playlist_id TEXT,
    auto_epg_enabled INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    max_streams INTEGER,
    last_synced_at TEXT,
    last_error TEXT,
    channel_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES iptv_sources(id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    name TEXT NOT NULL,
    hidden INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_categories_source_hidden
    ON categories(source_id, hidden);

CREATE TABLE IF NOT EXISTS channels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES iptv_sources(id) ON DELETE CASCADE,
    category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
    external_id TEXT NOT NULL,
    name TEXT NOT NULL,
    name_lower TEXT NOT NULL,
    stream_url TEXT NOT NULL,
    logo_url TEXT,
    epg_channel_id TEXT,
    epg_channel_id_lower TEXT,
    epg_override_id TEXT,
    is_favorite INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_channels_source_cat
    ON channels(source_id, category_id);
CREATE INDEX IF NOT EXISTS idx_channels_favorite
    ON channels(is_favorite);
CREATE INDEX IF NOT EXISTS idx_channels_name_lower
    ON channels(name_lower);
CREATE INDEX IF NOT EXISTS idx_channels_epg_lower
    ON channels(epg_channel_id_lower);

CREATE TABLE IF NOT EXISTS epg_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('xtream_auto', 'xmltv_url')),
    iptv_source_id INTEGER REFERENCES iptv_sources(id) ON DELETE CASCADE,
    url TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_synced_at TEXT,
    last_error TEXT,
    program_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_epg_sources_xtream_unique
    ON epg_sources(iptv_source_id) WHERE type = 'xtream_auto' AND iptv_source_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS epg_channels (
    epg_source_id INTEGER NOT NULL REFERENCES epg_sources(id) ON DELETE CASCADE,
    xmltv_id TEXT NOT NULL,
    xmltv_id_lower TEXT NOT NULL,
    display_name TEXT NOT NULL,
    display_name_lower TEXT NOT NULL,
    icon_url TEXT,
    PRIMARY KEY (epg_source_id, xmltv_id)
);

CREATE INDEX IF NOT EXISTS idx_epg_channels_lower
    ON epg_channels(xmltv_id_lower);
CREATE INDEX IF NOT EXISTS idx_epg_channels_name
    ON epg_channels(display_name_lower);

CREATE TABLE IF NOT EXISTS epg_programs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    epg_source_id INTEGER NOT NULL REFERENCES epg_sources(id) ON DELETE CASCADE,
    xmltv_id_lower TEXT NOT NULL,
    start_ts INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    category TEXT,
    icon_url TEXT
);

CREATE INDEX IF NOT EXISTS idx_epg_programs_chan_time
    ON epg_programs(xmltv_id_lower, start_ts, end_ts);
CREATE INDEX IF NOT EXISTS idx_epg_programs_end_ts
    ON epg_programs(end_ts);
CREATE INDEX IF NOT EXISTS idx_epg_programs_source
    ON epg_programs(epg_source_id);

CREATE TABLE IF NOT EXISTS auto_record_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    title_pattern TEXT NOT NULL,
    match_mode TEXT NOT NULL DEFAULT 'starts_with' CHECK (match_mode IN ('starts_with', 'contains', 'exact')),
    pre_padding_min INTEGER,
    post_padding_min INTEGER,
    skip_if_conflict INTEGER NOT NULL DEFAULT 1,
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    last_scheduled_count INTEGER NOT NULL DEFAULT 0,
    last_skipped_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_auto_record_rules_enabled
    ON auto_record_rules(enabled, channel_id);

CREATE TABLE IF NOT EXISTS auto_record_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL REFERENCES auto_record_rules(id) ON DELETE CASCADE,
    channel_id INTEGER NOT NULL,
    program_title TEXT NOT NULL,
    program_start_ts INTEGER NOT NULL,
    program_end_ts INTEGER NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('scheduled', 'skipped_conflict', 'error')),
    uhf_recording_id TEXT,
    reason TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_auto_record_history_rule_start
    ON auto_record_history(rule_id, program_start_ts);
CREATE INDEX IF NOT EXISTS idx_auto_record_history_created
    ON auto_record_history(created_at DESC);
"""

DEFAULT_SETTINGS: dict[str, Any] = {
    "uhf_server": {
        "url": "http://127.0.0.1:8085",
        "password": "",
        "token": None,
        "device_id": "uhf-scheduler-web",
    },
    "defaults": {
        "pre_padding_min": 2,
        "post_padding_min": 5,
        "max_simultaneous_streams": 1,
        "sync_interval_hours": 12,
        "epg_retention_hours": 24,
    },
}


class Database:
    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or get_db_path()
        self.write_lock = asyncio.Lock()

    def connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(self.db_path), timeout=15.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=10000;")
        return conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.session() as conn:
            conn.executescript(SCHEMA_SQL)
            for key, default_val in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO app_settings (key, value_json) VALUES (?, ?)",
                    (key, json.dumps(default_val)),
                )
        # Restrict DB permissions to owner read/write on POSIX
        if os.name == "posix" and self.db_path.exists():
            try:
                os.chmod(self.db_path, stat.S_IRUSR | stat.S_IWUSR)
            except OSError:
                pass

    def get_setting(self, key: str) -> dict[str, Any]:
        with self.session() as conn:
            row = conn.execute(
                "SELECT value_json FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        base = dict(DEFAULT_SETTINGS.get(key, {}))
        if row:
            try:
                stored = json.loads(row["value_json"])
                if isinstance(stored, dict):
                    base.update(stored)
            except json.JSONDecodeError:
                pass
        return base

    def set_setting(self, key: str, value: dict[str, Any]) -> None:
        with self.session() as conn:
            conn.execute(
                """
                INSERT INTO app_settings (key, value_json) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json
                """,
                (key, json.dumps(value)),
            )


_db_instance: Database | None = None


def get_db() -> Database:
    global _db_instance
    if _db_instance is None:
        _db_instance = Database()
        _db_instance.init_schema()
    return _db_instance


def reset_db_instance(db: Database | None = None) -> None:
    global _db_instance
    _db_instance = db
