from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def parse_iso_datetime(value: str) -> datetime:
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def compute_schedule_window(
    start_time_iso: str,
    duration_seconds: int | None = None,
    end_time_iso: str | None = None,
    pre_padding_min: int = 0,
    post_padding_min: int = 0,
    now: datetime | None = None,
) -> tuple[datetime, int]:
    """Validate and compute padded (start_dt_utc, duration_seconds).

    - Supports duration_seconds OR end_time_iso (including across midnight).
    - Applies pre/post padding, clamping padded start to `now` if pre-padding
      would push a starting-soon recording into the past.
    - Rejects schedules whose computed end time is in the past or duration <= 0.
    """
    ref_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    start_dt = parse_iso_datetime(start_time_iso).astimezone(timezone.utc)

    if end_time_iso:
        end_dt = parse_iso_datetime(end_time_iso).astimezone(timezone.utc)
        # If caller sent same-date end_time earlier than start_time (cross-midnight convenience)
        if end_dt <= start_dt and (start_dt - end_dt) < timedelta(hours=24):
            end_dt = end_dt + timedelta(days=1)
        base_duration = int((end_dt - start_dt).total_seconds())
    elif duration_seconds is not None:
        base_duration = int(duration_seconds)
        end_dt = start_dt + timedelta(seconds=base_duration)
    else:
        raise ValueError("Either duration_seconds or end_time must be provided.")

    if base_duration <= 0:
        raise ValueError("Recording duration must be greater than zero.")

    pre_sec = max(0, int(pre_padding_min)) * 60
    post_sec = max(0, int(post_padding_min)) * 60

    padded_start = start_dt - timedelta(seconds=pre_sec)
    padded_end = end_dt + timedelta(seconds=post_sec)

    if padded_end <= ref_now:
        raise ValueError("Recording end time must be in the future.")

    # Clamp start to now if pre-padding pushed start into the past (or recording already started)
    if padded_start < ref_now:
        padded_start = ref_now

    final_duration = int((padded_end - padded_start).total_seconds())
    if final_duration <= 0:
        raise ValueError("Computed recording duration must be greater than zero.")

    return padded_start, final_duration


def validate_recurrence(
    recurrence_days: list[int] | None,
    recurrence_end_date: str | None,
    tz_name: str | None,
) -> tuple[list[int] | None, str | None, str | None]:
    if not recurrence_days:
        return None, None, None

    cleaned_days = sorted({int(d) for d in recurrence_days})
    for d in cleaned_days:
        if d < 0 or d > 6:
            raise ValueError("recurrence_days values must be between 0 (Monday) and 6 (Sunday).")

    resolved_tz = (tz_name or "UTC").strip() or "UTC"
    try:
        ZoneInfo(resolved_tz)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Unknown IANA timezone: {resolved_tz}") from exc

    resolved_end: str | None = None
    if recurrence_end_date:
        end_dt = parse_iso_datetime(recurrence_end_date).astimezone(timezone.utc)
        resolved_end = end_dt.isoformat()

    return cleaned_days, resolved_end, resolved_tz


def default_recording_title(channel_name: str, start_dt: datetime, tz_name: str | None = None) -> str:
    local_dt = start_dt
    if tz_name:
        try:
            local_dt = start_dt.astimezone(ZoneInfo(tz_name))
        except ZoneInfoNotFoundError:
            pass
    return f"{channel_name} - {local_dt.strftime('%Y-%m-%d %H:%M')}"


def build_uhf_recording_payload(
    *,
    name: str | None,
    channel: dict[str, Any],
    category_name: str | None,
    playlist_id: str | None,
    start_dt: datetime,
    duration_seconds: int,
    recurrence_days: list[int] | None = None,
    recurrence_end_date: str | None = None,
    tz_name: str | None = None,
) -> dict[str, Any]:
    title = (name or "").strip() or default_recording_title(channel["name"], start_dt, tz_name)
    metadata: dict[str, Any] = {"name": channel["name"]}
    if category_name:
        metadata["categoryName"] = category_name
        metadata["originalCategoryName"] = category_name
    if channel.get("logo_url"):
        metadata["thumbnailURL"] = channel["logo_url"]
    if playlist_id:
        metadata["playlistID"] = playlist_id

    payload: dict[str, Any] = {
        "name": title,
        "url": channel["stream_url"],
        "start_time": start_dt.astimezone(timezone.utc).isoformat(),
        "duration_seconds": int(duration_seconds),
        "description": channel["name"],
        "parent_id": channel["stream_url"],
        "metadata": metadata,
    }
    if recurrence_days:
        payload["recurrence_days"] = recurrence_days
        payload["timezone"] = tz_name or "UTC"
        if recurrence_end_date:
            payload["recurrence_end_date"] = recurrence_end_date
    return payload


def _project_recording_windows(
    rec: dict[str, Any],
    target_start: datetime,
    target_end: datetime,
) -> list[tuple[datetime, datetime]]:
    """Return concrete [start, end) intervals for an existing uhf-server recording near target window."""
    try:
        r_start = parse_iso_datetime(rec["start_time"]).astimezone(timezone.utc)
        r_dur = int(rec["duration_seconds"])
    except (KeyError, TypeError, ValueError):
        return []
    if r_dur <= 0:
        return []

    windows = [(r_start, r_start + timedelta(seconds=r_dur))]

    # If recurring, project matching weekdays around the target window
    rec_days = rec.get("recurrence_days")
    if isinstance(rec_days, list) and rec_days:
        tz_name = rec.get("timezone") or "UTC"
        try:
            tz = ZoneInfo(tz_name)
        except ZoneInfoNotFoundError:
            tz = timezone.utc

        end_limit: datetime | None = None
        if rec.get("recurrence_end_date"):
            try:
                end_limit = parse_iso_datetime(rec["recurrence_end_date"]).astimezone(timezone.utc)
            except ValueError:
                end_limit = None

        r_local = r_start.astimezone(tz)
        # Check days from target_start - 1d through target_end + 1d
        probe_date = (target_start.astimezone(tz) - timedelta(days=1)).date()
        last_date = (target_end.astimezone(tz) + timedelta(days=1)).date()
        while probe_date <= last_date:
            if probe_date.weekday() in rec_days:
                cand_local = datetime(
                    probe_date.year,
                    probe_date.month,
                    probe_date.day,
                    r_local.hour,
                    r_local.minute,
                    r_local.second,
                    tzinfo=tz,
                )
                cand_utc = cand_local.astimezone(timezone.utc)
                if cand_utc > r_start and (end_limit is None or cand_utc <= end_limit):
                    windows.append((cand_utc, cand_utc + timedelta(seconds=r_dur)))
            probe_date += timedelta(days=1)

    return windows


def detect_overlaps(
    *,
    proposed_start: datetime,
    proposed_duration: int,
    existing_recordings: list[dict[str, Any]],
    max_simultaneous_streams: int = 1,
    exclude_recording_id: str | None = None,
) -> dict[str, Any]:
    """Check if [proposed_start, proposed_end) overlaps existing scheduled/active timers."""
    p_start = proposed_start.astimezone(timezone.utc)
    p_end = p_start + timedelta(seconds=int(proposed_duration))

    overlapping: list[dict[str, Any]] = []
    # Sweep-line events within [p_start, p_end) to compute true peak concurrency
    events: list[tuple[datetime, int]] = [(p_start, 1), (p_end, -1)]

    for rec in existing_recordings:
        rec_id = rec.get("id")
        if exclude_recording_id and rec_id == exclude_recording_id:
            continue
        status = (rec.get("status") or "").lower()
        if status not in ("scheduled", "recording"):
            continue

        for w_start, w_end in _project_recording_windows(rec, p_start, p_end):
            # Strict half-open interval overlap: adjacent [a, b) and [b, c) do NOT overlap
            ov_start = max(p_start, w_start)
            ov_end = min(p_end, w_end)
            if ov_start < ov_end:
                overlapping.append(
                    {
                        "id": rec_id,
                        "name": rec.get("name"),
                        "channel": rec.get("description") or (rec.get("metadata") or {}).get("name"),
                        "status": status,
                        "start_time": w_start.isoformat(),
                        "end_time": w_end.isoformat(),
                        "overlap_minutes": round((ov_end - ov_start).total_seconds() / 60, 1),
                    }
                )
                events.append((ov_start, 1))
                events.append((ov_end, -1))
                break  # count each existing recording once against this window

    # Sort with -1 before +1 at identical timestamp so adjacent intervals don't stack
    events.sort(key=lambda item: (item[0], item[1]))
    current = 0
    peak = 0
    for _ts, delta in events:
        current += delta
        if current > peak:
            peak = current

    limit = max(1, int(max_simultaneous_streams))
    return {
        "has_conflict": peak > limit,
        "peak_concurrent": peak,
        "max_simultaneous_streams": limit,
        "conflicts": overlapping,
    }
