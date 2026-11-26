from __future__ import annotations

import asyncio
import gzip
import io
import logging
import os
import tempfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO

import httpx

from .db import Database

logger = logging.getLogger(__name__)

_epg_locks: dict[int, asyncio.Lock] = {}

DEFAULT_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
}


def _format_exc(exc: Exception) -> str:
    msg = str(exc).strip()
    return f"{type(exc).__name__}: {msg}" if msg else type(exc).__name__


def _get_epg_lock(epg_source_id: int) -> asyncio.Lock:
    lock = _epg_locks.get(epg_source_id)
    if lock is None:
        lock = asyncio.Lock()
        _epg_locks[epg_source_id] = lock
    return lock


def parse_xmltv_timestamp(raw: str | None) -> int | None:
    """Parse XMLTV timestamp like '20261125200000 +0100' or '20261125200000' into UTC epoch seconds."""
    if not raw:
        return None
    s = raw.strip()
    if not s:
        return None
    parts = s.split()
    dt_part = parts[0]
    tz_part = parts[1] if len(parts) > 1 else "+0000"

    # Pad short timestamps (YYYYMMDDHHMM -> YYYYMMDDHHMM00)
    if len(dt_part) == 12:
        dt_part += "00"
    if len(dt_part) < 14:
        return None
    dt_part = dt_part[:14]

    try:
        naive = datetime.strptime(dt_part, "%Y%m%d%H%M%S")
    except ValueError:
        return None

    tz_part = tz_part.upper()
    if tz_part in ("Z", "UTC", "GMT"):
        tz = timezone.utc
    else:
        sign = 1
        if tz_part.startswith("-"):
            sign = -1
            tz_digits = tz_part[1:]
        elif tz_part.startswith("+"):
            tz_digits = tz_part[1:]
        else:
            tz_digits = tz_part
        tz_digits = tz_digits.replace(":", "")
        try:
            hours = int(tz_digits[:2]) if len(tz_digits) >= 2 else 0
            mins = int(tz_digits[2:4]) if len(tz_digits) >= 4 else 0
            tz = timezone(timedelta(seconds=sign * (hours * 3600 + mins * 60)))
        except ValueError:
            tz = timezone.utc

    return int(naive.replace(tzinfo=tz).timestamp())


def _open_maybe_gzip(file_path: Path) -> BinaryIO:
    with open(file_path, "rb") as probe:
        magic = probe.read(2)
    if magic == b"\x1f\x8b":
        return gzip.open(file_path, "rb")
    return open(file_path, "rb")


def ingest_xmltv_stream(
    db: Database,
    epg_source_id: int,
    stream: BinaryIO,
    retention_hours: int = 24,
    batch_size: int = 2000,
) -> tuple[int, int]:
    """Stream-parse XMLTV from a binary stream into SQLite with bounded memory.

    Returns (channel_count, program_count).
    """
    cutoff_ts = int(datetime.now(timezone.utc).timestamp()) - retention_hours * 3600

    channel_batch: list[tuple[Any, ...]] = []
    program_batch: list[tuple[Any, ...]] = []
    channel_count = 0
    program_count = 0

    with db.session() as conn:
        conn.execute("DELETE FROM epg_programs WHERE epg_source_id = ?", (epg_source_id,))
        conn.execute("DELETE FROM epg_channels WHERE epg_source_id = ?", (epg_source_id,))

        def flush_channels() -> None:
            nonlocal channel_batch
            if channel_batch:
                conn.executemany(
                    """
                    INSERT OR REPLACE INTO epg_channels
                        (epg_source_id, xmltv_id, xmltv_id_lower, display_name, display_name_lower, icon_url)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    channel_batch,
                )
                channel_batch = []

        def flush_programs() -> None:
            nonlocal program_batch
            if program_batch:
                conn.executemany(
                    """
                    INSERT INTO epg_programs
                        (epg_source_id, xmltv_id_lower, start_ts, end_ts, title, description, category, icon_url)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    program_batch,
                )
                program_batch = []

        for _event, elem in ET.iterparse(stream, events=("end",)):
            tag = elem.tag
            if "}" in tag:
                tag = tag.rsplit("}", 1)[-1]

            if tag == "channel":
                xmltv_id = (elem.attrib.get("id") or "").strip()
                if xmltv_id:
                    dn_elem = elem.find("display-name")
                    display_name = (dn_elem.text or "").strip() if dn_elem is not None else xmltv_id
                    if not display_name:
                        display_name = xmltv_id
                    icon_elem = elem.find("icon")
                    icon_url = icon_elem.attrib.get("src") if icon_elem is not None else None
                    channel_batch.append(
                        (
                            epg_source_id,
                            xmltv_id,
                            xmltv_id.lower(),
                            display_name,
                            display_name.lower(),
                            icon_url,
                        )
                    )
                    channel_count += 1
                    if len(channel_batch) >= batch_size:
                        flush_channels()
                elem.clear()

            elif tag == "programme":
                ch_id = (elem.attrib.get("channel") or "").strip()
                start_ts = parse_xmltv_timestamp(elem.attrib.get("start"))
                end_ts = parse_xmltv_timestamp(elem.attrib.get("stop"))
                if ch_id and start_ts is not None and end_ts is not None and end_ts > start_ts:
                    if end_ts >= cutoff_ts:
                        title_elem = elem.find("title")
                        title = (title_elem.text or "").strip() if title_elem is not None else "Untitled"
                        if not title:
                            title = "Untitled"
                        desc_elem = elem.find("desc")
                        desc = (desc_elem.text or "").strip() if desc_elem is not None else None
                        cat_elem = elem.find("category")
                        cat = (cat_elem.text or "").strip() if cat_elem is not None else None
                        icon_elem = elem.find("icon")
                        icon_url = icon_elem.attrib.get("src") if icon_elem is not None else None
                        program_batch.append(
                            (
                                epg_source_id,
                                ch_id.lower(),
                                start_ts,
                                end_ts,
                                title,
                                desc,
                                cat,
                                icon_url,
                            )
                        )
                        program_count += 1
                        if len(program_batch) >= batch_size:
                            flush_programs()
                elem.clear()

        flush_channels()
        flush_programs()
        # Prune any expired programs across all sources
        conn.execute("DELETE FROM epg_programs WHERE end_ts < ?", (cutoff_ts,))

    return channel_count, program_count


def _resolve_epg_url(db: Database, epg_source: dict[str, Any]) -> str:
    if epg_source["type"] == "xmltv_url":
        url = (epg_source.get("url") or "").strip()
        if not url:
            raise ValueError("XMLTV source URL is empty.")
        return url
    if epg_source["type"] == "xtream_auto":
        iptv_id = epg_source.get("iptv_source_id")
        with db.session() as conn:
            row = conn.execute("SELECT * FROM iptv_sources WHERE id = ?", (iptv_id,)).fetchone()
        if not row:
            raise ValueError("Linked Xtream IPTV source no longer exists.")
        base = row["url"].rstrip("/")
        return f"{base}/xmltv.php?username={row['username']}&password={row['password']}"
    raise ValueError(f"Unsupported EPG source type: {epg_source['type']}")


async def sync_epg_source(db: Database, epg_source_id: int, timeout: float = 180.0) -> dict[str, Any]:
    lock = _get_epg_lock(epg_source_id)
    async with lock:
        with db.session() as conn:
            row = conn.execute("SELECT * FROM epg_sources WHERE id = ?", (epg_source_id,)).fetchone()
            if not row:
                raise KeyError(f"EPG source {epg_source_id} not found")
            epg_source = dict(row)

        defaults = db.get_setting("defaults")
        retention_hours = int(defaults.get("epg_retention_hours", 24))
        now_iso = datetime.now(timezone.utc).isoformat()

        tmp_path: Path | None = None
        try:
            feed_url = _resolve_epg_url(db, epg_source)
            fd, tmp_name = tempfile.mkstemp(prefix="uhf_epg_", suffix=".xml")
            os.close(fd)
            tmp_path = Path(tmp_name)

            async with httpx.AsyncClient(
                timeout=httpx.Timeout(connect=20.0, read=timeout, write=30.0, pool=30.0),
                follow_redirects=True,
                headers=DEFAULT_HTTP_HEADERS,
            ) as client:
                async with client.stream("GET", feed_url) as resp:
                    resp.raise_for_status()
                    with open(tmp_path, "wb") as out:
                        async for chunk in resp.aiter_bytes(chunk_size=65536):
                            out.write(chunk)

            # Parse & ingest inside write lock using a worker thread so event loop stays responsive
            async with db.write_lock:
                def _run_parse() -> tuple[int, int]:
                    with _open_maybe_gzip(tmp_path) as stream:
                        return ingest_xmltv_stream(db, epg_source_id, stream, retention_hours=retention_hours)

                ch_count, prog_count = await asyncio.to_thread(_run_parse)
                with db.session() as conn:
                    conn.execute(
                        """
                        UPDATE epg_sources
                        SET last_synced_at = ?, last_error = NULL, program_count = ?
                        WHERE id = ?
                        """,
                        (now_iso, prog_count, epg_source_id),
                    )

            return {
                "epg_source_id": epg_source_id,
                "ok": True,
                "channel_count": ch_count,
                "program_count": prog_count,
                "last_synced_at": now_iso,
            }
        except Exception as exc:
            err_msg = _format_exc(exc)
            logger.warning("EPG sync failed for source %s: %s", epg_source_id, err_msg)
            async with db.write_lock:
                with db.session() as conn:
                    conn.execute(
                        "UPDATE epg_sources SET last_error = ? WHERE id = ?",
                        (err_msg, epg_source_id),
                    )
            return {
                "epg_source_id": epg_source_id,
                "ok": False,
                "error": err_msg,
                "program_count": epg_source["program_count"],
            }
        finally:
            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass


async def sync_all_enabled_epg_sources(db: Database) -> list[dict[str, Any]]:
    with db.session() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM epg_sources WHERE enabled = 1")]
    results = []
    for eid in ids:
        results.append(await sync_epg_source(db, eid))
    return results
