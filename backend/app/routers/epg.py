from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..auto_record_service import normalize_title
from ..db import get_db
from ..scheduler_service import parse_iso_datetime
from ..uhf_client import UHFClient, UHFServerError

router = APIRouter(prefix="/api/epg", tags=["epg"])


@router.get("/channels")
def search_epg_channels(
    q: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[dict[str, Any]]:
    """Search XMLTV channel IDs so users can assign manual per-channel EPG overrides."""
    db = get_db()
    sql = """
        SELECT ec.epg_source_id, es.name AS epg_source_name,
               ec.xmltv_id, ec.display_name, ec.icon_url
        FROM epg_channels ec
        JOIN epg_sources es ON es.id = ec.epg_source_id
        WHERE es.enabled = 1
    """
    params: list[Any] = []
    if q and q.strip():
        needle = f"%{q.strip().lower()}%"
        sql += " AND (ec.xmltv_id_lower LIKE ? OR ec.display_name_lower LIKE ?)"
        params.extend([needle, needle])
    sql += " ORDER BY ec.display_name_lower ASC LIMIT ?"
    params.append(limit)

    with db.session() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


@router.get("/search")
def search_epg_programs(
    q: str = Query(default="", description="Program title or description search query"),
    category_id: str | None = Query(default=None, description="Category ID or 'favorites'"),
    channel_id: int | None = Query(default=None, description="Restrict search strictly to one channel"),
    source_id: int | None = None,
    upcoming_only: bool = True,
    search_descriptions: bool = False,
    limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:
    """Search EPG programs across all future days, grouping mirror streams per broadcast."""
    cleaned_q = q.strip()
    if channel_id is None and len(cleaned_q) < 2:
        raise HTTPException(
            status_code=422,
            detail="Query 'q' must be at least 2 characters when channel_id is not specified.",
        )

    now_ts = int(datetime.now(timezone.utc).timestamp())
    db = get_db()

    # Fast, accent-insensitive path when strictly scoped to one selected channel
    if channel_id is not None:
        with db.session() as conn:
            ch = conn.execute(
                """
                SELECT ch.id, ch.name, ch.logo_url, ch.is_favorite,
                       ch.epg_channel_id, ch.epg_override_id, cat.name AS category_name
                FROM channels ch
                LEFT JOIN categories cat ON cat.id = ch.category_id
                WHERE ch.id = ?
                """,
                (channel_id,),
            ).fetchone()
            if not ch:
                raise HTTPException(status_code=404, detail=f"Channel {channel_id} not found.")
            eff_epg = (ch["epg_override_id"] or ch["epg_channel_id"] or "").strip().lower()
            if not eff_epg:
                return {"query": cleaned_q, "count": 0, "items": []}

            time_clause = "AND p.end_ts >= ?" if upcoming_only else ""
            q_params: list[Any] = [eff_epg]
            if upcoming_only:
                q_params.append(now_ts)

            raw_rows = conn.execute(
                f"""
                SELECT p.id, p.epg_source_id, es.type AS source_type,
                       p.start_ts, p.end_ts, p.title, p.description, p.category
                FROM epg_programs p
                JOIN epg_sources es ON es.id = p.epg_source_id
                WHERE es.enabled = 1
                  AND p.xmltv_id_lower = ?
                  {time_clause}
                ORDER BY CASE WHEN es.type = 'xmltv_url' THEN 0 ELSE 1 END,
                         p.epg_source_id ASC,
                         p.start_ts ASC
                """,
                q_params,
            ).fetchall()

        entries = [dict(r) for r in raw_rows]
        has_custom = any(e["source_type"] == "xmltv_url" for e in entries)
        filtered = [e for e in entries if e["source_type"] == "xmltv_url"] if has_custom else entries
        filtered.sort(key=lambda e: (e["start_ts"], e["epg_source_id"], e["id"]))

        deduped_entries: list[dict[str, Any]] = []
        for pr in filtered:
            if (
                deduped_entries
                and pr["start_ts"] < deduped_entries[-1]["end_ts"]
                and abs(pr["start_ts"] - deduped_entries[-1]["start_ts"]) <= 300
            ):
                continue
            deduped_entries.append(pr)

        norm_q = normalize_title(cleaned_q)
        ch_entry = {
            "channel_id": ch["id"],
            "channel_name": ch["name"],
            "category_name": ch["category_name"],
            "logo_url": ch["logo_url"],
            "is_favorite": bool(ch["is_favorite"]),
        }
        items_ch: list[dict[str, Any]] = []
        for pr in deduped_entries:
            if norm_q:
                in_title = norm_q in normalize_title(pr["title"])
                in_desc = search_descriptions and norm_q in normalize_title(pr.get("description") or "")
                if not (in_title or in_desc):
                    continue
            items_ch.append(
                {
                    "id": pr["id"],
                    "title": pr["title"],
                    "description": pr["description"],
                    "category": pr["category"],
                    "start_time": datetime.fromtimestamp(pr["start_ts"], tz=timezone.utc).isoformat(),
                    "end_time": datetime.fromtimestamp(pr["end_ts"], tz=timezone.utc).isoformat(),
                    "start_ts": pr["start_ts"],
                    "end_ts": pr["end_ts"],
                    **ch_entry,
                    "channels": [ch_entry],
                }
            )
            if len(items_ch) >= limit:
                break
        return {"query": cleaned_q, "count": len(items_ch), "items": items_ch}

    needle = f"%{cleaned_q}%"

    where = ["s.enabled = 1", "es.enabled = 1"]
    params: list[Any] = []
    if search_descriptions:
        where.append("(p.title LIKE ? OR p.description LIKE ?)")
        params.extend([needle, needle])
    else:
        where.append("p.title LIKE ?")
        params.append(needle)

    if upcoming_only:
        where.append("p.end_ts >= ?")
        params.append(now_ts)

    if category_id == "favorites":
        where.append("ch.is_favorite = 1")
    elif category_id is not None and category_id != "" and category_id != "all":
        try:
            cat_int = int(category_id)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="category_id must be an integer or 'favorites'.") from exc
        where.append("ch.category_id = ?")
        params.append(cat_int)
    else:
        where.append("(cat.hidden IS NULL OR cat.hidden = 0)")

    if source_id is not None:
        where.append("ch.source_id = ?")
        params.append(source_id)

    where_sql = " AND ".join(where)
    with db.session() as conn:
        rows = conn.execute(
            f"""
            SELECT p.id, p.epg_source_id, es.type AS source_type, p.xmltv_id_lower,
                   p.start_ts, p.end_ts, p.title, p.description, p.category,
                   ch.id AS channel_id, ch.name AS channel_name, ch.logo_url,
                   ch.is_favorite, cat.name AS category_name
            FROM epg_programs p
            JOIN epg_sources es ON es.id = p.epg_source_id
            JOIN channels ch
              ON LOWER(COALESCE(NULLIF(TRIM(ch.epg_override_id), ''), ch.epg_channel_id_lower)) = p.xmltv_id_lower
            JOIN iptv_sources s ON s.id = ch.source_id
            LEFT JOIN categories cat ON cat.id = ch.category_id
            WHERE {where_sql}
            ORDER BY CASE WHEN p.title LIKE ? THEN 0 ELSE 1 END,
                     p.start_ts ASC,
                     CASE WHEN es.type = 'xmltv_url' THEN 0 ELSE 1 END,
                     ch.is_favorite DESC,
                     LENGTH(ch.name) ASC,
                     ch.name_lower ASC
            LIMIT ?
            """,
            [*params, needle, limit * 12],
        ).fetchall()

    # Group by broadcast (xmltv_id_lower + start_ts within 5 min), preferring custom xmltv_url
    # and collecting all mirror streams (e.g. "TMC SD", "TMC 1 SD") under a single result row.
    groups: list[dict[str, Any]] = []
    by_xmltv: dict[str, list[dict[str, Any]]] = {}

    for r in rows:
        xmltv_key = r["xmltv_id_lower"]
        st = r["start_ts"]
        existing_group = None
        for g in by_xmltv.get(xmltv_key, []):
            if abs(st - g["start_ts"]) <= 300:
                existing_group = g
                break

        ch_entry = {
            "channel_id": r["channel_id"],
            "channel_name": r["channel_name"],
            "category_name": r["category_name"],
            "logo_url": r["logo_url"],
            "is_favorite": bool(r["is_favorite"]),
        }

        if existing_group is None:
            if len(groups) >= limit:
                continue
            group = {
                "id": r["id"],
                "title": r["title"],
                "description": r["description"],
                "category": r["category"],
                "start_time": datetime.fromtimestamp(st, tz=timezone.utc).isoformat(),
                "end_time": datetime.fromtimestamp(r["end_ts"], tz=timezone.utc).isoformat(),
                "start_ts": st,
                "end_ts": r["end_ts"],
                "channel_id": ch_entry["channel_id"],
                "channel_name": ch_entry["channel_name"],
                "category_name": ch_entry["category_name"],
                "logo_url": ch_entry["logo_url"],
                "is_favorite": ch_entry["is_favorite"],
                "channels": [ch_entry],
                "_seen_ch_ids": {ch_entry["channel_id"]},
            }
            groups.append(group)
            by_xmltv.setdefault(xmltv_key, []).append(group)
        else:
            if ch_entry["channel_id"] not in existing_group["_seen_ch_ids"]:
                existing_group["_seen_ch_ids"].add(ch_entry["channel_id"])
                existing_group["channels"].append(ch_entry)

    for g in groups:
        g.pop("_seen_ch_ids", None)

    return {"query": q.strip(), "count": len(groups), "items": groups}


@router.get("/grid")
async def get_epg_grid(
    category_id: str | None = Query(default=None, description="Category ID or 'favorites'"),
    source_id: int | None = None,
    q: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = Query(default=120, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Return channels in the selected scope plus overlapping EPG programs and UHF recordings."""
    now = datetime.now(timezone.utc)
    try:
        start_dt = parse_iso_datetime(start).astimezone(timezone.utc) if start else now.replace(minute=0, second=0, microsecond=0)
        end_dt = parse_iso_datetime(end).astimezone(timezone.utc) if end else start_dt + timedelta(hours=6)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid start/end timestamp: {exc}") from exc

    if end_dt <= start_dt:
        raise HTTPException(status_code=422, detail="end must be after start.")

    start_ts = int(start_dt.timestamp())
    end_ts = int(end_dt.timestamp())

    db = get_db()
    with db.session() as conn:
        epg_src_cnt = conn.execute("SELECT COUNT(*) AS cnt FROM epg_sources WHERE enabled = 1").fetchone()["cnt"]
        total_prog_cnt = conn.execute("SELECT COUNT(*) AS cnt FROM epg_programs").fetchone()["cnt"]

        where = ["s.enabled = 1"]
        params: list[Any] = []
        if category_id == "favorites":
            where.append("ch.is_favorite = 1")
        elif category_id is not None and category_id != "":
            try:
                cat_int = int(category_id)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail="category_id must be an integer or 'favorites'.") from exc
            where.append("ch.category_id = ?")
            params.append(cat_int)
        else:
            where.append("(cat.hidden IS NULL OR cat.hidden = 0)")

        if source_id is not None:
            where.append("ch.source_id = ?")
            params.append(source_id)
        if q and q.strip():
            where.append("ch.name_lower LIKE ?")
            params.append(f"%{q.strip().lower()}%")

        where_sql = " AND ".join(where)
        base_from = """
            FROM channels ch
            JOIN iptv_sources s ON s.id = ch.source_id
            LEFT JOIN categories cat ON cat.id = ch.category_id
        """
        total_channels = conn.execute(
            f"SELECT COUNT(*) AS cnt {base_from} WHERE {where_sql}", params
        ).fetchone()["cnt"]

        ch_rows = conn.execute(
            f"""
            SELECT ch.id, ch.source_id, s.name AS source_name,
                   ch.category_id, cat.name AS category_name,
                   ch.name, ch.stream_url, ch.logo_url,
                   ch.epg_channel_id, ch.epg_override_id, ch.is_favorite
            {base_from}
            WHERE {where_sql}
            ORDER BY ch.is_favorite DESC, ch.name_lower ASC
            LIMIT ? OFFSET ?
            """,
            [*params, limit, offset],
        ).fetchall()

        epg_keys: set[str] = set()
        channel_list = []
        for r in ch_rows:
            eff_epg = (r["epg_override_id"] or r["epg_channel_id"] or "").strip()
            eff_lower = eff_epg.lower() if eff_epg else None
            if eff_lower:
                epg_keys.add(eff_lower)
            channel_list.append((dict(r), eff_epg or None, eff_lower))

        programs_by_epg: dict[str, list[dict[str, Any]]] = {}
        if epg_keys:
            keys_list = list(epg_keys)
            raw_by_epg: dict[str, list[dict[str, Any]]] = {}
            for i in range(0, len(keys_list), 400):
                chunk = keys_list[i : i + 400]
                placeholders = ",".join("?" for _ in chunk)
                p_rows = conn.execute(
                    f"""
                    SELECT p.id, p.epg_source_id, es.type AS source_type, p.xmltv_id_lower,
                           p.start_ts, p.end_ts, p.title, p.description, p.category, p.icon_url
                    FROM epg_programs p
                    JOIN epg_sources es ON es.id = p.epg_source_id
                    WHERE es.enabled = 1
                      AND p.xmltv_id_lower IN ({placeholders})
                      AND p.start_ts < ?
                      AND p.end_ts > ?
                    ORDER BY CASE WHEN es.type = 'xmltv_url' THEN 0 ELSE 1 END,
                             p.epg_source_id ASC,
                             p.start_ts ASC
                    """,
                    [*chunk, end_ts, start_ts],
                ).fetchall()
                for pr in p_rows:
                    raw_by_epg.setdefault(pr["xmltv_id_lower"], []).append(dict(pr))

            # Deduplicate per channel: prefer custom xmltv_url sources over xtream_auto,
            # falling back to xtream_auto only for channels/slots not covered by custom feeds,
            # and collapse duplicate entries starting within 5 minutes of an already-kept slot.
            for key, entries in raw_by_epg.items():
                has_custom = any(e["source_type"] == "xmltv_url" for e in entries)
                filtered = [e for e in entries if e["source_type"] == "xmltv_url"] if has_custom else entries
                filtered.sort(key=lambda e: (e["start_ts"], e["epg_source_id"], e["id"]))
                deduped: list[dict[str, Any]] = []
                for pr in filtered:
                    if (
                        deduped
                        and pr["start_ts"] < deduped[-1]["end_ts"]
                        and abs(pr["start_ts"] - deduped[-1]["start_ts"]) <= 300
                    ):
                        continue
                    deduped.append(
                        {
                            "id": pr["id"],
                            "start_time": datetime.fromtimestamp(pr["start_ts"], tz=timezone.utc).isoformat(),
                            "end_time": datetime.fromtimestamp(pr["end_ts"], tz=timezone.utc).isoformat(),
                            "start_ts": pr["start_ts"],
                            "end_ts": pr["end_ts"],
                            "title": pr["title"],
                            "description": pr["description"],
                            "category": pr["category"],
                            "icon_url": pr["icon_url"],
                        }
                    )
                programs_by_epg[key] = deduped

    # Fetch active/scheduled uhf-server recordings gracefully (don't fail grid if uhf-server is offline)
    uhf_recordings: list[dict[str, Any]] = []
    uhf_warning: str | None = None
    try:
        uhf_recordings = await UHFClient(db).list_recordings()
    except UHFServerError as exc:
        uhf_warning = exc.message

    recs_by_url: dict[str, list[dict[str, Any]]] = {}
    recs_by_name: dict[str, list[dict[str, Any]]] = {}
    for rec in uhf_recordings:
        status = (rec.get("status") or "").lower()
        if status not in ("scheduled", "recording"):
            continue
        try:
            r_start = parse_iso_datetime(rec["start_time"]).astimezone(timezone.utc)
            r_dur = int(rec["duration_seconds"])
            r_end = r_start + timedelta(seconds=r_dur)
        except (KeyError, TypeError, ValueError):
            continue
        if r_start >= end_dt or r_end <= start_dt:
            continue
        compact = {
            "id": rec.get("id"),
            "name": rec.get("name"),
            "status": status,
            "start_time": r_start.isoformat(),
            "end_time": r_end.isoformat(),
            "start_ts": int(r_start.timestamp()),
            "end_ts": int(r_end.timestamp()),
            "recurrence_days": rec.get("recurrence_days"),
        }
        if rec.get("url"):
            recs_by_url.setdefault(rec["url"], []).append(compact)
        ch_desc = (rec.get("description") or (rec.get("metadata") or {}).get("name") or "").strip().lower()
        if ch_desc:
            recs_by_name.setdefault(ch_desc, []).append(compact)

    rows_out: list[dict[str, Any]] = []
    for ch, eff_epg, eff_lower in channel_list:
        matched_recs = list(recs_by_url.get(ch["stream_url"], []))
        if not matched_recs:
            matched_recs = list(recs_by_name.get(ch["name"].strip().lower(), []))
        rows_out.append(
            {
                "id": ch["id"],
                "source_id": ch["source_id"],
                "source_name": ch["source_name"],
                "category_id": ch["category_id"],
                "category_name": ch["category_name"],
                "name": ch["name"],
                "logo_url": ch["logo_url"],
                "epg_channel_id": ch["epg_channel_id"],
                "epg_override_id": ch["epg_override_id"],
                "effective_epg_id": eff_epg,
                "is_favorite": bool(ch["is_favorite"]),
                "programs": programs_by_epg.get(eff_lower, []) if eff_lower else [],
                "recordings": matched_recs,
            }
        )

    return {
        "start_time": start_dt.isoformat(),
        "end_time": end_dt.isoformat(),
        "start_ts": start_ts,
        "end_ts": end_ts,
        "epg_source_count": epg_src_cnt,
        "total_epg_programs": total_prog_cnt,
        "total_channels": total_channels,
        "uhf_warning": uhf_warning,
        "channels": rows_out,
    }
