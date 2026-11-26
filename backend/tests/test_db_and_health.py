from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import Database


def test_health_endpoint(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["app"] == "uhf-scheduler"
    assert body["version"]
    assert body["build_timestamp"]


def test_spa_index_served(client: TestClient) -> None:
    resp = client.get("/")
    assert resp.status_code == 200
    assert "UHF Scheduler" in resp.text


def test_sqlite_schema_and_indexes(temp_db: Database) -> None:
    with temp_db.session() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
        assert journal_mode.lower() == "wal"

        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        expected_tables = {
            "app_settings",
            "iptv_sources",
            "categories",
            "channels",
            "epg_sources",
            "epg_channels",
            "epg_programs",
        }
        assert expected_tables.issubset(tables)

        indexes = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "idx_channels_name_lower" in indexes
        assert "idx_epg_programs_chan_time" in indexes
        assert "idx_epg_programs_end_ts" in indexes
