from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import get_db
from ..uhf_client import UHFClient, UHFServerError, discover_uhf_servers

router = APIRouter(prefix="/api/settings", tags=["settings"])


class UHFServerSettingsUpdate(BaseModel):
    url: str | None = None
    password: str | None = None
    clear_password: bool = False
    device_id: str | None = None


class DefaultsSettingsUpdate(BaseModel):
    pre_padding_min: int | None = Field(default=None, ge=0, le=120)
    post_padding_min: int | None = Field(default=None, ge=0, le=180)
    max_simultaneous_streams: int | None = Field(default=None, ge=1, le=50)
    sync_interval_hours: int | None = Field(default=None, ge=1, le=168)
    epg_retention_hours: int | None = Field(default=None, ge=1, le=336)


class SettingsUpdate(BaseModel):
    uhf_server: UHFServerSettingsUpdate | None = None
    defaults: DefaultsSettingsUpdate | None = None


class TestUHFRequest(BaseModel):
    url: str | None = None
    password: str | None = None


class DetectPlaylistRequest(BaseModel):
    host: str | None = None


def _redacted_settings() -> dict[str, Any]:
    db = get_db()
    uhf = db.get_setting("uhf_server")
    defaults = db.get_setting("defaults")
    return {
        "uhf_server": {
            "url": uhf.get("url", ""),
            "password_configured": bool(uhf.get("password")),
            "token_cached": bool(uhf.get("token")),
            "device_id": uhf.get("device_id", "uhf-scheduler-web"),
        },
        "defaults": defaults,
    }


@router.get("")
def get_settings() -> dict[str, Any]:
    return _redacted_settings()


@router.put("")
def update_settings(body: SettingsUpdate) -> dict[str, Any]:
    db = get_db()
    if body.uhf_server is not None:
        cfg = db.get_setting("uhf_server")
        url_changed = False
        pw_changed = False
        if body.uhf_server.url is not None:
            new_url = body.uhf_server.url.strip().rstrip("/")
            if new_url != cfg.get("url"):
                cfg["url"] = new_url
                url_changed = True
        if body.uhf_server.clear_password:
            cfg["password"] = ""
            pw_changed = True
        elif body.uhf_server.password is not None and body.uhf_server.password != "":
            cfg["password"] = body.uhf_server.password
            pw_changed = True
        if body.uhf_server.device_id is not None and body.uhf_server.device_id.strip():
            cfg["device_id"] = body.uhf_server.device_id.strip()
        if url_changed or pw_changed:
            cfg["token"] = None
        db.set_setting("uhf_server", cfg)

    if body.defaults is not None:
        defs = db.get_setting("defaults")
        for field_name, val in body.defaults.model_dump(exclude_none=True).items():
            defs[field_name] = val
        db.set_setting("defaults", defs)

    return _redacted_settings()


@router.post("/test-uhf")
async def test_uhf_connection(body: TestUHFRequest | None = None) -> dict[str, Any]:
    db = get_db()
    client = UHFClient(db)
    url = body.url if body and body.url else None
    password = body.password if body and body.password is not None and body.password != "" else None
    try:
        return await client.test_connection(base_url=url, password=password)
    except UHFServerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/detect-playlist-id")
async def detect_playlist_id(body: DetectPlaylistRequest | None = None) -> dict[str, Any]:
    db = get_db()
    client = UHFClient(db)
    host = body.host if body else None
    try:
        mapping = await client.detect_playlist_ids(host_filter=host)
        return {"detected": mapping}
    except UHFServerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc


@router.post("/discover-uhf")
async def discover_uhf() -> dict[str, Any]:
    """Scan LAN via mDNS (_uhf-server._tcp.local.) and local ports for uhf-server instances."""
    servers = await discover_uhf_servers(timeout=2.0)
    return {"servers": servers}
