from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient

from app.iptv_sync import parse_m3u_content


def test_m3u_parser() -> None:
    sample = """#EXTM3U
#EXTINF:-1 tvg-id="tf1.fr" tvg-logo="http://logo/tf1.png" group-title="|FR| GENERAL",TF1 HD
http://streams.example/tf1.ts
#EXTINF:-1 tvg-id="arte.fr" group-title="|FR| CULTURE",Arte
http://streams.example/arte.m3u8
"""
    cats, chs = parse_m3u_content(sample)
    assert len(cats) == 2
    assert len(chs) == 2
    assert chs[0]["name"] == "TF1 HD"
    assert chs[0]["epg_channel_id"] == "tf1.fr"
    assert chs[0]["category_ext_id"] == "|FR| GENERAL"


@respx.mock
def test_xtream_sync_favorites_overrides_and_failure_preservation(client: TestClient) -> None:
    # Add Xtream source
    create_resp = client.post(
        "/api/sources/iptv",
        json={
            "name": "MyXtream",
            "type": "xtream",
            "url": "http://iptv.local",
            "username": "alice",
            "password": "super-secret-password",
            "auto_epg_enabled": True,
        },
    )
    assert create_resp.status_code == 201
    src = create_resp.json()
    sid = src["id"]
    assert src["password_configured"] is True
    assert "super-secret-password" not in create_resp.text

    # Auto EPG source should have been created
    epg_list = client.get("/api/sources/epg").json()
    assert any(e["type"] == "xtream_auto" and e["iptv_source_id"] == sid for e in epg_list)

    # Mock Xtream API with 2 categories and 3 channels
    respx.get("http://iptv.local/player_api.php", params={"username": "alice", "password": "super-secret-password", "action": "get_live_categories"}).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"category_id": "10", "category_name": "|FR| CINEMA"},
                {"category_id": "20", "category_name": "|XX| ADULT"},
            ],
        )
    )
    respx.get("http://iptv.local/player_api.php", params={"username": "alice", "password": "super-secret-password", "action": "get_live_streams"}).mock(
        return_value=httpx.Response(
            200,
            json=[
                {"stream_id": 101, "name": "Canal+ HD", "category_id": "10", "stream_icon": "http://l/1.png", "epg_channel_id": "canal.fr"},
                {"stream_id": 102, "name": "Canal+ Cinema", "category_id": "10", "stream_icon": "", "epg_channel_id": "canalcin.fr"},
                {"stream_id": 201, "name": "Hidden Chan", "category_id": "20", "stream_icon": "", "epg_channel_id": ""},
            ],
        )
    )

    sync_resp = client.post(f"/api/sources/iptv/{sid}/sync")
    assert sync_resp.status_code == 200
    assert sync_resp.json()["ok"] is True
    assert sync_resp.json()["channel_count"] == 3

    # Channels response must NOT contain raw stream_url / credentials
    ch_resp = client.get("/api/channels", params={"q": "canal"})
    assert ch_resp.status_code == 200
    assert "super-secret-password" not in ch_resp.text
    items = ch_resp.json()["items"]
    assert len(items) == 2
    assert "stream_url" not in items[0]

    # Mark second channel favorite and set manual EPG override
    ch2_id = items[1]["id"]
    assert client.patch(f"/api/channels/{ch2_id}/favorite", json={"is_favorite": True}).status_code == 200
    assert client.patch(f"/api/channels/{ch2_id}/epg-override", json={"epg_override_id": "custom.canal.fr"}).status_code == 200

    # Favorite appears first now
    ordered = client.get("/api/channels", params={"q": "canal"}).json()["items"]
    assert ordered[0]["id"] == ch2_id
    assert ordered[0]["is_favorite"] is True

    # Hide category 20
    cats = client.get("/api/categories", params={"include_hidden": True}).json()
    adult_cat = next(c for c in cats if c["name"] == "|XX| ADULT")
    assert client.patch(f"/api/categories/{adult_cat['id']}", json={"hidden": True}).status_code == 200
    visible_channels = client.get("/api/channels").json()["items"]
    assert all(c["name"] != "Hidden Chan" for c in visible_channels)

    # Re-sync succeeds -> favorite & EPG override are preserved
    sync2 = client.post(f"/api/sources/iptv/{sid}/sync")
    assert sync2.json()["ok"] is True
    after_resync = client.get("/api/channels", params={"favorites_only": True}).json()["items"]
    assert len(after_resync) == 1
    assert after_resync[0]["epg_override_id"] == "custom.canal.fr"

    # Simulated failure preserves prior catalog
    respx.get("http://iptv.local/player_api.php", params={"username": "alice", "password": "super-secret-password", "action": "get_live_categories"}).mock(
        side_effect=httpx.ConnectTimeout("timeout")
    )
    fail_sync = client.post(f"/api/sources/iptv/{sid}/sync")
    assert fail_sync.status_code == 200
    assert fail_sync.json()["ok"] is False
    assert len(client.get("/api/channels", params={"include_hidden": True}).json()["items"]) == 3
