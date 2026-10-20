from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import ROOT, Settings, get_settings
from app.db import connect
from app.routers.api import router
from app.services.briefing import list_user_ids, stream_briefing

logger = logging.getLogger("kuafu")


def seconds_until(hour: int) -> float:
    now = datetime.now()
    target = now.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max((target - now).total_seconds(), 1)


async def daily_loop(settings: Settings) -> None:
    assert settings.daily_hour is not None
    while True:
        delay = seconds_until(settings.daily_hour)
        logger.info("下一轮每日简报在 %.0f 秒后", delay)
        await asyncio.sleep(delay)
        for user_id in list_user_ids():
            logger.info("开始为用户 %s 生成简报", user_id)
            async for event in stream_briefing(settings, user_id):
                if event["type"] in {"saved", "error"}:
                    logger.info("用户 %s 简报结束: %s", user_id, event["type"])
        await asyncio.sleep(61)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        connect(settings.database_path, settings.schema_path)
        task = None
        if settings.daily_hour is not None:
            task = asyncio.create_task(daily_loop(settings))
        yield
        if task is not None:
            task.cancel()

    app = FastAPI(title="KUAFU 每日 AI 新闻助手", lifespan=lifespan)
    app.state.settings = settings
    app.include_router(router)

    frontend = ROOT / "frontend"
    if frontend.exists():
        app.mount("/static", StaticFiles(directory=frontend), name="static")

        @app.get("/")
        def index():
            return FileResponse(Path(frontend / "index.html"))

    return app


app = create_app()
