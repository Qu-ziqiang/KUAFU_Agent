"""跑一轮简报图：边搜边总结，一条写完再写下一条，总览放最后。"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from datetime import datetime

from langgraph.errors import GraphRecursionError

from app.agent.graph import BriefingState, ChatFn, build_graph, last_assistant_text
from app.agent.llm import LLMError
from app.agent.constraints import emphasize_keywords, emphasize_numbers, emphasize_title, matches_geo
from app.agent.prompts import briefing_language, briefing_title, custom_clause, item_polish_prompt, overview_prompt
from app.agent.tools import NewsTools, plain_text

__all__ = ["ChatFn", "run_react"]


def _render_growing(title: str, items: list[dict], overview: str = "", terms: list[str] | None = None) -> str:
    keywords = terms or []
    lines = [title, ""]
    for item in items:
        heading = emphasize_title(plain_text(item.get("title") or ""))
        summary = emphasize_keywords(emphasize_numbers(plain_text(item.get("summary") or "")), keywords) or "摘要整理中"
        lines.append(f"- {heading}：{summary} [打开原文]({item.get('url') or ''})")
    if overview:
        lines.extend(["", emphasize_keywords(emphasize_numbers(plain_text(overview)), keywords)])
    return "\n".join(lines).strip()


def _emit_progress(tools: NewsTools, title: str) -> list[dict]:
    """检索阶段只推状态，不把候选来源甩进页面，避免六七条和三条之间闪烁。"""
    events = []
    if tools.last_new_items:
        tools.last_new_items = []
        events.append({"type": "status", "message": "正在联网检索候选来源…"})
    return events


async def _polish_item(
    *,
    chat: ChatFn,
    language: str,
    custom: str,
    item: dict,
    geo: str | None,
    tools: NewsTools | None = None,
    focus: str = "",
) -> dict | None:
    if not matches_geo(item.get("url") or "", item.get("title") or "", item.get("summary") or "", geo):
        return None
    try:
        message = await chat(
            [{"role": "user", "content": item_polish_prompt(language, item, custom, focus)}],
            None,
        )
        raw = str(message.content or "").replace("\r\n", "\n").strip()
        if raw.upper().startswith("SKIP"):
            return None
        lines = [line.strip() for line in raw.split("\n") if line.strip()]
        item_title = lines[0] if lines else item.get("title") or ""
        summary = " ".join(lines[1:]) if len(lines) > 1 else (item.get("summary") or "")
    except (LLMError, Exception):
        item_title = item.get("title") or ""
        summary = item.get("summary") or ""
    item_title = plain_text(item_title).replace("*", "").replace("#", "").strip()[:140]
    summary = plain_text(summary).replace("*", "")[:560]
    if not matches_geo(item.get("url") or "", item_title, summary, geo):
        return None
    # 改写后再验相关性：防止搜索摘要里夹带查询词、正文其实是无关新闻。
    if tools is not None and not tools.on_topic(item_title, summary):
        return None
    return {
        "title": item_title or item.get("title") or "未命名",
        "url": item["url"],
        "source": item.get("source"),
        "published_at": item.get("published_at"),
        "summary": summary or "摘要整理中",
        "ocr": bool(item.get("ocr")),
        "ocr_source_type": item.get("ocr_source_type"),
        "credibility": item.get("credibility"),
    }


async def _progressive_compose(
    *,
    chat: ChatFn,
    tools: NewsTools,
    prefs: dict,
    title: str,
    query: str,
    started: float,
    seed_items: list[dict] | None = None,
) -> AsyncIterator[dict]:
    """逐条改写；被丢掉的条目由监督层补搜，不指望模型自己再开一轮。"""
    from app.agent.tools import ToolError

    language = briefing_language(prefs)
    custom = custom_clause(prefs)
    focus = "、".join(tools.focus_terms())
    polished: list[dict] = list(seed_items or [])
    processed: set[str] = {str(item.get("url") or "") for item in polished if item.get("url")}
    geo = tools.options.geo
    target = tools.options.max_items
    first_ready_sent = bool(polished)

    async def polish_batch(candidates: list[dict]) -> AsyncIterator[dict]:
        nonlocal first_ready_sent
        for item in candidates:
            if len(polished) >= target:
                return
            url = item.get("url") or ""
            if not url or url in processed:
                continue
            processed.add(url)
            progress = f"正在整理第 {len(polished) + 1}/{target} 条…"
            yield {"type": "status", "message": progress}
            # 先刷新右侧，避免等模型时页面像卡死。
            yield {
                "type": "sources_update",
                "items": list(polished),
                "content": (_render_growing(title, polished, terms=tools.focus_terms()) + f"\n\n{progress}").strip(),
                "title": title,
                "stable": True,
            }
            polished_item = await _polish_item(
                chat=chat,
                language=language,
                custom=custom,
                item=item,
                geo=geo,
                tools=tools,
                focus=focus,
            )
            if polished_item is None:
                yield {"type": "status", "message": f"丢掉 1 条不符合要求的结果，当前 {len(polished)}/{target}"}
                continue
            polished.append(polished_item)
            tools.remember(url, title=polished_item["title"], summary=polished_item["summary"])
            content = _render_growing(title, polished, terms=tools.focus_terms())
            yield {"type": "item_draft", "item": polished_item, "phase": "summary", "index": len(polished)}
            # 只推已采纳条目，上面正文、下面引用来源一起稳定增长。
            yield {
                "type": "sources_update",
                "items": list(polished),
                "content": content,
                "title": title,
                "stable": True,
            }
            if not first_ready_sent:
                first_ready_sent = True
                yield {
                    "type": "first_ready",
                    "title": title,
                    "content": content,
                    "items": list(polished),
                    "query": "；".join(tools.queries) or query,
                    "status": "partial",
                    "elapsed_seconds": round(time.monotonic() - started, 1),
                    "recall_complete": False,
                }

    async for event in polish_batch(tools.draft_items(limit=40)):
        yield event

    # 改写丢掉后，在「条数一半」的轮次预算内继续补搜，不额外突破上限。
    empty_refills = 0
    while len(polished) < target and tools.search_rounds < tools.options.max_rounds:
        need = target - len(polished)
        yield {"type": "status", "message": f"相关条目不足，正在补搜（还差 {need} 条）…"}
        terms = " ".join(tools.focus_terms()) or query
        try:
            tools.web_search(f"{terms} latest news update {tools.search_rounds + 1}")
        except ToolError:
            break
        # 补搜只更新状态，不要把新候选整批刷进引用区。
        for event in _emit_progress(tools, title):
            yield event
        fresh = [
            item
            for item in tools.draft_items(limit=40)
            if (item.get("url") or "") not in processed
        ]
        if not fresh:
            empty_refills += 1
            if empty_refills >= 2:
                break
            continue
        empty_refills = 0
        before = len(polished)
        async for event in polish_batch(fresh):
            yield event
        if len(polished) == before:
            empty_refills += 1
            if empty_refills >= 2:
                break

    overview = ""
    shortfall = ""
    if len(polished) < target:
        shortfall = (
            f"目标检索 {target} 条，实际只找到 {len(polished)} 条相关资讯"
            f"（已搜索 {tools.search_rounds}/{tools.options.max_rounds} 轮）。"
            "相关结果不够，没有用无关新闻凑数。"
        )
        yield {"type": "status", "message": shortfall}
    if polished:
        yield {"type": "status", "message": "正在写收尾总览…"}
        try:
            message = await chat(
                [{"role": "user", "content": overview_prompt(language, polished, custom)}],
                None,
            )
            overview = plain_text(message.content or "")
        except (LLMError, Exception):
            overview = "以上为本次检索整理的相关报道。"
        if shortfall:
            overview = f"{overview}\n\n{shortfall}".strip() if overview else shortfall
        tools.compose_briefing(title, overview, polished, allow_short=True)
    else:
        shortfall = (
            f"目标检索 {target} 条，没有找到相关资讯"
            f"（已搜索 {tools.search_rounds}/{tools.options.max_rounds} 轮）。"
        )
        tools.auto_compose_from_seen(title, shortfall)
        yield {"type": "status", "message": shortfall}

    composed = tools.composed or {}
    final_items = composed.get("items") or polished
    yield {
        "type": "first_ready",
        "title": title,
        "content": composed.get("content") or _render_growing(title, polished, overview or shortfall),
        "items": final_items,
        "query": composed.get("query") or query,
        "status": "ok" if len(final_items) >= target else "partial",
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "recall_complete": True,
        "target_items": target,
        "found_items": len(final_items),
        "shortfall": shortfall,
    }


async def run_react(
    *,
    chat: ChatFn,
    tools: NewsTools,
    user_name: str,
    prefs: dict,
    max_steps: int,
    first_ready_seconds: int = 60,
    total_timeout_seconds: int = 180,
    today: str | None = None,
) -> AsyncIterator[dict]:
    day = today or datetime.now().strftime("%Y-%m-%d")
    query = " ".join([*(prefs.get("topics") or []), *(prefs.get("keywords") or [])]).strip() or "今日新闻"
    draft_title = briefing_title(prefs, day)
    graph = build_graph(
        chat=chat,
        tools=tools,
        user_name=user_name,
        prefs=prefs,
        max_steps=max_steps,
        today=day,
    )
    initial: BriefingState = {
        "messages": [],
        "steps": [],
        "title": "",
        "content": "",
        "items": [],
        "query": query,
        "status": "running",
        "review": "",
        "step_count": 0,
        "review_rounds": 0,
    }
    yield {
        "type": "start",
        "query": query,
        "max_steps": max_steps,
        "first_ready_seconds": first_ready_seconds,
        "total_timeout_seconds": total_timeout_seconds,
    }

    started = time.monotonic()
    first_ready_sent = False
    emitted_done = False
    progressive_done = False

    def elapsed() -> float:
        return time.monotonic() - started

    def force_done(reason: str) -> dict:
        if tools.composed is None:
            tools.auto_compose_from_seen(draft_title, "已达时限，根据已检索材料收成。")
        composed = tools.composed or {}
        items = composed.get("items") or tools.draft_items()
        content = composed.get("content") or _render_growing(draft_title, items) or reason
        return {
            "type": "done",
            "title": draft_title,
            "content": content,
            "summary": reason,
            "items": items,
            "query": composed.get("query") or query,
            "step_count": 0,
            "used_fallback": True,
            "status": "partial" if items else "failed",
            "review": reason,
        }

    async def finish_progressive() -> AsyncIterator[dict]:
        nonlocal progressive_done, first_ready_sent, emitted_done
        if progressive_done:
            return
        seed: list[dict] = []
        if tools.composed is not None:
            seed = list(tools.composed.get("items") or [])
            # 模型提前收成但条数不够时，清空后继续补搜补写。
            if len(seed) >= tools.options.max_items:
                return
            tools.composed = None
        progressive_done = True
        async for event in _progressive_compose(
            chat=chat,
            tools=tools,
            prefs=prefs,
            title=draft_title,
            query=query,
            started=started,
            seed_items=seed,
        ):
            if event["type"] == "first_ready":
                first_ready_sent = True
            yield event
        if tools.composed and not emitted_done:
            emitted_done = True
            composed = tools.composed
            items = composed.get("items") or []
            target = tools.options.max_items
            short = len(items) < target
            review = (
                f"目标检索 {target} 条，实际只找到 {len(items)} 条相关资讯"
                f"（已搜索 {tools.search_rounds}/{tools.options.max_rounds} 轮）。"
                "相关结果不够，没有用无关新闻凑数。"
                if short
                else "逐条整理完成"
            )
            if short:
                yield {"type": "status", "message": review}
            yield {
                "type": "done",
                "title": draft_title,
                "content": composed.get("content") or "",
                "summary": composed.get("overview") or "",
                "items": items,
                "query": composed.get("query") or query,
                "step_count": 0,
                "used_fallback": short,
                "status": "ok" if items and not short else ("partial" if items else "failed"),
                "review": review,
                "target_items": target,
                "found_items": len(items),
                "shortfall": review if short else "",
            }

    try:
        async for mode, chunk in graph.astream(
            initial,
            stream_mode=["updates", "values"],
            config={"recursion_limit": max(40, max_steps * 6)},
        ):
            if elapsed() >= total_timeout_seconds:
                for event in _emit_progress(tools, draft_title):
                    yield event
                if tools.draft_items() and tools.composed is None:
                    async for event in finish_progressive():
                        yield event
                if not emitted_done:
                    emitted_done = True
                    yield force_done(f"已超过总时限 {total_timeout_seconds} 秒，先交付当前材料")
                return

            if mode == "updates" and "tools" in chunk:
                for step in chunk["tools"].get("steps") or []:
                    yield {
                        "type": "tool_call",
                        "step_index": step["step_index"],
                        "tool_name": step["tool_name"],
                        "arguments": step["arguments"],
                    }
                    yield {
                        "type": "tool_result",
                        "step_index": step["step_index"],
                        "tool_name": step["tool_name"],
                        "arguments": step["arguments"],
                        "result": step["result"],
                        "ok": step["ok"],
                    }
                for event in _emit_progress(tools, draft_title):
                    yield event
                if tools.needs_language_pass and tools.composed is None:
                    async for event in finish_progressive():
                        yield event
                    return
                continue

            if mode != "values":
                continue
            status = chunk.get("status")
            if status == "failed":
                if tools.draft_items():
                    async for event in finish_progressive():
                        yield event
                    if not emitted_done:
                        emitted_done = True
                        yield force_done(chunk.get("review") or "模型调用失败，已尽量保留检索结果")
                    return
                yield {"type": "error", "message": chunk.get("review") or "模型调用失败"}
                return
            if status in {"ok", "partial"} and not emitted_done:
                current_items = chunk.get("items") or (tools.composed or {}).get("items") or []
                # 模型提前收成但条数不够：走补搜补写，不要直接交差。
                if len(current_items) < tools.options.max_items:
                    async for event in finish_progressive():
                        yield event
                    if emitted_done:
                        return
                emitted_done = True
                final_items = (tools.composed or {}).get("items") or current_items
                final_content = (tools.composed or {}).get("content") or chunk.get("content") or ""
                if not first_ready_sent and final_items:
                    first_ready_sent = True
                    yield {
                        "type": "first_ready",
                        "title": draft_title,
                        "content": final_content,
                        "items": final_items,
                        "query": chunk.get("query") or query,
                        "status": status,
                        "elapsed_seconds": round(elapsed(), 1),
                        "recall_complete": True,
                    }
                yield {
                    "type": "done",
                    "title": draft_title,
                    "content": final_content,
                    "summary": (tools.composed or {}).get("overview")
                    or last_assistant_text(chunk.get("messages") or []),
                    "items": final_items,
                    "query": (tools.composed or {}).get("query") or chunk.get("query") or query,
                    "step_count": chunk.get("step_count") or 0,
                    "used_fallback": status != "ok" or len(final_items) < tools.options.max_items,
                    "status": status if final_items else "partial",
                    "review": chunk.get("review") or "",
                }
    except GraphRecursionError:
        if tools.draft_items():
            async for event in finish_progressive():
                yield event
            if not emitted_done:
                emitted_done = True
                yield force_done("状态图步数超过上限，已用检索结果收成")
            return
        yield {"type": "error", "message": "状态图步数超过上限"}
        return

    if not emitted_done:
        if tools.needs_language_pass and tools.composed is None and tools.draft_items():
            async for event in finish_progressive():
                yield event
        elif tools.draft_items():
            async for event in finish_progressive():
                yield event
            if not emitted_done:
                yield force_done("简报图未正常结束，已用检索结果收成")
        else:
            yield {"type": "error", "message": "简报图没有产生结果"}
