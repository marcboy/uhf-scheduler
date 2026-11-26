from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from ..auto_record_service import evaluate_rules, force_schedule_history_entry
from ..db import get_db
from ..epg_sync import sync_all_enabled_epg_sources
from ..uhf_client import UHFServerError

router = APIRouter(prefix="/api/rules", tags=["rules"])


class AutoRecordRuleCreate(BaseModel):
    channel_id: int
    title_pattern: str = Field(min_length=1, max_length=200)
    match_mode: Literal["starts_with", "contains", "exact"] = "starts_with"
    pre_padding_min: int | None = Field(default=None, ge=0, le=120)
    post_padding_min: int | None = Field(default=None, ge=0, le=180)
    skip_if_conflict: bool = True
    enabled: bool = True
    run_now: bool = True


class AutoRecordRuleUpdate(BaseModel):
    title_pattern: str | None = Field(default=None, min_length=1, max_length=200)
    match_mode: Literal["starts_with", "contains", "exact"] | None = None
    pre_padding_min: int | None = Field(default=None, ge=0, le=120)
    post_padding_min: int | None = Field(default=None, ge=0, le=180)
    clear_padding_overrides: bool = False
    skip_if_conflict: bool | None = None
    enabled: bool | None = None


class RunRulesRequest(BaseModel):
    sync_epg_first: bool = False


def _format_rule_row(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["skip_if_conflict"] = bool(d["skip_if_conflict"])
    d["enabled"] = bool(d["enabled"])
    return d


def _fetch_rule(conn: Any, rule_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT r.*,
               ch.name AS channel_name, ch.logo_url,
               cat.name AS category_name,
               s.name AS source_name,
               (SELECT COUNT(*) FROM auto_record_history h WHERE h.rule_id = r.id AND h.status = 'scheduled') AS total_scheduled,
               (SELECT COUNT(*) FROM auto_record_history h WHERE h.rule_id = r.id AND h.status = 'skipped_conflict') AS total_skipped
        FROM auto_record_rules r
        JOIN channels ch ON ch.id = r.channel_id
        JOIN iptv_sources s ON s.id = ch.source_id
        LEFT JOIN categories cat ON cat.id = ch.category_id
        WHERE r.id = ?
        """,
        (rule_id,),
    ).fetchone()
    return _format_rule_row(row) if row else None


@router.get("")
def list_rules() -> list[dict[str, Any]]:
    db = get_db()
    with db.session() as conn:
        rows = conn.execute(
            """
            SELECT r.*,
                   ch.name AS channel_name, ch.logo_url,
                   cat.name AS category_name,
                   s.name AS source_name,
                   (SELECT COUNT(*) FROM auto_record_history h WHERE h.rule_id = r.id AND h.status = 'scheduled') AS total_scheduled,
                   (SELECT COUNT(*) FROM auto_record_history h WHERE h.rule_id = r.id AND h.status = 'skipped_conflict') AS total_skipped
            FROM auto_record_rules r
            JOIN channels ch ON ch.id = r.channel_id
            JOIN iptv_sources s ON s.id = ch.source_id
            LEFT JOIN categories cat ON cat.id = ch.category_id
            ORDER BY r.id DESC
            """
        ).fetchall()
    return [_format_rule_row(r) for r in rows]


@router.post("", status_code=201)
async def create_rule(body: AutoRecordRuleCreate) -> dict[str, Any]:
    db = get_db()
    with db.session() as conn:
        ch = conn.execute("SELECT id FROM channels WHERE id = ?", (body.channel_id,)).fetchone()
        if not ch:
            raise HTTPException(status_code=404, detail=f"Channel {body.channel_id} not found.")
        cur = conn.execute(
            """
            INSERT INTO auto_record_rules
                (channel_id, title_pattern, match_mode, pre_padding_min, post_padding_min, skip_if_conflict, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                body.channel_id,
                body.title_pattern.strip(),
                body.match_mode,
                body.pre_padding_min,
                body.post_padding_min,
                1 if body.skip_if_conflict else 0,
                1 if body.enabled else 0,
            ),
        )
        rule_id = cur.lastrowid

    run_summary = None
    if body.run_now and body.enabled:
        run_summary = await evaluate_rules(db, rule_id=rule_id)

    with db.session() as conn:
        rule = _fetch_rule(conn, rule_id)
    assert rule is not None
    if run_summary is not None:
        rule["run_summary"] = run_summary
    return rule


@router.put("/{rule_id}")
def update_rule(rule_id: int, body: AutoRecordRuleUpdate) -> dict[str, Any]:
    db = get_db()
    with db.session() as conn:
        existing = conn.execute("SELECT * FROM auto_record_rules WHERE id = ?", (rule_id,)).fetchone()
        if not existing:
            raise HTTPException(status_code=404, detail="Auto-record rule not found.")
        cur = dict(existing)

        title_pattern = body.title_pattern.strip() if body.title_pattern is not None else cur["title_pattern"]
        match_mode = body.match_mode if body.match_mode is not None else cur["match_mode"]
        if body.clear_padding_overrides:
            pre_pad = None
            post_pad = None
        else:
            pre_pad = body.pre_padding_min if body.pre_padding_min is not None else cur["pre_padding_min"]
            post_pad = body.post_padding_min if body.post_padding_min is not None else cur["post_padding_min"]
        skip_if_conflict = (
            (1 if body.skip_if_conflict else 0)
            if body.skip_if_conflict is not None
            else cur["skip_if_conflict"]
        )
        enabled = (1 if body.enabled else 0) if body.enabled is not None else cur["enabled"]

        conn.execute(
            """
            UPDATE auto_record_rules
            SET title_pattern = ?, match_mode = ?, pre_padding_min = ?, post_padding_min = ?,
                skip_if_conflict = ?, enabled = ?
            WHERE id = ?
            """,
            (title_pattern, match_mode, pre_pad, post_pad, skip_if_conflict, enabled, rule_id),
        )
        rule = _fetch_rule(conn, rule_id)
    assert rule is not None
    return rule


@router.delete("/{rule_id}")
def delete_rule(rule_id: int) -> dict[str, Any]:
    db = get_db()
    with db.session() as conn:
        cur = conn.execute("DELETE FROM auto_record_rules WHERE id = ?", (rule_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Auto-record rule not found.")
    return {"deleted": True, "id": rule_id}


@router.post("/run")
async def run_all_rules(body: RunRulesRequest | None = None) -> dict[str, Any]:
    db = get_db()
    epg_results = None
    if body and body.sync_epg_first:
        epg_results = await sync_all_enabled_epg_sources(db)
    summary = await evaluate_rules(db)
    if epg_results is not None:
        summary["epg_sync"] = epg_results
    return summary


@router.post("/{rule_id}/run")
async def run_single_rule(rule_id: int) -> dict[str, Any]:
    db = get_db()
    with db.session() as conn:
        exists = conn.execute("SELECT id FROM auto_record_rules WHERE id = ?", (rule_id,)).fetchone()
        if not exists:
            raise HTTPException(status_code=404, detail="Auto-record rule not found.")
    return await evaluate_rules(db, rule_id=rule_id)


@router.get("/history")
def list_rule_history(
    rule_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    db = get_db()
    sql = """
        SELECT h.*,
               r.title_pattern,
               ch.name AS channel_name, ch.logo_url
        FROM auto_record_history h
        JOIN auto_record_rules r ON r.id = h.rule_id
        JOIN channels ch ON ch.id = h.channel_id
    """
    params: list[Any] = []
    if rule_id is not None:
        sql += " WHERE h.rule_id = ?"
        params.append(rule_id)
    sql += " ORDER BY h.program_start_ts DESC, h.id DESC LIMIT ?"
    params.append(limit)

    with db.session() as conn:
        rows = conn.execute(sql, params).fetchall()

    out = []
    for r in rows:
        d = dict(r)
        d["start_time"] = datetime.fromtimestamp(d["program_start_ts"], tz=timezone.utc).isoformat()
        d["end_time"] = datetime.fromtimestamp(d["program_end_ts"], tz=timezone.utc).isoformat()
        out.append(d)
    return out


@router.post("/history/{history_id}/force-schedule")
async def force_schedule(history_id: int) -> dict[str, Any]:
    db = get_db()
    try:
        return await force_schedule_history_entry(db, history_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except UHFServerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
