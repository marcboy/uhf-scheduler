from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..auto_record_service import evaluate_rules
from ..db import get_db
from ..epg_sync import sync_epg_source
from ..iptv_sync import sync_iptv_source

router = APIRouter(prefix="/api/sources", tags=["sources"])


class IPTVSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: Literal["xtream", "m3u"]
    url: str = Field(min_length=1)
    username: str | None = None
    password: str | None = None
    playlist_id: str | None = None
    auto_epg_enabled: bool = True
    enabled: bool = True
    max_streams: int | None = Field(default=None, ge=1, le=50)


class IPTVSourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    url: str | None = None
    username: str | None = None
    password: str | None = None
    playlist_id: str | None = None
    auto_epg_enabled: bool | None = None
    enabled: bool | None = None
    max_streams: int | None = Field(default=None, ge=1, le=50)


class EPGSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    url: str = Field(min_length=1)
    enabled: bool = True


class EPGSourceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    url: str | None = None
    enabled: bool | None = None


def _mask_iptv_row(row: Any) -> dict[str, Any]:
    d = dict(row)
    has_pw = bool(d.pop("password", None))
    d["password_configured"] = has_pw
    d["auto_epg_enabled"] = bool(d["auto_epg_enabled"])
    d["enabled"] = bool(d["enabled"])
    return d


def _ensure_xtream_auto_epg(conn: Any, iptv_source_id: int, name: str, enabled: bool) -> None:
    existing = conn.execute(
        "SELECT id FROM epg_sources WHERE type = 'xtream_auto' AND iptv_source_id = ?",
        (iptv_source_id,),
    ).fetchone()
    if enabled:
        if existing:
            conn.execute(
                "UPDATE epg_sources SET name = ?, enabled = 1 WHERE id = ?",
                (f"{name} (Provider EPG)", existing["id"]),
            )
        else:
            conn.execute(
                """
                INSERT INTO epg_sources (name, type, iptv_source_id, enabled)
                VALUES (?, 'xtream_auto', ?, 1)
                """,
                (f"{name} (Provider EPG)", iptv_source_id),
            )
    elif existing:
        conn.execute("UPDATE epg_sources SET enabled = 0 WHERE id = ?", (existing["id"],))


@router.get("/iptv")
def list_iptv_sources() -> list[dict[str, Any]]:
    db = get_db()
    with db.session() as conn:
        rows = conn.execute("SELECT * FROM iptv_sources ORDER BY id ASC").fetchall()
    return [_mask_iptv_row(r) for r in rows]


@router.post("/iptv", status_code=201)
def create_iptv_source(body: IPTVSourceCreate) -> dict[str, Any]:
    if body.type == "xtream" and (not body.username or not body.password):
        raise HTTPException(status_code=422, detail="Xtream Codes sources require username and password.")
    db = get_db()
    with db.session() as conn:
        cur = conn.execute(
            """
            INSERT INTO iptv_sources
                (name, type, url, username, password, playlist_id, auto_epg_enabled, enabled, max_streams)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                body.name.strip(),
                body.type,
                body.url.strip(),
                (body.username or "").strip() or None,
                body.password,
                (body.playlist_id or "").strip() or None,
                1 if (body.type == "xtream" and body.auto_epg_enabled) else 0,
                1 if body.enabled else 0,
                body.max_streams,
            ),
        )
        sid = cur.lastrowid
        if body.type == "xtream":
            _ensure_xtream_auto_epg(conn, sid, body.name.strip(), body.auto_epg_enabled and body.enabled)
        row = conn.execute("SELECT * FROM iptv_sources WHERE id = ?", (sid,)).fetchone()
    return _mask_iptv_row(row)


@router.put("/iptv/{source_id}")
def update_iptv_source(source_id: int, body: IPTVSourceUpdate) -> dict[str, Any]:
    db = get_db()
    with db.session() as conn:
        existing = conn.execute("SELECT * FROM iptv_sources WHERE id = ?", (source_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="IPTV source not found.")
        cur = dict(existing)

        name = body.name.strip() if body.name is not None else cur["name"]
        url = body.url.strip() if body.url is not None else cur["url"]
        username = body.username.strip() if body.username is not None else cur["username"]
        password = body.password if (body.password is not None and body.password != "") else cur["password"]
        playlist_id = (
            (body.playlist_id.strip() or None) if body.playlist_id is not None else cur["playlist_id"]
        )
        auto_epg = (
            (1 if body.auto_epg_enabled else 0)
            if body.auto_epg_enabled is not None
            else cur["auto_epg_enabled"]
        )
        enabled = (1 if body.enabled else 0) if body.enabled is not None else cur["enabled"]
        max_streams = body.max_streams if body.max_streams is not None else cur["max_streams"]

        conn.execute(
            """
            UPDATE iptv_sources
            SET name = ?, url = ?, username = ?, password = ?, playlist_id = ?,
                auto_epg_enabled = ?, enabled = ?, max_streams = ?
            WHERE id = ?
            """,
            (name, url, username, password, playlist_id, auto_epg, enabled, max_streams, source_id),
        )
        if cur["type"] == "xtream":
            _ensure_xtream_auto_epg(conn, source_id, name, bool(auto_epg and enabled))
        row = conn.execute("SELECT * FROM iptv_sources WHERE id = ?", (source_id,)).fetchone()
    return _mask_iptv_row(row)


@router.delete("/iptv/{source_id}")
def delete_iptv_source(source_id: int) -> dict[str, Any]:
    db = get_db()
    with db.session() as conn:
        cur = conn.execute("DELETE FROM iptv_sources WHERE id = ?", (source_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="IPTV source not found.")
    return {"deleted": True, "id": source_id}


@router.post("/iptv/{source_id}/sync")
async def trigger_iptv_sync(source_id: int) -> dict[str, Any]:
    db = get_db()
    try:
        return await sync_iptv_source(db, source_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


# --- EPG Sources ---

@router.get("/epg")
def list_epg_sources() -> list[dict[str, Any]]:
    db = get_db()
    with db.session() as conn:
        rows = conn.execute("SELECT * FROM epg_sources ORDER BY id ASC").fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["enabled"] = bool(d["enabled"])
        result.append(d)
    return result


@router.post("/epg", status_code=201)
def create_epg_source(body: EPGSourceCreate) -> dict[str, Any]:
    db = get_db()
    with db.session() as conn:
        cur = conn.execute(
            """
            INSERT INTO epg_sources (name, type, url, enabled)
            VALUES (?, 'xmltv_url', ?, ?)
            """,
            (body.name.strip(), body.url.strip(), 1 if body.enabled else 0),
        )
        row = conn.execute("SELECT * FROM epg_sources WHERE id = ?", (cur.lastrowid,)).fetchone()
    d = dict(row)
    d["enabled"] = bool(d["enabled"])
    return d


@router.put("/epg/{epg_source_id}")
def update_epg_source(epg_source_id: int, body: EPGSourceUpdate) -> dict[str, Any]:
    db = get_db()
    with db.session() as conn:
        existing = conn.execute("SELECT * FROM epg_sources WHERE id = ?", (epg_source_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="EPG source not found.")
        cur = dict(existing)
        name = body.name.strip() if body.name is not None else cur["name"]
        url = body.url.strip() if body.url is not None else cur["url"]
        enabled = (1 if body.enabled else 0) if body.enabled is not None else cur["enabled"]
        conn.execute(
            "UPDATE epg_sources SET name = ?, url = ?, enabled = ? WHERE id = ?",
            (name, url, enabled, epg_source_id),
        )
        row = conn.execute("SELECT * FROM epg_sources WHERE id = ?", (epg_source_id,)).fetchone()
    d = dict(row)
    d["enabled"] = bool(d["enabled"])
    return d


@router.delete("/epg/{epg_source_id}")
def delete_epg_source(epg_source_id: int) -> dict[str, Any]:
    db = get_db()
    with db.session() as conn:
        cur = conn.execute("DELETE FROM epg_sources WHERE id = ?", (epg_source_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="EPG source not found.")
    return {"deleted": True, "id": epg_source_id}


@router.post("/epg/{epg_source_id}/sync")
async def trigger_epg_sync(epg_source_id: int) -> dict[str, Any]:
    db = get_db()
    try:
        res = await sync_epg_source(db, epg_source_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if res.get("ok"):
        res["auto_record"] = await evaluate_rules(db)
    return res
