from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

from .db import Database

logger = logging.getLogger(__name__)

_source_locks: dict[int, asyncio.Lock] = {}

EXTINF_ATTR_RE = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')

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


def _get_lock(source_id: int) -> asyncio.Lock:
    lock = _source_locks.get(source_id)
    if lock is None:
        lock = asyncio.Lock()
        _source_locks[source_id] = lock
    return lock


def parse_m3u_content(text: str) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    """Parse M3U / M3U8 text into (categories, channels)."""
    categories_map: dict[str, str] = {}
    channels: list[dict[str, Any]] = []
    current_meta: dict[str, Any] | None = None
    seq = 0

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            attrs = dict(EXTINF_ATTR_RE.findall(line))
            # Channel display name is after the last comma
            comma_idx = line.rfind(",")
            display_name = line[comma_idx + 1 :].strip() if comma_idx != -1 else ""
            if not display_name:
                display_name = attrs.get("tvg-name") or attrs.get("tvg-id") or "Unnamed Channel"
            group_title = (attrs.get("group-title") or "Uncategorized").strip() or "Uncategorized"
            categories_map[group_title] = group_title
            current_meta = {
                "name": display_name,
                "category_ext_id": group_title,
                "logo_url": attrs.get("tvg-logo") or None,
                "epg_channel_id": (attrs.get("tvg-id") or "").strip() or None,
                "tvg_name": attrs.get("tvg-name") or None,
            }
        elif line.startswith("#"):
            continue
        else:
            if current_meta is not None:
                seq += 1
                ext_id = current_meta.get("epg_channel_id") or f"m3u:{seq}:{line}"
                # Ensure unique external_id per stream URL if epg_channel_id is duplicated
                ext_id = f"{ext_id}|{line}" if current_meta.get("epg_channel_id") else f"m3u:{line}"
                channels.append(
                    {
                        "external_id": ext_id,
                        "category_ext_id": current_meta["category_ext_id"],
                        "name": current_meta["name"],
                        "stream_url": line,
                        "logo_url": current_meta["logo_url"],
                        "epg_channel_id": current_meta["epg_channel_id"],
                    }
                )
                current_meta = None

    categories = [(cid, cname) for cid, cname in categories_map.items()]
    return categories, channels


def build_xtream_stream_url(base_url: str, username: str, password: str, stream_id: Any) -> str:
    clean_base = base_url.rstrip("/")
    return f"{clean_base}/live/{username}/{password}/{stream_id}.ts"


async def _fetch_xtream(
    url: str, username: str, password: str, timeout: float = 120.0
) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    clean_base = url.rstrip("/")
    api_url = f"{clean_base}/player_api.php"
    params_base = {"username": username, "password": password}

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=20.0, read=timeout, write=30.0, pool=30.0),
        follow_redirects=True,
        headers=DEFAULT_HTTP_HEADERS,
    ) as client:
        cat_resp = await client.get(api_url, params={**params_base, "action": "get_live_categories"})
        cat_resp.raise_for_status()
        cat_data = cat_resp.json()
        if not isinstance(cat_data, list):
            raise ValueError("Xtream get_live_categories did not return a JSON list.")

        streams_resp = await client.get(api_url, params={**params_base, "action": "get_live_streams"})
        streams_resp.raise_for_status()
        streams_data = streams_resp.json()
        if not isinstance(streams_data, list):
            raise ValueError("Xtream get_live_streams did not return a JSON list.")

    categories: list[tuple[str, str]] = []
    seen_cats: set[str] = set()
    for c in cat_data:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("category_id") or "").strip()
        cname = str(c.get("category_name") or "Uncategorized").strip() or "Uncategorized"
        if cid and cid not in seen_cats:
            seen_cats.add(cid)
            categories.append((cid, cname))

    channels: list[dict[str, Any]] = []
    for s in streams_data:
        if not isinstance(s, dict):
            continue
        sid = s.get("stream_id")
        if sid is None:
            continue
        cid = str(s.get("category_id") or "uncategorized").strip() or "uncategorized"
        if cid not in seen_cats:
            seen_cats.add(cid)
            categories.append((cid, "Uncategorized"))
        name = str(s.get("name") or f"Channel {sid}").strip()
        epg_id = (s.get("epg_channel_id") or "").strip() or None
        logo = (s.get("stream_icon") or "").strip() or None
        channels.append(
            {
                "external_id": str(sid),
                "category_ext_id": cid,
                "name": name,
                "stream_url": build_xtream_stream_url(clean_base, username, password, sid),
                "logo_url": logo,
                "epg_channel_id": epg_id,
            }
        )

    return categories, channels


async def _fetch_m3u(url: str, timeout: float = 120.0) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=20.0, read=timeout, write=30.0, pool=30.0),
        follow_redirects=True,
        headers=DEFAULT_HTTP_HEADERS,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        text = resp.text
    return parse_m3u_content(text)


async def sync_iptv_source(db: Database, source_id: int) -> dict[str, Any]:
    """Fetch and upsert categories & channels for a single IPTV source.

    Preserves existing favorites, hidden category flags, and manual EPG overrides.
    On fetch/parse error, records last_error and preserves prior catalog intact.
    """
    lock = _get_lock(source_id)
    async with lock:
        with db.session() as conn:
            src = conn.execute("SELECT * FROM iptv_sources WHERE id = ?", (source_id,)).fetchone()
            if not src:
                raise KeyError(f"IPTV source {source_id} not found")
            source = dict(src)

        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            if source["type"] == "xtream":
                if not source.get("username") or not source.get("password"):
                    raise ValueError("Xtream source requires username and password.")
                categories, channels = await _fetch_xtream(
                    source["url"], source["username"], source["password"]
                )
            elif source["type"] == "m3u":
                categories, channels = await _fetch_m3u(source["url"])
            else:
                raise ValueError(f"Unsupported source type: {source['type']}")
        except Exception as exc:
            err_msg = _format_exc(exc)
            logger.warning("IPTV sync failed for source %s (%s): %s", source_id, source["name"], err_msg)
            async with db.write_lock:
                with db.session() as conn:
                    conn.execute(
                        "UPDATE iptv_sources SET last_error = ? WHERE id = ?",
                        (err_msg, source_id),
                    )
            return {
                "source_id": source_id,
                "ok": False,
                "error": err_msg,
                "channel_count": source["channel_count"],
            }

        async with db.write_lock:
            with db.session() as conn:
                # Upsert categories preserving hidden flag
                cat_ext_to_id: dict[str, int] = {}
                seen_cat_exts: set[str] = set()
                for ext_id, cname in categories:
                    seen_cat_exts.add(ext_id)
                    conn.execute(
                        """
                        INSERT INTO categories (source_id, external_id, name)
                        VALUES (?, ?, ?)
                        ON CONFLICT(source_id, external_id) DO UPDATE SET name = excluded.name
                        """,
                        (source_id, ext_id, cname),
                    )

                for row in conn.execute(
                    "SELECT id, external_id FROM categories WHERE source_id = ?", (source_id,)
                ):
                    cat_ext_to_id[row["external_id"]] = row["id"]

                # Upsert channels preserving is_favorite & epg_override_id
                seen_ch_exts: set[str] = set()
                batch: list[tuple[Any, ...]] = []
                for ch in channels:
                    ext_id = ch["external_id"]
                    if ext_id in seen_ch_exts:
                        continue
                    seen_ch_exts.add(ext_id)
                    cat_id = cat_ext_to_id.get(ch["category_ext_id"])
                    epg_id = ch.get("epg_channel_id")
                    batch.append(
                        (
                            source_id,
                            cat_id,
                            ext_id,
                            ch["name"],
                            ch["name"].lower(),
                            ch["stream_url"],
                            ch.get("logo_url"),
                            epg_id,
                            epg_id.lower() if epg_id else None,
                        )
                    )

                conn.executemany(
                    """
                    INSERT INTO channels (
                        source_id, category_id, external_id, name, name_lower,
                        stream_url, logo_url, epg_channel_id, epg_channel_id_lower
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source_id, external_id) DO UPDATE SET
                        category_id = excluded.category_id,
                        name = excluded.name,
                        name_lower = excluded.name_lower,
                        stream_url = excluded.stream_url,
                        logo_url = excluded.logo_url,
                        epg_channel_id = excluded.epg_channel_id,
                        epg_channel_id_lower = excluded.epg_channel_id_lower
                    """,
                    batch,
                )

                # Remove channels that disappeared from provider (only after successful fetch)
                existing_exts = [
                    r["external_id"]
                    for r in conn.execute(
                        "SELECT external_id FROM channels WHERE source_id = ?", (source_id,)
                    )
                ]
                stale_exts = [e for e in existing_exts if e not in seen_ch_exts]
                for i in range(0, len(stale_exts), 500):
                    chunk = stale_exts[i : i + 500]
                    placeholders = ",".join("?" for _ in chunk)
                    conn.execute(
                        f"DELETE FROM channels WHERE source_id = ? AND external_id IN ({placeholders})",
                        [source_id, *chunk],
                    )

                # Remove stale categories
                stale_cats = [e for e in cat_ext_to_id if e not in seen_cat_exts]
                for i in range(0, len(stale_cats), 500):
                    chunk = stale_cats[i : i + 500]
                    placeholders = ",".join("?" for _ in chunk)
                    conn.execute(
                        f"DELETE FROM categories WHERE source_id = ? AND external_id IN ({placeholders})",
                        [source_id, *chunk],
                    )

                conn.execute(
                    """
                    UPDATE iptv_sources
                    SET last_synced_at = ?, last_error = NULL, channel_count = ?
                    WHERE id = ?
                    """,
                    (now_iso, len(batch), source_id),
                )

        return {
            "source_id": source_id,
            "ok": True,
            "channel_count": len(batch),
            "category_count": len(categories),
            "last_synced_at": now_iso,
        }


async def sync_all_enabled_iptv_sources(db: Database) -> list[dict[str, Any]]:
    with db.session() as conn:
        ids = [r["id"] for r in conn.execute("SELECT id FROM iptv_sources WHERE enabled = 1")]
    results = []
    for sid in ids:
        results.append(await sync_iptv_source(db, sid))
    return results
