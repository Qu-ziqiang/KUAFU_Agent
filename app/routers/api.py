from __future__ import annotations

import json

from fastapi import APIRouter, Body, Request
from fastapi.responses import StreamingResponse

from app.schemas import PreferenceIn, UserCreate
from app.services import briefing as briefing_service

router = APIRouter(prefix="/api")


def ok(data):
    return {"code": 0, "message": "ok", "data": data}


def fail(message: str, code: int = 40001, status: int = 400):
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=status, content={"code": code, "message": message, "data": None})


@router.get("/health")
def health(request: Request):
    settings = request.app.state.settings
    return ok(
        {
            "llm_ready": settings.llm_ready,
            "search_ready": settings.search_ready,
            "model": settings.openai_model,
            "search_backend": "tavily" if settings.search_ready else "",
            "daily_hour": settings.daily_hour,
            "search_max_results": settings.search_max_results,
            "search_max_rounds": settings.search_max_rounds,
            "first_ready_seconds": settings.first_ready_seconds,
            "total_timeout_seconds": settings.total_timeout_seconds,
            "preferred_domains": settings.preferred_domains,
        }
    )


@router.post("/users")
def create_user(body: UserCreate):
    user = briefing_service.create_user(body.name, body.email)
    return ok(user)


@router.get("/users/{user_id}")
def get_user(user_id: str):
    user = briefing_service.get_user(user_id)
    if user is None:
        return fail("用户不存在", code=40401, status=404)
    return ok(user)


@router.put("/users/{user_id}/preferences")
def update_preferences(user_id: str, body: PreferenceIn, request: Request):
    if briefing_service.get_user(user_id) is None:
        return fail("用户不存在", code=40401, status=404)
    user = briefing_service.save_preferences(
        user_id,
        body.topics,
        body.keywords,
        body.language,
        request.app.state.settings,
        body.recall_count,
        body.custom_prompt,
    )
    return ok(user)


@router.get("/users/{user_id}/briefings")
def list_briefings(user_id: str):
    if briefing_service.get_user(user_id) is None:
        return fail("用户不存在", code=40401, status=404)
    return ok(briefing_service.list_briefings(user_id))


@router.get("/briefings/{briefing_id}")
def get_briefing(briefing_id: int):
    briefing = briefing_service.get_briefing(briefing_id)
    if briefing is None:
        return fail("简报不存在", code=40402, status=404)
    return ok(briefing)


@router.post("/users/{user_id}/briefings/stream")
async def stream_briefing(
    user_id: str,
    request: Request,
    body: PreferenceIn | None = Body(default=None),
):
    settings = request.app.state.settings
    if body is not None:
        if briefing_service.get_user(user_id) is None:
            return fail("用户不存在", code=40401, status=404)
        briefing_service.save_preferences(
            user_id,
            body.topics,
            body.keywords,
            body.language,
            settings,
            body.recall_count,
            body.custom_prompt,
        )

    async def events():
        async for event in briefing_service.stream_briefing(settings, user_id):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")
