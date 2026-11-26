from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..db import get_db

router = APIRouter(prefix="/api", tags=["channels"])


class CategoryVisibilityUpdate(BaseModel):
    hidden: bool


class FavoriteUpdate(BaseModel):
    is_favorite: bool


class EpgOverrideUpdate(BaseModel):
    epg_override_id: str | None = None


@router.get("/categories")
def list_categories(
    source_id: int | None = None,
    include_hidden: bool = False,
) -> list[dict[str, Any]]:
    db = get_db()
    sql = """
        SELECT c.id, c.source_id, s.name AS source_name, c.external_id, c.name, c.hidden,
               COUNT(ch.id) AS channel_count
        FROM categories c
        JOIN iptv_sources s ON s.id = c.source_id
        LEFT JOIN channels ch ON ch.category_id = c.id
        WHERE s.enabled = 1
    """
    params: list[Any] = []
    if source_id is not None:
        sql += " AND c.source_id = ?"
        params.append(source_id)
    if not include_hidden:
        sql += " AND c.hidden = 0"
    sql += " GROUP BY c.id ORDER BY c.name COLLATE NOCASE ASC"

    with db.session() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [{**dict(r), "hidden": bool(r["hidden"])} for r in rows]


@router.patch("/categories/{category_id}")
def update_category_visibility(category_id: int, body: CategoryVisibilityUpdate) -> dict[str, Any]:
    db = get_db()
    with db.session() as conn:
        cur = conn.execute(
            "UPDATE categories SET hidden = ? WHERE id = ?",
            (1 if body.hidden else 0, category_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Category not found.")
        row = conn.execute("SELECT * FROM categories WHERE id = ?", (category_id,)).fetchone()
    return {**dict(row), "hidden": bool(row["hidden"])}


@router.get("/channels")
def list_channels(
    q: str | None = None,
    source_id: int | None = None,
    category_id: int | None = None,
    favorites_only: bool = False,
    include_hidden: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Search/filter indexed channels without exposing raw stream URL credentials."""
    db = get_db()
    where = ["s.enabled = 1"]
    params: list[Any] = []

    if source_id is not None:
        where.append("ch.source_id = ?")
        params.append(source_id)
    if category_id is not None:
        where.append("ch.category_id = ?")
        params.append(category_id)
    if favorites_only:
        where.append("ch.is_favorite = 1")
    if not include_hidden and not favorites_only and category_id is None:
        where.append("(cat.hidden IS NULL OR cat.hidden = 0)")
    if q and q.strip():
        where.append("ch.name_lower LIKE ?")
        params.append(f"%{q.strip().lower()}%")

    where_sql = " AND ".join(where)
    base_from = """
        FROM channels ch
        JOIN iptv_sources s ON s.id = ch.source_id
        LEFT JOIN categories cat ON cat.id = ch.category_id
    """

    with db.session() as conn:
        total = conn.execute(f"SELECT COUNT(*) AS cnt {base_from} WHERE {where_sql}", params).fetchone()["cnt"]
        rows = conn.execute(
            f"""
            SELECT ch.id, ch.source_id, s.name AS source_name,
                   ch.category_id, cat.name AS category_name,
                   ch.name, ch.logo_url, ch.epg_channel_id, ch.epg_override_id,
                   ch.is_favorite
            {base_from}
            WHERE {where_sql}
            ORDER BY ch.is_favorite DESC, ch.name_lower ASC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()

    items = [{**dict(r), "is_favorite": bool(r["is_favorite"])} for r in rows]
    return {"total": total, "items": items}


@router.patch("/channels/{channel_id}/favorite")
def set_channel_favorite(channel_id: int, body: FavoriteUpdate) -> dict[str, Any]:
    db = get_db()
    with db.session() as conn:
        cur = conn.execute(
            "UPDATE channels SET is_favorite = ? WHERE id = ?",
            (1 if body.is_favorite else 0, channel_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Channel not found.")
        row = conn.execute(
            "SELECT id, name, is_favorite FROM channels WHERE id = ?", (channel_id,)
        ).fetchone()
    return {**dict(row), "is_favorite": bool(row["is_favorite"])}


@router.patch("/channels/{channel_id}/epg-override")
def set_channel_epg_override(channel_id: int, body: EpgOverrideUpdate) -> dict[str, Any]:
    db = get_db()
    val = (body.epg_override_id or "").strip() or None
    with db.session() as conn:
        cur = conn.execute(
            "UPDATE channels SET epg_override_id = ? WHERE id = ?",
            (val, channel_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Channel not found.")
        row = conn.execute(
            "SELECT id, name, epg_channel_id, epg_override_id FROM channels WHERE id = ?",
            (channel_id,),
        ).fetchone()
    return dict(row)
