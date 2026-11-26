from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .auto_record_service import evaluate_rules, is_sync_due
from .config import APP_NAME, APP_VERSION, BUILD_TIMESTAMP, get_frontend_dist_dir
from .db import get_db
from .epg_sync import sync_epg_source
from .iptv_sync import sync_iptv_source
from .routers import channels, epg, recordings, rules, settings, sources
from .update_checker import check_for_updates

logger = logging.getLogger(__name__)


async def run_due_syncs_and_rules(db=None) -> dict[str, int]:
    """Check enabled IPTV & EPG sources against sync_interval_hours; sync due ones and run rules."""
    db = db or get_db()
    defaults = db.get_setting("defaults")
    interval_hours = max(1, int(defaults.get("sync_interval_hours", 12)))

    with db.session() as conn:
        iptv_rows = [
            dict(r)
            for r in conn.execute("SELECT id, last_synced_at FROM iptv_sources WHERE enabled = 1")
        ]
        epg_rows = [
            dict(r)
            for r in conn.execute("SELECT id, last_synced_at FROM epg_sources WHERE enabled = 1")
        ]

    iptv_synced = 0
    for row in iptv_rows:
        if is_sync_due(row["last_synced_at"], interval_hours):
            await sync_iptv_source(db, row["id"])
            iptv_synced += 1

    epg_synced = 0
    for row in epg_rows:
        if is_sync_due(row["last_synced_at"], interval_hours):
            res = await sync_epg_source(db, row["id"])
            if res.get("ok"):
                epg_synced += 1

    rules_evaluated = 0
    if epg_synced > 0:
        summary = await evaluate_rules(db)
        rules_evaluated = int(summary.get("evaluated_rules", 0))

    return {
        "iptv_synced": iptv_synced,
        "epg_synced": epg_synced,
        "rules_evaluated": rules_evaluated,
    }


async def _background_sync_loop() -> None:
    """Elapsed-time-aware sync loop resilient across restarts (checks every 15 minutes)."""
    await asyncio.sleep(60)
    while True:
        try:
            await run_due_syncs_and_rules(get_db())
        except Exception:
            logger.exception("Background sync loop encountered an error")
        await asyncio.sleep(15 * 60)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    db = get_db()
    db.init_schema()
    sync_task = asyncio.create_task(_background_sync_loop())
    try:
        yield
    finally:
        sync_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sync_task


def create_app() -> FastAPI:
    app = FastAPI(
        title="UHF Scheduler",
        version=APP_VERSION,
        lifespan=lifespan,
    )

    @app.get("/api/health", tags=["system"])
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "app": APP_NAME,
            "version": APP_VERSION,
            "build_timestamp": BUILD_TIMESTAMP,
        }

    @app.get("/api/system/update-check", tags=["system"])
    async def system_update_check(force: bool = False) -> dict[str, object]:
        return await check_for_updates(force=force)

    app.include_router(settings.router)
    app.include_router(sources.router)
    app.include_router(channels.router)
    app.include_router(epg.router)
    app.include_router(recordings.router)
    app.include_router(rules.router)

    dist_dir = get_frontend_dist_dir()
    assets_dir = dist_dir / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found")
        dist = get_frontend_dist_dir()
        if full_path:
            candidate = (dist / full_path).resolve()
            if candidate.is_file() and str(candidate).startswith(str(dist.resolve())):
                return FileResponse(candidate)
        index_html = dist / "index.html"
        if index_html.is_file():
            return FileResponse(index_html)
        raise HTTPException(
            status_code=404,
            detail="Frontend build not found. Run 'npm run build' in frontend/.",
        )

    return app


app = create_app()
