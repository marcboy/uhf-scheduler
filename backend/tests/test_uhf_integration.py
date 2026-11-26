from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.db import Database


UHF_BASE = "http://uhf.local:8085"


def _seed_channel(temp_db: Database, playlist_id: str | None = "PL-UUID-123") -> int:
    with temp_db.session() as conn:
        sid = conn.execute(
            """
            INSERT INTO iptv_sources (name, type, url, username, password, playlist_id)
            VALUES ('Main', 'xtream', 'http://provider.example.com', 'u1', 'p1', ?)
            """,
            (playlist_id,),
        ).lastrowid
        cid = conn.execute(
            "INSERT INTO categories (source_id, external_id, name) VALUES (?, '1', '|FR| CINEMA')",
            (sid,),
        ).lastrowid
        ch_id = conn.execute(
            """
            INSERT INTO channels
                (source_id, category_id, external_id, name, name_lower, stream_url, logo_url, epg_channel_id, epg_channel_id_lower)
            VALUES (?, ?, '247', 'Canal+ HD', 'canal+ hd', 'http://provider.example.com/live/u1/p1/247.ts', 'http://img/247.png', 'canalplus.fr', 'canalplus.fr')
            """,
            (sid, cid),
        ).lastrowid
    return ch_id


@respx.mock
def test_settings_redaction_and_passwordless_test(client: TestClient) -> None:
    respx.get(f"{UHF_BASE}/auth/methods").mock(
        return_value=httpx.Response(200, json={"uhf_account": True, "server_password": True, "password_required": False})
    )
    respx.post(f"{UHF_BASE}/auth/password-login").mock(
        return_value=httpx.Response(200, json={"id_token": "tok-123", "refresh_token": "", "expires_in": 2592000})
    )
    respx.get(f"{UHF_BASE}/dvr/recordings").mock(
        return_value=httpx.Response(200, json=[])
    )

    put_resp = client.put(
        "/api/settings",
        json={"uhf_server": {"url": UHF_BASE, "password": "secret-password"}},
    )
    assert put_resp.status_code == 200
    body = put_resp.json()
    assert body["uhf_server"]["url"] == UHF_BASE
    assert body["uhf_server"]["password_configured"] is True
    assert "password" not in body["uhf_server"]
    assert "secret-password" not in put_resp.text

    test_resp = client.post("/api/settings/test-uhf", json={})
    assert test_resp.status_code == 200
    assert test_resp.json()["ok"] is True


@respx.mock
def test_password_protected_login_and_401_reauth(client: TestClient, temp_db: Database) -> None:
    temp_db.set_setting(
        "uhf_server",
        {"url": UHF_BASE, "password": "good-pw", "token": "expired-token", "device_id": "dev1"},
    )
    respx.get(f"{UHF_BASE}/auth/methods").mock(
        return_value=httpx.Response(200, json={"password_required": True})
    )
    respx.post(f"{UHF_BASE}/auth/password-login").mock(
        return_value=httpx.Response(200, json={"id_token": "fresh-token", "refresh_token": "", "expires_in": 2592000})
    )
    # First call fails with 401 (expired token), second succeeds with fresh token
    route = respx.get(f"{UHF_BASE}/dvr/recordings").mock(
        side_effect=[
            httpx.Response(401, json={"detail": "expired"}),
            httpx.Response(200, json=[{"id": "r1", "name": "Test", "status": "scheduled", "url": "http://x/1.ts"}]),
        ]
    )

    resp = client.get("/api/recordings")
    assert resp.status_code == 200
    assert len(resp.json()["items"]) == 1
    assert "url" not in resp.json()["items"][0]  # raw creds redacted
    assert route.call_count == 2
    assert temp_db.get_setting("uhf_server")["token"] == "fresh-token"


@respx.mock
def test_unreachable_uhf_server_returns_502(client: TestClient, temp_db: Database) -> None:
    temp_db.set_setting("uhf_server", {"url": UHF_BASE, "password": "", "token": None, "device_id": "d"})
    respx.get(f"{UHF_BASE}/auth/methods").mock(side_effect=httpx.ConnectError("refused"))

    resp = client.post("/api/settings/test-uhf", json={})
    assert resp.status_code == 502


@respx.mock
def test_detect_playlist_id(client: TestClient, temp_db: Database) -> None:
    temp_db.set_setting("uhf_server", {"url": UHF_BASE, "password": "", "token": "t1", "device_id": "d"})
    respx.get(f"{UHF_BASE}/dvr/recordings").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "id": "rec1",
                    "url": "http://provider.example.com/live/u/p/247.ts",
                    "metadata": {"playlistID": "EXAMPLE-PLAYLIST-UUID"},
                }
            ],
        )
    )

    resp = client.post("/api/settings/detect-playlist-id", json={"host": "http://provider.example.com"})
    assert resp.status_code == 200
    assert resp.json()["detected"] == {"provider.example.com": ["EXAMPLE-PLAYLIST-UUID"]}


@respx.mock
def test_schedule_metadata_cancel_and_edit_via_replace(client: TestClient, temp_db: Database) -> None:
    temp_db.set_setting("uhf_server", {"url": UHF_BASE, "password": "", "token": "t1", "device_id": "d"})
    ch_id = _seed_channel(temp_db, playlist_id="EXAMPLE-PLAYLIST-UUID")

    created_payloads: list[dict] = []

    def _capture_create(request: httpx.Request) -> httpx.Response:
        import json

        body = json.loads(request.content.decode())
        created_payloads.append(body)
        return httpx.Response(201, json={"id": f"rec-{len(created_payloads)}", "status": "scheduled", **body})

    respx.post(f"{UHF_BASE}/dvr/recordings").mock(side_effect=_capture_create)

    # 1. Create far-future recurring recording
    create_resp = client.post(
        "/api/recordings",
        json={
            "channel_id": ch_id,
            "name": "Sunday Film",
            "start_time": "2030-05-05T20:00:00Z",
            "duration_seconds": 5400,
            "pre_padding_min": 2,
            "post_padding_min": 5,
            "recurrence_days": [6],
            "timezone": "America/Los_Angeles",
        },
    )
    assert create_resp.status_code == 201
    p = created_payloads[0]
    assert p["description"] == "Canal+ HD"
    assert p["metadata"]["playlistID"] == "EXAMPLE-PLAYLIST-UUID"
    assert p["metadata"]["categoryName"] == "|FR| CINEMA"
    assert p["metadata"]["thumbnailURL"] == "http://img/247.png"
    assert p["duration_seconds"] == 5400 + 420
    assert p["recurrence_days"] == [6]
    assert p["timezone"] == "America/Los_Angeles"

    # 2. Edit scheduled recurring recording -> creates replacement first, then cancel-recurrence on old
    respx.get(f"{UHF_BASE}/dvr/recordings/rec-1").mock(
        return_value=httpx.Response(200, json={"id": "rec-1", "status": "scheduled", **p})
    )
    cancel_rec_route = respx.patch(f"{UHF_BASE}/dvr/recordings/rec-1/cancel-recurrence").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    edit_resp = client.put(
        "/api/recordings/rec-1",
        json={
            "name": "Sunday Film Updated",
            "start_time": "2030-05-05T21:00:00Z",
            "duration_seconds": 3600,
            "recurrence_days": [6],
            "timezone": "America/Los_Angeles",
        },
    )
    assert edit_resp.status_code == 200
    assert edit_resp.json()["id"] == "rec-2"
    assert cancel_rec_route.called

    # 3. Edit blocked when recording already started
    respx.get(f"{UHF_BASE}/dvr/recordings/rec-live").mock(
        return_value=httpx.Response(200, json={"id": "rec-live", "status": "recording", **p})
    )
    blocked = client.put(
        "/api/recordings/rec-live",
        json={"start_time": "2030-05-05T21:00:00Z", "duration_seconds": 3600},
    )
    assert blocked.status_code == 409

    # 4. Stop in-progress calls /cancel
    cancel_live_route = respx.patch(f"{UHF_BASE}/dvr/recordings/rec-live/cancel").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    stop_resp = client.post("/api/recordings/rec-live/stop")
    assert stop_resp.status_code == 200
    assert stop_resp.json()["action"] == "cancelled_in_progress"
    assert cancel_live_route.called

    # 5. Delete one-off scheduled calls DELETE
    respx.get(f"{UHF_BASE}/dvr/recordings/rec-oneoff").mock(
        return_value=httpx.Response(200, json={"id": "rec-oneoff", "status": "scheduled", "recurrence_days": None})
    )
    del_route = respx.delete(f"{UHF_BASE}/dvr/recordings/rec-oneoff").mock(
        return_value=httpx.Response(200, json={"ok": True})
    )
    del_resp = client.delete("/api/recordings/rec-oneoff")
    assert del_resp.status_code == 200
    assert del_route.called
