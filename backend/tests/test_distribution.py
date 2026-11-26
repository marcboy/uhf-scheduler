from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import uhf_client, update_checker


@respx.mock
def test_discover_host_docker_internal(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(uhf_client, "_browse_mdns_sync", lambda timeout=2.0: [])
    respx.get("http://127.0.0.1:8085/auth/methods").mock(side_effect=httpx.ConnectError("no"))
    respx.get("http://127.0.0.1:8000/auth/methods").mock(side_effect=httpx.ConnectError("no"))
    respx.get("http://host.docker.internal:8085/auth/methods").mock(side_effect=httpx.ConnectError("no"))
    respx.get("http://host.docker.internal:8000/auth/methods").mock(
        return_value=httpx.Response(200, json={"uhf_account": True, "server_password": True, "password_required": True})
    )

    resp = client.post("/api/settings/discover-uhf")
    assert resp.status_code == 200
    servers = resp.json()["servers"]
    assert len(servers) == 1
    assert servers[0]["url"] == "http://host.docker.internal:8000"
    assert servers[0]["password_required"] is True


@respx.mock
def test_update_check_newer_uptodate_and_offline(client: TestClient) -> None:
    update_checker.reset_update_cache()
    route = respx.get(update_checker.GITHUB_LATEST_RELEASE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "tag_name": "v9.9.9",
                "html_url": "https://github.com/marcboy/uhf-scheduler/releases/tag/v9.9.9",
                "body": "Major update",
            },
        )
    )
    r1 = client.get("/api/system/update-check")
    assert r1.status_code == 200
    b1 = r1.json()
    assert b1["update_available"] is True
    assert b1["latest_version"] == "9.9.9"
    assert b1["checked_online"] is True

    # Second call uses cache (no extra GitHub HTTP call)
    r2 = client.get("/api/system/update-check")
    assert r2.status_code == 200
    assert route.call_count == 1

    # Force bypasses cache -> already up to date
    respx.get(update_checker.GITHUB_LATEST_RELEASE_URL).mock(
        return_value=httpx.Response(200, json={"tag_name": "v0.0.1"})
    )
    r3 = client.get("/api/system/update-check", params={"force": "true"})
    assert r3.status_code == 200
    assert r3.json()["update_available"] is False

    # Offline fails silently with HTTP 200
    respx.get(update_checker.GITHUB_LATEST_RELEASE_URL).mock(side_effect=httpx.ConnectError("offline"))
    r4 = client.get("/api/system/update-check", params={"force": "true"})
    assert r4.status_code == 200
    assert r4.json()["update_available"] is False
    assert r4.json()["checked_online"] is False
