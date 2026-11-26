from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..db import get_db
from ..scheduler_service import (
    build_uhf_recording_payload,
    compute_schedule_window,
    detect_overlaps,
    validate_recurrence,
)
from ..uhf_client import UHFClient, UHFServerError

router = APIRouter(prefix="/api/recordings", tags=["recordings"])


class ScheduleRecordingRequest(BaseModel):
    channel_id: int
    name: str | None = None
    start_time: str
    duration_seconds: int | None = None
    end_time: str | None = None
    pre_padding_min: int | None = Field(default=None, ge=0, le=120)
    post_padding_min: int | None = Field(default=None, ge=0, le=180)
    recurrence_days: list[int] | None = None
    recurrence_end_date: str | None = None
    timezone: str | None = None


class EditRecordingRequest(BaseModel):
    channel_id: int | None = None
    name: str | None = None
    start_time: str
    duration_seconds: int | None = None
    end_time: str | None = None
    pre_padding_min: int = Field(default=0, ge=0, le=120)
    post_padding_min: int = Field(default=0, ge=0, le=180)
    recurrence_days: list[int] | None = None
    recurrence_end_date: str | None = None
    timezone: str | None = None


class OverlapCheckRequest(BaseModel):
    start_time: str
    duration_seconds: int | None = None
    end_time: str | None = None
    pre_padding_min: int = Field(default=0, ge=0, le=120)
    post_padding_min: int = Field(default=0, ge=0, le=180)
    channel_id: int | None = None
    exclude_recording_id: str | None = None


def _load_channel_with_source(channel_id: int) -> tuple[dict[str, Any], str | None, dict[str, Any]]:
    db = get_db()
    with db.session() as conn:
        row = conn.execute(
            """
            SELECT ch.*, cat.name AS category_name,
                   s.playlist_id AS source_playlist_id, s.max_streams AS source_max_streams
            FROM channels ch
            JOIN iptv_sources s ON s.id = ch.source_id
            LEFT JOIN categories cat ON cat.id = ch.category_id
            WHERE ch.id = ?
            """,
            (channel_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"Channel {channel_id} not found.")
    d = dict(row)
    return d, d.get("category_name"), {
        "playlist_id": d.get("source_playlist_id"),
        "max_streams": d.get("source_max_streams"),
    }


def _enrich_recording(rec: dict[str, Any], uhf_base: str, url_to_channel_id: dict[str, int]) -> dict[str, Any]:
    out = dict(rec)
    # Never leak raw provider credentials embedded in stream URLs to the browser UI
    rec_url = out.pop("url", None)
    out.pop("parent_id", None)
    out["channel_id"] = url_to_channel_id.get(rec_url) if rec_url else None
    rec_id = out.get("id")
    if uhf_base and rec_id:
        out["uhf_web_url"] = f"{uhf_base}/"
        out["download_url"] = f"{uhf_base}/dvr/recordings/{rec_id}/download"
        out["stream_url_uhf"] = f"{uhf_base}/dvr/recordings/{rec_id}/stream"
    return out


@router.get("")
async def list_recordings() -> dict[str, Any]:
    db = get_db()
    client = UHFClient(db)
    uhf_cfg = db.get_setting("uhf_server")
    uhf_base = (uhf_cfg.get("url") or "").rstrip("/")

    try:
        raw_recs = await client.list_recordings()
    except UHFServerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    with db.session() as conn:
        ch_rows = conn.execute("SELECT id, stream_url FROM channels").fetchall()
    url_map = {r["stream_url"]: r["id"] for r in ch_rows}

    enriched = [_enrich_recording(r, uhf_base, url_map) for r in raw_recs]
    return {"uhf_server_url": uhf_base, "items": enriched}


@router.post("/check-overlap")
async def check_overlap(body: OverlapCheckRequest) -> dict[str, Any]:
    db = get_db()
    defaults = db.get_setting("defaults")
    max_streams = int(defaults.get("max_simultaneous_streams", 1))
    if body.channel_id is not None:
        _, _, src_info = _load_channel_with_source(body.channel_id)
        if src_info.get("max_streams"):
            max_streams = int(src_info["max_streams"])

    try:
        start_dt, duration_sec = compute_schedule_window(
            start_time_iso=body.start_time,
            duration_seconds=body.duration_seconds,
            end_time_iso=body.end_time,
            pre_padding_min=body.pre_padding_min,
            post_padding_min=body.post_padding_min,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    client = UHFClient(db)
    try:
        existing = await client.list_recordings()
    except UHFServerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    return detect_overlaps(
        proposed_start=start_dt,
        proposed_duration=duration_sec,
        existing_recordings=existing,
        max_simultaneous_streams=max_streams,
        exclude_recording_id=body.exclude_recording_id,
    )


@router.post("", status_code=201)
async def schedule_recording(body: ScheduleRecordingRequest) -> dict[str, Any]:
    db = get_db()
    defaults = db.get_setting("defaults")
    pre_pad = body.pre_padding_min if body.pre_padding_min is not None else int(defaults.get("pre_padding_min", 0))
    post_pad = body.post_padding_min if body.post_padding_min is not None else int(defaults.get("post_padding_min", 0))

    channel, category_name, src_info = _load_channel_with_source(body.channel_id)

    try:
        start_dt, duration_sec = compute_schedule_window(
            start_time_iso=body.start_time,
            duration_seconds=body.duration_seconds,
            end_time_iso=body.end_time,
            pre_padding_min=pre_pad,
            post_padding_min=post_pad,
        )
        rec_days, rec_end, tz_name = validate_recurrence(
            body.recurrence_days, body.recurrence_end_date, body.timezone
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    payload = build_uhf_recording_payload(
        name=body.name,
        channel=channel,
        category_name=category_name,
        playlist_id=src_info.get("playlist_id"),
        start_dt=start_dt,
        duration_seconds=duration_sec,
        recurrence_days=rec_days,
        recurrence_end_date=rec_end,
        tz_name=tz_name or body.timezone,
    )

    client = UHFClient(db)
    try:
        created = await client.create_recording(payload)
    except UHFServerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    uhf_base = (db.get_setting("uhf_server").get("url") or "").rstrip("/")
    return _enrich_recording(created, uhf_base, {channel["stream_url"]: channel["id"]})


@router.put("/{recording_id}")
async def edit_scheduled_recording(recording_id: str, body: EditRecordingRequest) -> dict[str, Any]:
    """Safely edit a scheduled recording by creating the replacement first, then removing the old one."""
    db = get_db()
    client = UHFClient(db)

    try:
        old_rec = await client.get_recording(recording_id)
    except UHFServerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    old_status = (old_rec.get("status") or "").lower()
    if old_status != "scheduled":
        raise HTTPException(
            status_code=409,
            detail=f"Only scheduled recordings can be edited (current status: '{old_status}').",
        )

    try:
        start_dt, duration_sec = compute_schedule_window(
            start_time_iso=body.start_time,
            duration_seconds=body.duration_seconds,
            end_time_iso=body.end_time,
            pre_padding_min=body.pre_padding_min,
            post_padding_min=body.post_padding_min,
        )
        rec_days, rec_end, tz_name = validate_recurrence(
            body.recurrence_days, body.recurrence_end_date, body.timezone
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    url_map: dict[str, int] = {}
    if body.channel_id is not None:
        channel, category_name, src_info = _load_channel_with_source(body.channel_id)
        url_map[channel["stream_url"]] = channel["id"]
        payload = build_uhf_recording_payload(
            name=body.name or old_rec.get("name"),
            channel=channel,
            category_name=category_name,
            playlist_id=src_info.get("playlist_id"),
            start_dt=start_dt,
            duration_seconds=duration_sec,
            recurrence_days=rec_days,
            recurrence_end_date=rec_end,
            tz_name=tz_name or body.timezone,
        )
    else:
        payload = {
            "name": (body.name or old_rec.get("name") or "Recording").strip(),
            "url": old_rec["url"],
            "start_time": start_dt.isoformat(),
            "duration_seconds": duration_sec,
            "description": old_rec.get("description"),
            "parent_id": old_rec.get("parent_id"),
            "metadata": old_rec.get("metadata"),
        }
        if rec_days:
            payload["recurrence_days"] = rec_days
            payload["timezone"] = tz_name or old_rec.get("timezone") or "UTC"
            if rec_end:
                payload["recurrence_end_date"] = rec_end

    # Step 1: Create new recording first so a failure never drops the existing schedule
    try:
        created = await client.create_recording(payload)
    except UHFServerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc

    # Step 2: Remove old recording using proper recurrence-aware endpoint
    cleanup_warning: str | None = None
    try:
        if old_rec.get("recurrence_days"):
            await client.cancel_recurrence(recording_id)
        else:
            await client.delete_recording(recording_id)
    except UHFServerError as exc:
        cleanup_warning = (
            f"Created replacement recording {created.get('id')}, "
            f"but removing original {recording_id} failed: {exc.message}"
        )

    uhf_base = (db.get_setting("uhf_server").get("url") or "").rstrip("/")
    res = _enrich_recording(created, uhf_base, url_map)
    if cleanup_warning:
        res["warning"] = cleanup_warning
    return res


@router.post("/{recording_id}/stop")
async def stop_or_cancel_recording(recording_id: str) -> dict[str, Any]:
    """Dispatch to the exact uhf-server cancellation endpoint for the recording's state."""
    db = get_db()
    client = UHFClient(db)
    try:
        rec = await client.get_recording(recording_id)
        status = (rec.get("status") or "").lower()
        if status == "recording":
            await client.cancel_recording(recording_id)
            action = "cancelled_in_progress"
        elif status == "scheduled" and rec.get("recurrence_days"):
            await client.cancel_recurrence(recording_id)
            action = "cancelled_recurrence"
        else:
            await client.delete_recording(recording_id)
            action = "deleted"
    except UHFServerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"ok": True, "id": recording_id, "action": action}


@router.delete("/{recording_id}")
async def delete_recording(recording_id: str) -> dict[str, Any]:
    """Delete a one-off scheduled or terminal recording; stop series if recurring & scheduled."""
    db = get_db()
    client = UHFClient(db)
    try:
        rec = await client.get_recording(recording_id)
        status = (rec.get("status") or "").lower()
        if status == "scheduled" and rec.get("recurrence_days"):
            await client.cancel_recurrence(recording_id)
            action = "cancelled_recurrence"
        else:
            await client.delete_recording(recording_id)
            action = "deleted"
    except UHFServerError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    return {"ok": True, "id": recording_id, "action": action}
