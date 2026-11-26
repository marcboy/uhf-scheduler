from __future__ import annotations

import gzip
import io
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.db import Database
from app.epg_sync import ingest_xmltv_stream, parse_xmltv_timestamp
from app.scheduler_service import compute_schedule_window, detect_overlaps, validate_recurrence


def test_xmltv_timestamp_parsing() -> None:
    ts_utc = parse_xmltv_timestamp("20261125200000 +0000")
    ts_plus1 = parse_xmltv_timestamp("20261125210000 +0100")
    assert ts_utc == ts_plus1
    assert parse_xmltv_timestamp("invalid") is None


def test_streaming_xmltv_ingest_and_retention_pruning(temp_db: Database) -> None:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    future_start = (now + timedelta(hours=2)).strftime("%Y%m%d%H%M%S +0000")
    future_end = (now + timedelta(hours=3)).strftime("%Y%m%d%H%M%S +0000")
    old_start = (now - timedelta(hours=72)).strftime("%Y%m%d%H%M%S +0000")
    old_end = (now - timedelta(hours=71)).strftime("%Y%m%d%H%M%S +0000")

    xml_bytes = f"""<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="CanalPlus.fr">
    <display-name>Canal+ France</display-name>
  </channel>
  <programme start="{old_start}" stop="{old_end}" channel="CanalPlus.fr">
    <title>Expired Show</title>
  </programme>
  <programme start="{future_start}" stop="{future_end}" channel="CanalPlus.fr">
    <title>Upcoming Movie</title>
    <desc>A great movie</desc>
    <category>Cinema</category>
  </programme>
</tv>
""".encode("utf-8")

    with temp_db.session() as conn:
        epg_id = conn.execute(
            "INSERT INTO epg_sources (name, type, url) VALUES ('TestXML', 'xmltv_url', 'http://epg.local/guide.xml')"
        ).lastrowid

    ch_cnt, prog_cnt = ingest_xmltv_stream(temp_db, epg_id, io.BytesIO(xml_bytes), retention_hours=24)
    assert ch_cnt == 1
    assert prog_cnt == 1  # expired show pruned


@respx.mock
def test_epg_gzip_sync_override_and_grid_overlay(client: TestClient, temp_db: Database) -> None:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    p_start = now + timedelta(hours=1)
    p_end = now + timedelta(hours=2)

    # Seed IPTV source + channel without matching epg_channel_id initially
    with temp_db.session() as conn:
        sid = conn.execute(
            "INSERT INTO iptv_sources (name, type, url) VALUES ('M3U', 'm3u', 'http://x/list.m3u')"
        ).lastrowid
        cat_id = conn.execute(
            "INSERT INTO categories (source_id, external_id, name) VALUES (?, 'c1', 'Movies')", (sid,)
        ).lastrowid
        ch_id = conn.execute(
            """
            INSERT INTO channels (source_id, category_id, external_id, name, name_lower, stream_url, epg_channel_id, epg_channel_id_lower)
            VALUES (?, ?, '1', 'Film One', 'film one', 'http://stream/1.ts', 'wrong.id', 'wrong.id')
            """,
            (sid, cat_id),
        ).lastrowid

    # Check empty EPG mode first
    temp_db.set_setting("uhf_server", {"url": "http://uhf.local:8085", "password": "", "token": "t", "device_id": "d"})
    respx.get("http://uhf.local:8085/dvr/recordings").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "rec-ov-1",
                    "name": "Scheduled Movie",
                    "url": "http://stream/1.ts",
                    "status": "scheduled",
                    "start_time": p_start.isoformat(),
                    "duration_seconds": 3600,
                }
            ],
        )
    )
    empty_grid = client.get("/api/epg/grid").json()
    assert empty_grid["epg_source_count"] == 0

    # Add custom .xml.gz EPG source and sync
    xml_text = f"""<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="film1.ext"><display-name>Film One HD</display-name></channel>
  <programme start="{p_start.strftime('%Y%m%d%H%M%S +0000')}" stop="{p_end.strftime('%Y%m%d%H%M%S +0000')}" channel="film1.ext">
    <title>Space Odyssey</title>
  </programme>
</tv>
""".encode("utf-8")
    gz_payload = gzip.compress(xml_text)

    epg_src = client.post(
        "/api/sources/epg", json={"name": "External 7d", "url": "http://epg.local/7d.xml.gz"}
    ).json()
    respx.get("http://epg.local/7d.xml.gz").mock(return_value=httpx.Response(200, content=gz_payload))

    sync_res = client.post(f"/api/sources/epg/{epg_src['id']}/sync").json()
    assert sync_res["ok"] is True
    assert sync_res["program_count"] == 1

    # Assign manual EPG override
    assert client.patch(f"/api/channels/{ch_id}/epg-override", json={"epg_override_id": "film1.ext"}).status_code == 200

    # Query grid -> sees both the program and the overlaid scheduled recording
    grid = client.get(
        "/api/epg/grid",
        params={"category_id": str(cat_id), "start": now.isoformat(), "end": (now + timedelta(hours=6)).isoformat()},
    ).json()
    assert grid["epg_source_count"] == 1
    assert len(grid["channels"]) == 1
    row = grid["channels"][0]
    assert row["programs"][0]["title"] == "Space Odyssey"
    assert row["recordings"][0]["id"] == "rec-ov-1"


def test_scheduler_padding_midnight_recurrence_and_overlaps() -> None:
    fixed_now = datetime(2026, 11, 25, 22, 59, 0, tzinfo=timezone.utc)

    # 1. Midnight rollover via end_time (23:30 -> 01:00 next day = 90 min)
    start_dt, dur = compute_schedule_window(
        start_time_iso="2026-11-26T23:30:00Z",
        end_time_iso="2026-11-26T01:00:00Z",
        pre_padding_min=2,
        post_padding_min=5,
        now=fixed_now,
    )
    assert start_dt == datetime(2026, 11, 26, 23, 28, 0, tzinfo=timezone.utc)
    assert dur == (90 + 7) * 60

    # 2. Pre-padding clamped to now for starting-soon recording
    clamped_start, clamped_dur = compute_schedule_window(
        start_time_iso="2026-11-25T23:00:00Z",
        duration_seconds=3600,
        pre_padding_min=5,
        post_padding_min=5,
        now=fixed_now,
    )
    assert clamped_start == fixed_now
    assert clamped_dur == 60 + 3600 + 300

    # 3. Reject past end time
    with pytest.raises(ValueError, match="future"):
        compute_schedule_window(
            start_time_iso="2026-11-25T20:00:00Z",
            duration_seconds=1800,
            now=fixed_now,
        )

    # 4. Recurrence validation
    days, end_iso, tz = validate_recurrence([6, 0, 6], "2027-01-01T00:00:00Z", "America/Los_Angeles")
    assert days == [0, 6]
    assert tz == "America/Los_Angeles"
    assert end_iso is not None

    with pytest.raises(ValueError, match="timezone"):
        validate_recurrence([1], None, "Mars/Phobos")

    # 5. Overlap detection vs adjacent non-overlap
    existing = [
        {
            "id": "e1",
            "name": "Existing Show",
            "description": "Ch 1",
            "status": "scheduled",
            "start_time": "2026-11-26T20:00:00Z",
            "duration_seconds": 3600,
        }
    ]
    # Adjacent (starts exactly at 21:00) -> NO conflict
    adj = detect_overlaps(
        proposed_start=datetime(2026, 11, 26, 21, 0, 0, tzinfo=timezone.utc),
        proposed_duration=3600,
        existing_recordings=existing,
        max_simultaneous_streams=1,
    )
    assert adj["has_conflict"] is False
    assert adj["conflicts"] == []

    # Overlapping by 30m with max_streams=1 -> conflict
    ov = detect_overlaps(
        proposed_start=datetime(2026, 11, 26, 20, 30, 0, tzinfo=timezone.utc),
        proposed_duration=3600,
        existing_recordings=existing,
        max_simultaneous_streams=1,
    )
    assert ov["has_conflict"] is True
    assert ov["peak_concurrent"] == 2
    assert ov["conflicts"][0]["overlap_minutes"] == 30.0

    # Same overlap with max_streams=2 -> allowed (peak 2 <= 2)
    ov2 = detect_overlaps(
        proposed_start=datetime(2026, 11, 26, 20, 30, 0, tzinfo=timezone.utc),
        proposed_duration=3600,
        existing_recordings=existing,
        max_simultaneous_streams=2,
    )
    assert ov2["has_conflict"] is False


@respx.mock
def test_epg_grid_deduplicates_custom_over_provider_with_fallback(
    client: TestClient, temp_db: Database
) -> None:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    t1 = int((now + timedelta(hours=1)).timestamp())
    t2 = int((now + timedelta(hours=2)).timestamp())

    with temp_db.session() as conn:
        sid = conn.execute(
            "INSERT INTO iptv_sources (name, type, url, username, password) VALUES ('X', 'xtream', 'http://x', 'u', 'p')"
        ).lastrowid
        cat_id = conn.execute(
            "INSERT INTO categories (source_id, external_id, name) VALUES (?, 'c1', 'FR')", (sid,)
        ).lastrowid
        conn.execute(
            """
            INSERT INTO channels (source_id, category_id, external_id, name, name_lower, stream_url, epg_channel_id, epg_channel_id_lower)
            VALUES (?, ?, '1', 'TF1', 'tf1', 'http://x/1.ts', 'tf1.fr', 'tf1.fr'),
                   (?, ?, '1b', 'TF1 1', 'tf1 1', 'http://x/1b.ts', 'tf1.fr', 'tf1.fr'),
                   (?, ?, '2', 'Niche', 'niche', 'http://x/2.ts', 'niche.fr', 'niche.fr')
            """,
            (sid, cat_id, sid, cat_id, sid, cat_id),
        )
        auto_epg = conn.execute(
            "INSERT INTO epg_sources (name, type, iptv_source_id, enabled) VALUES ('Provider', 'xtream_auto', ?, 1)",
            (sid,),
        ).lastrowid
        custom_epg = conn.execute(
            "INSERT INTO epg_sources (name, type, url, enabled) VALUES ('xmltvfr', 'xmltv_url', 'http://xmltvfr/fr.xml.gz', 1)"
        ).lastrowid

        # Provider covers both TF1 (with a 1-min overlapping duplicate) and Niche; custom covers TF1
        # Include a 5-min short show (t1-300..t1) immediately followed by TF1 Show (t1..t2) to ensure
        # sequential short shows (like 5-min French weather/Canteloup) never hide the next program.
        conn.executemany(
            "INSERT INTO epg_programs (epg_source_id, xmltv_id_lower, start_ts, end_ts, title) VALUES (?, ?, ?, ?, ?)",
            [
                (auto_epg, "tf1.fr", t1, t2, "Provider TF1 Show"),
                (auto_epg, "tf1.fr", t1 + 60, t2, "Provider TF1 Dup"),
                (custom_epg, "tf1.fr", t1 - 300, t1, "Short 5m Show"),
                (custom_epg, "tf1.fr", t1, t2, "Custom TF1 Show"),
                (auto_epg, "niche.fr", t1, t2, "Provider Niche Show"),
                (auto_epg, "niche.fr", t1 + 60, t2, "Provider Niche Dup"),
            ],
        )

    temp_db.set_setting("uhf_server", {"url": "http://uhf.local:8085", "password": "", "token": "t", "device_id": "d"})
    respx.get("http://uhf.local:8085/dvr/recordings").mock(return_value=httpx.Response(200, json=[]))

    grid = client.get(
        "/api/epg/grid",
        params={"category_id": str(cat_id), "start": now.isoformat(), "end": (now + timedelta(hours=4)).isoformat()},
    ).json()
    by_name = {c["name"]: c["programs"] for c in grid["channels"]}
    # TF1 keeps both the 5-min short show AND the immediately following Custom TF1 Show (no provider dup)
    assert [p["title"] for p in by_name["TF1"]] == ["Short 5m Show", "Custom TF1 Show"]
    # Niche falls back to provider source and collapses the 1-min duplicate
    assert len(by_name["Niche"]) == 1
    assert by_name["Niche"][0]["title"] == "Provider Niche Show"

    # Program search groups mirror streams (TF1 + TF1 1) into 1 broadcast row and deduplicates multi-source
    sr = client.get("/api/epg/search", params={"q": "TF1 Show", "category_id": str(cat_id)})
    assert sr.status_code == 200
    body = sr.json()
    assert body["count"] == 1
    assert body["items"][0]["channel_name"] == "TF1"
    assert body["items"][0]["title"] == "Custom TF1 Show"
    assert len(body["items"][0]["channels"]) == 2
