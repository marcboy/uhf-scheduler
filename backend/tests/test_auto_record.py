from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.auto_record_service import evaluate_rules, is_sync_due, normalize_title, title_matches
from app.db import Database
from app.main import run_due_syncs_and_rules

UHF_BASE = "http://uhf.local:8085"


def _seed_fr_env(temp_db: Database) -> tuple[int, int, int]:
    """Seed M6 + TF1 channels and custom XMLTV EPG source; return (m6_ch_id, tf1_ch_id, epg_src_id)."""
    temp_db.set_setting("uhf_server", {"url": UHF_BASE, "password": "", "token": "t1", "device_id": "d"})
    with temp_db.session() as conn:
        sid = conn.execute(
            """
            INSERT INTO iptv_sources (name, type, url, username, password, playlist_id)
            VALUES ('Main', 'xtream', 'http://iptv.local', 'u', 'p', 'PL-1')
            """
        ).lastrowid
        cid = conn.execute(
            "INSERT INTO categories (source_id, external_id, name) VALUES (?, '1', '|FR| TNT')",
            (sid,),
        ).lastrowid
        m6_id = conn.execute(
            """
            INSERT INTO channels
                (source_id, category_id, external_id, name, name_lower, stream_url, epg_channel_id, epg_channel_id_lower)
            VALUES (?, ?, '6', '|FR| M6 UHD', '|fr| m6 uhd', 'http://iptv.local/live/u/p/6.ts', 'm6.fr', 'm6.fr')
            """,
            (sid, cid),
        ).lastrowid
        tf1_id = conn.execute(
            """
            INSERT INTO channels
                (source_id, category_id, external_id, name, name_lower, stream_url, epg_channel_id, epg_channel_id_lower)
            VALUES (?, ?, '1', '|FR| TF1 UHD', '|fr| tf1 uhd', 'http://iptv.local/live/u/p/1.ts', 'tf1.fr', 'tf1.fr')
            """,
            (sid, cid),
        ).lastrowid
        epg_id = conn.execute(
            "INSERT INTO epg_sources (name, type, url, enabled) VALUES ('xmltvfr', 'xmltv_url', 'http://epg.local/fr.xml.gz', 1)"
        ).lastrowid
    return m6_id, tf1_id, epg_id


def test_normalize_and_title_match_modes() -> None:
    assert normalize_title("Enquête exclusive : Spéciale") == "enquete exclusive : speciale"
    assert title_matches("Enquête exclusive", "enquete exclusive", "starts_with") is True
    assert title_matches("90' Enquêtes S16", "90' enquetes", "starts_with") is True
    assert title_matches("Capital Santé - S01E08", "Capital", "exact") is False
    assert title_matches("Capital", "capital", "exact") is True
    assert title_matches("Le grand Capital", "capital", "contains") is True
    assert title_matches("Le grand Capital", "capital", "starts_with") is False


def test_is_sync_due_catchup() -> None:
    now = datetime(2026, 11, 25, 22, 0, 0, tzinfo=timezone.utc)
    assert is_sync_due(None, 12, now=now) is True
    assert is_sync_due((now - timedelta(hours=13)).isoformat(), 12, now=now) is True
    assert is_sync_due((now - timedelta(hours=6)).isoformat(), 12, now=now) is False


@respx.mock
def test_back_to_back_padding_trim_conflict_skip_and_idempotency(
    client: TestClient, temp_db: Database
) -> None:
    m6_id, tf1_id, epg_id = _seed_fr_env(temp_db)
    base = datetime(2030, 5, 5, 20, 0, 0, tzinfo=timezone.utc)
    ep1_s = int(base.timestamp())
    ep1_e = int((base + timedelta(minutes=75)).timestamp())  # 20:00 -> 21:15
    ep2_s = ep1_e
    ep2_e = int((base + timedelta(minutes=150)).timestamp())  # 21:15 -> 22:30 (back-to-back!)
    # Another day overlapping an existing TF1 timer
    ep3_s = int((base + timedelta(days=1)).timestamp())       # +1d 20:00 -> 21:15
    ep3_e = int((base + timedelta(days=1, minutes=75)).timestamp())

    with temp_db.session() as conn:
        conn.executemany(
            "INSERT INTO epg_programs (epg_source_id, xmltv_id_lower, start_ts, end_ts, title) VALUES (?, ?, ?, ?, ?)",
            [
                (epg_id, "m6.fr", ep1_s, ep1_e, "Enquête exclusive"),
                (epg_id, "m6.fr", ep2_s, ep2_e, "Enquête exclusive : Suite"),
                (epg_id, "m6.fr", ep3_s, ep3_e, "Enquête exclusive"),
                (epg_id, "tf1.fr", ep1_s, ep1_e, "Enquête exclusive"),  # wrong channel -> ignored
            ],
        )

    # Pre-existing uhf-server recordings:
    # 1. Ends exactly at ep1_s (tests pre-padding edge trim without skipping!)
    # 2. Overlaps ep3 core broadcast (tests conflict skip with max_streams=1)
    uhf_store: list[dict] = [
        {
            "id": "pre-edge",
            "name": "Before Show",
            "description": "|FR| TF1 UHD",
            "url": "http://iptv.local/live/u/p/1.ts",
            "status": "scheduled",
            "start_time": (base - timedelta(hours=1)).isoformat(),
            "duration_seconds": 3600,  # ends right at 20:00
        },
        {
            "id": "conflict-1",
            "name": "Big Match",
            "description": "|FR| TF1 UHD",
            "url": "http://iptv.local/live/u/p/1.ts",
            "status": "scheduled",
            "start_time": (base + timedelta(days=1, minutes=30)).isoformat(),
            "duration_seconds": 5400,  # +1d 20:30 -> 22:00 (overlaps ep3 core)
        },
    ]

    respx.get(f"{UHF_BASE}/dvr/recordings").mock(
        side_effect=lambda _req: httpx.Response(200, json=uhf_store)
    )

    def _create_rec(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode())
        rec = {"id": f"auto-{len(uhf_store)+1}", "status": "scheduled", **body}
        uhf_store.append(rec)
        return httpx.Response(201, json=rec)

    create_route = respx.post(f"{UHF_BASE}/dvr/recordings").mock(side_effect=_create_rec)

    # Create rule with default skip_if_conflict=True and 2m pre / 5m post padding
    resp = client.post(
        "/api/rules",
        json={
            "channel_id": m6_id,
            "title_pattern": "enquete exclusive",
            "match_mode": "starts_with",
            "skip_if_conflict": True,
            "run_now": True,
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    rule_id = data["id"]
    assert data["run_summary"]["scheduled"] == 2
    assert data["run_summary"]["skipped_conflict"] == 1
    assert create_route.call_count == 2

    # Verify Option A padding trimming:
    # Ep 1: pre-pad trimmed to 20:00 (touching pre-edge end), post-pad trimmed to 21:15 (back-to-back with Ep 2)
    auto_recs = [r for r in uhf_store if r["id"].startswith("auto-")]
    assert auto_recs[0]["start_time"] == base.isoformat()
    assert auto_recs[0]["duration_seconds"] == 75 * 60
    # Ep 2: pre-pad trimmed to 21:15, post-pad kept (+5m -> 80m total)
    assert auto_recs[1]["start_time"] == (base + timedelta(minutes=75)).isoformat()
    assert auto_recs[1]["duration_seconds"] == 80 * 60

    # Simulate user manually deleting Ep 1 from uhf-server and re-running -> MUST NOT re-schedule!
    uhf_store[:] = [r for r in uhf_store if r["id"] != auto_recs[0]["id"]]
    run2 = client.post(f"/api/rules/{rule_id}/run").json()
    assert run2["scheduled"] == 0
    assert run2["skipped_conflict"] == 1  # ep3 still conflicts

    # Force schedule the skipped conflict entry
    hist = client.get("/api/rules/history", params={"rule_id": rule_id}).json()
    skipped = next(h for h in hist if h["status"] == "skipped_conflict")
    assert "Big Match" in (skipped["reason"] or "")

    force_resp = client.post(f"/api/rules/history/{skipped['id']}/force-schedule")
    assert force_resp.status_code == 200
    assert force_resp.json()["ok"] is True
    assert create_route.call_count == 3


@respx.mock
def test_stream_limit_2_and_unchecked_skip_conflict_allow_overlap(
    client: TestClient, temp_db: Database
) -> None:
    m6_id, _tf1_id, epg_id = _seed_fr_env(temp_db)
    base = datetime(2030, 6, 1, 20, 0, 0, tzinfo=timezone.utc)
    with temp_db.session() as conn:
        conn.execute(
            "INSERT INTO epg_programs (epg_source_id, xmltv_id_lower, start_ts, end_ts, title) VALUES (?, 'm6.fr', ?, ?, 'Capital')",
            (epg_id, int(base.timestamp()), int((base + timedelta(hours=1)).timestamp())),
        )

    uhf_store = [
        {
            "id": "existing-1",
            "name": "Other Show",
            "url": "http://iptv.local/live/u/p/1.ts",
            "status": "scheduled",
            "start_time": base.isoformat(),
            "duration_seconds": 3600,
        }
    ]
    respx.get(f"{UHF_BASE}/dvr/recordings").mock(
        side_effect=lambda _req: httpx.Response(200, json=uhf_store)
    )
    respx.post(f"{UHF_BASE}/dvr/recordings").mock(
        side_effect=lambda req: httpx.Response(201, json={"id": "new-1", "status": "scheduled", **json.loads(req.content.decode())})
    )

    # Raise max_simultaneous_streams to 2 -> skip_if_conflict=True now allows 2 concurrent streams
    client.put("/api/settings", json={"defaults": {"max_simultaneous_streams": 2}})
    r1 = client.post(
        "/api/rules",
        json={"channel_id": m6_id, "title_pattern": "Capital", "match_mode": "exact", "skip_if_conflict": True},
    ).json()
    assert r1["run_summary"]["scheduled"] == 1
    assert r1["run_summary"]["skipped_conflict"] == 0

    # Delete rule, lower max_simultaneous_streams back to 1, uncheck skip_if_conflict -> still schedules!
    client.delete(f"/api/rules/{r1['id']}")
    client.put("/api/settings", json={"defaults": {"max_simultaneous_streams": 1}})
    r2 = client.post(
        "/api/rules",
        json={"channel_id": m6_id, "title_pattern": "Capital", "match_mode": "exact", "skip_if_conflict": False},
    ).json()
    assert r2["run_summary"]["scheduled"] == 1


@respx.mock
@pytest.mark.asyncio
async def test_manual_epg_sync_and_due_sync_trigger_rules(
    client: TestClient, temp_db: Database
) -> None:
    m6_id, _tf1_id, epg_id = _seed_fr_env(temp_db)
    # Create rule before EPG has programs (run_now=False)
    rule_resp = client.post(
        "/api/rules",
        json={"channel_id": m6_id, "title_pattern": "Capital", "run_now": False},
    )
    assert rule_resp.status_code == 201

    future = datetime.now(timezone.utc) + timedelta(days=2)
    xml_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="m6.fr"><display-name>M6</display-name></channel>
  <programme start="{future.strftime('%Y%m%d%H%M%S +0000')}" stop="{(future + timedelta(hours=1)).strftime('%Y%m%d%H%M%S +0000')}" channel="m6.fr">
    <title>Capital</title>
  </programme>
</tv>
""".encode("utf-8")

    respx.get("http://epg.local/fr.xml.gz").mock(return_value=httpx.Response(200, content=xml_body))
    respx.get(f"{UHF_BASE}/dvr/recordings").mock(return_value=httpx.Response(200, json=[]))
    post_route = respx.post(f"{UHF_BASE}/dvr/recordings").mock(
        side_effect=lambda req: httpx.Response(201, json={"id": "from-sync", "status": "scheduled", **json.loads(req.content.decode())})
    )

    # Trigger manual EPG sync -> automatically runs rules!
    sync_resp = client.post(f"/api/sources/epg/{epg_id}/sync").json()
    assert sync_resp["ok"] is True
    assert sync_resp["auto_record"]["scheduled"] == 1
    assert post_route.call_count == 1

    # Also verify run_due_syncs_and_rules skips freshly synced EPG and catches up when stale
    res_fresh = await run_due_syncs_and_rules(temp_db)
    assert res_fresh["epg_synced"] == 0


def test_per_channel_program_search_accent_insensitive(
    client: TestClient, temp_db: Database
) -> None:
    m6_id, tf1_id, epg_id = _seed_fr_env(temp_db)
    future = datetime.now(timezone.utc) + timedelta(days=2)
    t1 = int(future.timestamp())
    t2 = int((future + timedelta(hours=1)).timestamp())

    with temp_db.session() as conn:
        conn.executemany(
            "INSERT INTO epg_programs (epg_source_id, xmltv_id_lower, start_ts, end_ts, title) VALUES (?, ?, ?, ?, ?)",
            [
                (epg_id, "m6.fr", t1, t2, "Enquête exclusive"),
                (epg_id, "tf1.fr", t1, t2, "Enquête exclusive"),
            ],
        )

    # Scoped strictly to M6 channel -> returns only M6 airing, accent-insensitively
    res = client.get("/api/epg/search", params={"channel_id": m6_id, "q": "enquete"})
    assert res.status_code == 200
    body = res.json()
    assert body["count"] == 1
    assert body["items"][0]["channel_id"] == m6_id
    assert body["items"][0]["title"] == "Enquête exclusive"


@respx.mock
def test_discover_uhf_servers_endpoint(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import uhf_client

    monkeypatch.setattr(
        uhf_client,
        "_browse_mdns_sync",
        lambda timeout=2.0: [
            {"url": "http://192.168.1.50:8085", "name": "dvrbox.local", "host": "192.168.1.50", "port": 8085, "source": "mdns"}
        ],
    )
    respx.get("http://192.168.1.50:8085/auth/methods").mock(
        return_value=httpx.Response(200, json={"uhf_account": True, "server_password": True, "password_required": False})
    )
    respx.get("http://127.0.0.1:8085/auth/methods").mock(side_effect=httpx.ConnectError("no"))
    respx.get("http://127.0.0.1:8000/auth/methods").mock(side_effect=httpx.ConnectError("no"))
    respx.get("http://host.docker.internal:8085/auth/methods").mock(side_effect=httpx.ConnectError("no"))
    respx.get("http://host.docker.internal:8000/auth/methods").mock(side_effect=httpx.ConnectError("no"))

    resp = client.post("/api/settings/discover-uhf")
    assert resp.status_code == 200
    servers = resp.json()["servers"]
    assert len(servers) == 1
    assert servers[0]["url"] == "http://192.168.1.50:8085"
    assert servers[0]["source"] == "mdns"
    assert servers[0]["password_required"] is False
