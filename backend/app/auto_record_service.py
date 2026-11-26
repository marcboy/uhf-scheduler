from __future__ import annotations

import asyncio
import logging
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import Database
from .scheduler_service import (
    build_uhf_recording_payload,
    detect_overlaps,
    parse_iso_datetime,
)
from .uhf_client import UHFClient, UHFServerError

logger = logging.getLogger(__name__)

_eval_lock = asyncio.Lock()
SHIFT_TOLERANCE_SECONDS = 15 * 60


def normalize_title(text: str) -> str:
    """Fold Unicode accents (NFKD), lowercase, and collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(stripped.lower().split())


def title_matches(program_title: str, pattern: str, match_mode: str = "starts_with") -> bool:
    norm_prog = normalize_title(program_title)
    norm_pat = normalize_title(pattern)
    if not norm_pat:
        return False
    if match_mode == "exact":
        return norm_prog == norm_pat
    if match_mode == "contains":
        return norm_pat in norm_prog
    # default: starts_with
    return norm_prog.startswith(norm_pat)


def is_sync_due(last_synced_at: str | None, interval_hours: int, now: datetime | None = None) -> bool:
    """Return True if a source has never synced or last synced longer ago than interval_hours."""
    if not last_synced_at:
        return True
    ref_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        last_dt = parse_iso_datetime(last_synced_at).astimezone(timezone.utc)
    except ValueError:
        return True
    return (ref_now - last_dt) >= timedelta(hours=max(1, int(interval_hours)))


def _deduped_upcoming_programs_for_epg_id(
    conn: Any,
    epg_id_lower: str,
    now_ts: int,
) -> list[dict[str, Any]]:
    """Fetch upcoming deduplicated EPG programs for one channel, preferring custom xmltv_url."""
    rows = conn.execute(
        """
        SELECT p.id, p.epg_source_id, es.type AS source_type,
               p.start_ts, p.end_ts, p.title, p.description
        FROM epg_programs p
        JOIN epg_sources es ON es.id = p.epg_source_id
        WHERE es.enabled = 1
          AND p.xmltv_id_lower = ?
          AND p.end_ts > ?
        ORDER BY CASE WHEN es.type = 'xmltv_url' THEN 0 ELSE 1 END,
                 p.epg_source_id ASC,
                 p.start_ts ASC
        """,
        (epg_id_lower, now_ts),
    ).fetchall()
    if not rows:
        return []
    entries = [dict(r) for r in rows]
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
        deduped.append(pr)
    return deduped


def _trim_padding_edges(
    *,
    raw_start: datetime,
    raw_end: datetime,
    pre_pad_min: int,
    post_pad_min: int,
    now: datetime,
    active_timers: list[dict[str, Any]],
    prev_cand_raw_end: datetime | None,
    next_cand_raw_start: datetime | None,
    trim_against_existing: bool,
) -> tuple[datetime, datetime]:
    """Apply pre/post padding and Option A edge trimming (never trimming core broadcast time)."""
    padded_start = raw_start - timedelta(minutes=max(0, pre_pad_min))
    padded_end = raw_end + timedelta(minutes=max(0, post_pad_min))

    if padded_start < now:
        padded_start = now

    # Trim shared padding edges between adjacent candidates in the same run
    if prev_cand_raw_end is not None and prev_cand_raw_end <= raw_start and padded_start < prev_cand_raw_end:
        padded_start = prev_cand_raw_end
    if next_cand_raw_start is not None and next_cand_raw_start >= raw_end and padded_end > next_cand_raw_start:
        padded_end = next_cand_raw_start

    # Trim padding edges touching existing scheduled/active uhf-server timers
    if trim_against_existing:
        for rec in active_timers:
            status = (rec.get("status") or "").lower()
            if status not in ("scheduled", "recording"):
                continue
            try:
                r_start = parse_iso_datetime(rec["start_time"]).astimezone(timezone.utc)
                r_end = r_start + timedelta(seconds=int(rec["duration_seconds"]))
            except (KeyError, TypeError, ValueError):
                continue
            # Existing timer ends inside our pre-padding window (before raw_start)
            if padded_start < r_end <= raw_start:
                padded_start = r_end
            # Existing timer starts inside our post-padding window (after raw_end)
            if raw_end <= r_start < padded_end:
                padded_end = r_start

    return padded_start, padded_end


def _matches_existing_uhf_timer(
    stream_url: str,
    raw_start_ts: int,
    active_timers: list[dict[str, Any]],
) -> dict[str, Any] | None:
    for rec in active_timers:
        status = (rec.get("status") or "").lower()
        if status not in ("scheduled", "recording"):
            continue
        if rec.get("url") != stream_url:
            continue
        try:
            r_start_ts = int(parse_iso_datetime(rec["start_time"]).astimezone(timezone.utc).timestamp())
        except (KeyError, TypeError, ValueError):
            continue
        if abs(r_start_ts - raw_start_ts) <= SHIFT_TOLERANCE_SECONDS:
            return rec
    return None


async def evaluate_rules(
    db: Database,
    rule_id: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate all enabled auto-record rules (or one specific rule) against indexed EPG."""
    async with _eval_lock:
        ref_now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        now_ts = int(ref_now.timestamp())
        now_iso = ref_now.isoformat()
        defaults = db.get_setting("defaults")
        default_pre = int(defaults.get("pre_padding_min", 2))
        default_post = int(defaults.get("post_padding_min", 5))
        default_max_streams = int(defaults.get("max_simultaneous_streams", 1))

        with db.session() as conn:
            sql = """
                SELECT r.*,
                       ch.name AS channel_name, ch.stream_url, ch.logo_url,
                       ch.epg_channel_id, ch.epg_override_id,
                       cat.name AS category_name,
                       s.playlist_id AS source_playlist_id,
                       s.max_streams AS source_max_streams
                FROM auto_record_rules r
                JOIN channels ch ON ch.id = r.channel_id
                JOIN iptv_sources s ON s.id = ch.source_id
                LEFT JOIN categories cat ON cat.id = ch.category_id
                WHERE s.enabled = 1
            """
            params: list[Any] = []
            if rule_id is not None:
                sql += " AND r.id = ?"
                params.append(rule_id)
            else:
                sql += " AND r.enabled = 1"
            sql += " ORDER BY r.id ASC"
            rules = [dict(r) for r in conn.execute(sql, params).fetchall()]

        if not rules:
            return {
                "evaluated_rules": 0,
                "scheduled": 0,
                "skipped_conflict": 0,
                "errors": 0,
                "uhf_error": None,
            }

        client = UHFClient(db)
        try:
            active_timers = await client.list_recordings()
        except UHFServerError as exc:
            logger.warning("Auto-record evaluation skipped; uhf-server unreachable: %s", exc.message)
            return {
                "evaluated_rules": len(rules),
                "scheduled": 0,
                "skipped_conflict": 0,
                "errors": len(rules),
                "uhf_error": exc.message,
            }

        # Step 1: Collect all candidate airings across targeted rules
        candidates: list[dict[str, Any]] = []
        rule_stats: dict[int, dict[str, int]] = {
            r["id"]: {"scheduled": 0, "skipped_conflict": 0, "errors": 0} for r in rules
        }

        with db.session() as conn:
            for rule in rules:
                eff_epg = (rule.get("epg_override_id") or rule.get("epg_channel_id") or "").strip().lower()
                if not eff_epg:
                    continue
                programs = _deduped_upcoming_programs_for_epg_id(conn, eff_epg, now_ts)
                for prog in programs:
                    if not title_matches(prog["title"], rule["title_pattern"], rule["match_mode"]):
                        continue
                    # Check history within +/- 15 min tolerance
                    hist = conn.execute(
                        """
                        SELECT id, status FROM auto_record_history
                        WHERE rule_id = ? AND ABS(program_start_ts - ?) <= ?
                        ORDER BY id DESC LIMIT 1
                        """,
                        (rule["id"], prog["start_ts"], SHIFT_TOLERANCE_SECONDS),
                    ).fetchone()
                    if hist and hist["status"] == "scheduled":
                        continue  # already handled (even if manually deleted later)
                    candidates.append(
                        {
                            "rule": rule,
                            "prog": prog,
                            "existing_history_id": hist["id"] if hist else None,
                            "raw_start": datetime.fromtimestamp(prog["start_ts"], tz=timezone.utc),
                            "raw_end": datetime.fromtimestamp(prog["end_ts"], tz=timezone.utc),
                        }
                    )

        # Sort chronologically so Option A back-to-back trimming works across consecutive airings
        candidates.sort(key=lambda c: (c["prog"]["start_ts"], c["rule"]["id"]))

        total_scheduled = 0
        total_skipped = 0
        total_errors = 0

        for idx, cand in enumerate(candidates):
            rule = cand["rule"]
            prog = cand["prog"]
            rid = rule["id"]
            raw_start: datetime = cand["raw_start"]
            raw_end: datetime = cand["raw_end"]

            # Skip if already present in live uhf-server (e.g. manually scheduled beforehand)
            existing_live = _matches_existing_uhf_timer(rule["stream_url"], prog["start_ts"], active_timers)
            if existing_live is not None:
                async with db.write_lock:
                    with db.session() as conn:
                        _upsert_history(
                            conn,
                            existing_history_id=cand["existing_history_id"],
                            rule_id=rid,
                            channel_id=rule["channel_id"],
                            program_title=prog["title"],
                            program_start_ts=prog["start_ts"],
                            program_end_ts=prog["end_ts"],
                            status="scheduled",
                            uhf_recording_id=existing_live.get("id"),
                            reason="Matched existing timer in uhf-server",
                        )
                continue

            pre_pad = rule["pre_padding_min"] if rule["pre_padding_min"] is not None else default_pre
            post_pad = rule["post_padding_min"] if rule["post_padding_min"] is not None else default_post
            skip_if_conflict = bool(rule["skip_if_conflict"])
            max_streams = int(rule.get("source_max_streams") or default_max_streams)

            prev_end = candidates[idx - 1]["raw_end"] if idx > 0 else None
            next_start = candidates[idx + 1]["raw_start"] if idx + 1 < len(candidates) else None

            padded_start, padded_end = _trim_padding_edges(
                raw_start=raw_start,
                raw_end=raw_end,
                pre_pad_min=pre_pad,
                post_pad_min=post_pad,
                now=ref_now,
                active_timers=active_timers,
                prev_cand_raw_end=prev_end if skip_if_conflict else None,
                next_cand_raw_start=next_start if skip_if_conflict else None,
                trim_against_existing=skip_if_conflict,
            )

            duration_sec = int((padded_end - padded_start).total_seconds())
            if duration_sec <= 0:
                continue

            if skip_if_conflict:
                ov = detect_overlaps(
                    proposed_start=padded_start,
                    proposed_duration=duration_sec,
                    existing_recordings=active_timers,
                    max_simultaneous_streams=max_streams,
                )
                if ov["has_conflict"]:
                    names = ", ".join(
                        f"{c.get('name') or 'Recording'} ({c.get('channel') or 'Channel'})"
                        for c in ov["conflicts"][:3]
                    )
                    reason = (
                        f"Overlaps {names} "
                        f"({ov['peak_concurrent']}/{ov['max_simultaneous_streams']} streams)"
                    )
                    async with db.write_lock:
                        with db.session() as conn:
                            _upsert_history(
                                conn,
                                existing_history_id=cand["existing_history_id"],
                                rule_id=rid,
                                channel_id=rule["channel_id"],
                                program_title=prog["title"],
                                program_start_ts=prog["start_ts"],
                                program_end_ts=prog["end_ts"],
                                status="skipped_conflict",
                                uhf_recording_id=None,
                                reason=reason,
                            )
                    rule_stats[rid]["skipped_conflict"] += 1
                    total_skipped += 1
                    continue

            channel_dict = {
                "name": rule["channel_name"],
                "stream_url": rule["stream_url"],
                "logo_url": rule["logo_url"],
            }
            payload = build_uhf_recording_payload(
                name=prog["title"],
                channel=channel_dict,
                category_name=rule.get("category_name"),
                playlist_id=rule.get("source_playlist_id"),
                start_dt=padded_start,
                duration_seconds=duration_sec,
            )

            try:
                created = await client.create_recording(payload)
                # Ensure URL is present in working active_timers copy for subsequent overlap checks
                timer_copy = dict(created)
                timer_copy.setdefault("url", rule["stream_url"])
                timer_copy.setdefault("status", "scheduled")
                timer_copy.setdefault("start_time", payload["start_time"])
                timer_copy.setdefault("duration_seconds", duration_sec)
                active_timers.append(timer_copy)

                async with db.write_lock:
                    with db.session() as conn:
                        _upsert_history(
                            conn,
                            existing_history_id=cand["existing_history_id"],
                            rule_id=rid,
                            channel_id=rule["channel_id"],
                            program_title=prog["title"],
                            program_start_ts=prog["start_ts"],
                            program_end_ts=prog["end_ts"],
                            status="scheduled",
                            uhf_recording_id=created.get("id"),
                            reason=None,
                        )
                rule_stats[rid]["scheduled"] += 1
                total_scheduled += 1
            except UHFServerError as exc:
                async with db.write_lock:
                    with db.session() as conn:
                        _upsert_history(
                            conn,
                            existing_history_id=cand["existing_history_id"],
                            rule_id=rid,
                            channel_id=rule["channel_id"],
                            program_title=prog["title"],
                            program_start_ts=prog["start_ts"],
                            program_end_ts=prog["end_ts"],
                            status="error",
                            uhf_recording_id=None,
                            reason=exc.message,
                        )
                rule_stats[rid]["errors"] += 1
                total_errors += 1

        # Update rule run metadata
        async with db.write_lock:
            with db.session() as conn:
                for rid, st in rule_stats.items():
                    conn.execute(
                        """
                        UPDATE auto_record_rules
                        SET last_run_at = ?, last_scheduled_count = ?, last_skipped_count = ?
                        WHERE id = ?
                        """,
                        (now_iso, st["scheduled"], st["skipped_conflict"], rid),
                    )

        return {
            "evaluated_rules": len(rules),
            "scheduled": total_scheduled,
            "skipped_conflict": total_skipped,
            "errors": total_errors,
            "uhf_error": None,
        }


def _upsert_history(
    conn: Any,
    *,
    existing_history_id: int | None,
    rule_id: int,
    channel_id: int,
    program_title: str,
    program_start_ts: int,
    program_end_ts: int,
    status: str,
    uhf_recording_id: str | None,
    reason: str | None,
) -> None:
    if existing_history_id is not None:
        conn.execute(
            """
            UPDATE auto_record_history
            SET program_title = ?, program_start_ts = ?, program_end_ts = ?,
                status = ?, uhf_recording_id = ?, reason = ?, created_at = datetime('now')
            WHERE id = ?
            """,
            (
                program_title,
                program_start_ts,
                program_end_ts,
                status,
                uhf_recording_id,
                reason,
                existing_history_id,
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO auto_record_history
                (rule_id, channel_id, program_title, program_start_ts, program_end_ts, status, uhf_recording_id, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rule_id,
                channel_id,
                program_title,
                program_start_ts,
                program_end_ts,
                status,
                uhf_recording_id,
                reason,
            ),
        )


async def force_schedule_history_entry(db: Database, history_id: int) -> dict[str, Any]:
    """Manually schedule a skipped_conflict or error history entry bypassing conflict skip."""
    with db.session() as conn:
        row = conn.execute(
            """
            SELECT h.*,
                   r.pre_padding_min, r.post_padding_min,
                   ch.name AS channel_name, ch.stream_url, ch.logo_url,
                   cat.name AS category_name,
                   s.playlist_id AS source_playlist_id
            FROM auto_record_history h
            JOIN auto_record_rules r ON r.id = h.rule_id
            JOIN channels ch ON ch.id = h.channel_id
            JOIN iptv_sources s ON s.id = ch.source_id
            LEFT JOIN categories cat ON cat.id = ch.category_id
            WHERE h.id = ?
            """,
            (history_id,),
        ).fetchone()
    if not row:
        raise KeyError(f"History entry {history_id} not found.")
    entry = dict(row)

    now = datetime.now(timezone.utc)
    defaults = db.get_setting("defaults")
    pre_pad = entry["pre_padding_min"] if entry["pre_padding_min"] is not None else int(defaults.get("pre_padding_min", 2))
    post_pad = entry["post_padding_min"] if entry["post_padding_min"] is not None else int(defaults.get("post_padding_min", 5))

    raw_start = datetime.fromtimestamp(entry["program_start_ts"], tz=timezone.utc)
    raw_end = datetime.fromtimestamp(entry["program_end_ts"], tz=timezone.utc)
    padded_start = max(now, raw_start - timedelta(minutes=max(0, pre_pad)))
    padded_end = raw_end + timedelta(minutes=max(0, post_pad))
    duration_sec = int((padded_end - padded_start).total_seconds())
    if duration_sec <= 0:
        raise ValueError("This broadcast has already ended.")

    payload = build_uhf_recording_payload(
        name=entry["program_title"],
        channel={
            "name": entry["channel_name"],
            "stream_url": entry["stream_url"],
            "logo_url": entry["logo_url"],
        },
        category_name=entry.get("category_name"),
        playlist_id=entry.get("source_playlist_id"),
        start_dt=padded_start,
        duration_seconds=duration_sec,
    )

    client = UHFClient(db)
    created = await client.create_recording(payload)

    async with db.write_lock:
        with db.session() as conn:
            conn.execute(
                """
                UPDATE auto_record_history
                SET status = 'scheduled', uhf_recording_id = ?, reason = 'Force scheduled manually'
                WHERE id = ?
                """,
                (created.get("id"), history_id),
            )

    return {
        "ok": True,
        "history_id": history_id,
        "recording_id": created.get("id"),
        "name": created.get("name"),
        "start_time": created.get("start_time"),
        "duration_seconds": created.get("duration_seconds"),
    }
