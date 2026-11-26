from __future__ import annotations

import re
import time
from typing import Any

import httpx

from .config import APP_VERSION

GITHUB_REPO = "marcboy/uhf-scheduler"
GITHUB_LATEST_RELEASE_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
CACHE_TTL_SECONDS = 6 * 3600

_cache_ts: float = 0.0
_cache_result: dict[str, Any] | None = None


def parse_semver(raw: str | None) -> tuple[int, int, int] | None:
    if not raw:
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", raw.strip())
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def is_newer_version(latest: str | None, current: str = APP_VERSION) -> bool:
    lat_t = parse_semver(latest)
    cur_t = parse_semver(current)
    if lat_t is None or cur_t is None:
        return False
    return lat_t > cur_t


def reset_update_cache() -> None:
    global _cache_ts, _cache_result
    _cache_ts = 0.0
    _cache_result = None


async def check_for_updates(force: bool = False) -> dict[str, Any]:
    """Check GitHub latest release with 6-hour cache and silent offline fallback."""
    global _cache_ts, _cache_result
    now = time.monotonic()
    if not force and _cache_result is not None and (now - _cache_ts) < CACHE_TTL_SECONDS:
        return dict(_cache_result)

    base_result: dict[str, Any] = {
        "current_version": APP_VERSION,
        "latest_version": None,
        "update_available": False,
        "release_url": f"https://github.com/{GITHUB_REPO}/releases",
        "release_notes": None,
        "update_command": "docker compose pull && docker compose up -d",
        "checked_online": False,
    }

    try:
        async with httpx.AsyncClient(
            timeout=5.0,
            follow_redirects=True,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": f"uhf-scheduler/{APP_VERSION}",
            },
        ) as client:
            resp = await client.get(GITHUB_LATEST_RELEASE_URL)
            if resp.status_code == 200:
                data = resp.json()
                tag = str(data.get("tag_name") or "").strip()
                latest_clean = tag.lstrip("vV") if tag else None
                base_result["latest_version"] = latest_clean
                base_result["update_available"] = is_newer_version(latest_clean, APP_VERSION)
                if data.get("html_url"):
                    base_result["release_url"] = str(data["html_url"])
                if data.get("body"):
                    base_result["release_notes"] = str(data["body"])[:600]
                base_result["checked_online"] = True
    except Exception:
        # Fail silently when offline or rate-limited
        pass

    _cache_ts = now
    _cache_result = dict(base_result)
    return base_result
