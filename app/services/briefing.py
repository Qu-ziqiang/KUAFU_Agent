"""用户、偏好、简报的持久化，以及一次 Agent 运行。"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timezone

from app.agent.llm import OpenAICompatibleClient
from app.agent.constraints import parse_custom_constraints
from app.agent.loop import run_react
from app.agent.prompts import briefing_title
from app.agent.tools import NewsTools, SearchOptions
from app.config import Settings
from app.schemas import RECALL_LIMIT, clean_site
from app.db import execute, query_all, query_one


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def row_user(row) -> dict:
    return {"id": row["id"], "name": row["name"], "email": row["email"], "created_at": row["created_at"]}


def row_prefs(row) -> dict:
    if row is None:
        return {
            "topics": [],
            "keywords": [],
            "language": "zh",
            "recall_count": 5,
            "custom_prompt": "仅仅输出国内的资讯",
            "preferred_sites": [],
            "site_hits": {},
            "search_count": 0,
            "updated_at": None,
        }
    keys = row.keys()
    recall = row["recall_count"] if "recall_count" in keys and row["recall_count"] else 5
    custom = row["custom_prompt"] if "custom_prompt" in keys and row["custom_prompt"] else ""
    sites = json.loads(row["preferred_sites"]) if "preferred_sites" in keys and row["preferred_sites"] else []
    hits = json.loads(row["site_hits"]) if "site_hits" in keys and row["site_hits"] else {}
    count = row["search_count"] if "search_count" in keys and row["search_count"] else 0
    return {
        "topics": json.loads(row["topics"]),
        "keywords": json.loads(row["keywords"]),
        "language": row["language"],
        "recall_count": int(recall),
        "custom_prompt": custom,
        "preferred_sites": sites,
        "site_hits": hits if isinstance(hits, dict) else {},
        "search_count": int(count),
        "updated_at": row["updated_at"],
    }


def row_briefing(row, steps: list[dict] | None = None) -> dict:
    keys = row.keys()
    items = json.loads(row["items"]) if "items" in keys and row["items"] else []
    data = {
        "id": row["id"],
        "user_id": row["user_id"],
        "title": row["title"],
        "content": row["content"],
        "summary": row["summary"],
        "query": row["query"] if "query" in keys else "",
        "items": items,
        "item_count": len(items),
        "status": row["status"],
        "error": row["error"],
        "created_at": row["created_at"],
    }
    if steps is not None:
        data["steps"] = steps
    return data


def row_step(row) -> dict:
    return {
        "step_index": row["step_index"],
        "tool_name": row["tool_name"],
        "arguments": row["arguments"],
        "result": row["result"],
        "ok": bool(row["ok"]),
        "created_at": row["created_at"],
    }


def create_user(name: str, email: str | None) -> dict:
    created = now_iso()
    user_id = str(uuid.uuid4())
    execute(
        "INSERT INTO users (id, name, email, created_at) VALUES (?, ?, ?, ?)",
        (user_id, name, email, created),
    )
    execute(
        """
        INSERT INTO preferences
            (user_id, topics, keywords, language, recall_count, custom_prompt, preferred_sites, site_hits, search_count, updated_at)
        VALUES (?, '[]', '[]', 'zh', 5, '仅仅输出国内的资讯', '[]', '{}', 0, ?)
        """,
        (user_id, created),
    )
    return get_user(user_id)


def canonical_user_id(user_id: str) -> str | None:
    row = query_one("SELECT id FROM users WHERE id = ?", (user_id,))
    if row is not None:
        return row["id"]
    if str(user_id).isdigit():
        legacy = query_one("SELECT id FROM users WHERE legacy_id = ?", (int(user_id),))
        if legacy is not None:
            return legacy["id"]
    return None


def get_user(user_id: str) -> dict | None:
    user_id = canonical_user_id(user_id) or ""
    if not user_id:
        return None
    row = query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if row is None:
        return None
    data = row_user(row)
    prefs = query_one("SELECT * FROM preferences WHERE user_id = ?", (user_id,))
    data["preferences"] = row_prefs(prefs)
    return data


def save_preferences(
    user_id: str,
    topics: list[str],
    keywords: list[str],
    language: str,
    settings: Settings,
    recall_count: int = 5,
    custom_prompt: str = "",
) -> dict:
    found = canonical_user_id(user_id)
    if found is None:
        return None
    user_id = found
    updated = now_iso()
    recall = min(RECALL_LIMIT, max(1, int(recall_count or 5)))
    execute(
        """
        UPDATE preferences
        SET topics = ?, keywords = ?, language = ?, recall_count = ?, custom_prompt = ?, updated_at = ?
        WHERE user_id = ?
        """,
        (
            json.dumps(topics, ensure_ascii=False),
            json.dumps(keywords, ensure_ascii=False),
            language,
            recall,
            (custom_prompt or "").strip()[:500],
            updated,
            user_id,
        ),
    )
    return get_user(user_id)


def site_recall_bonus(search_count: int, sites: list[str]) -> int:
    """画像里已有偏爱网站时，搜索次数越多，额外多取的条数越多，最多加 3 条。"""
    if not sites:
        return 0
    return min(3, 1 + max(0, int(search_count)) // 2)


def absorb_source_hits(hits: dict, items: list[dict]) -> tuple[dict, list[str]]:
    """按简报来源累计域名次数，频次高的站成为偏爱网站。"""
    counts = {str(host): int(count) for host, count in (hits or {}).items() if str(host)}
    for item in items or []:
        host = clean_site(str(item.get("url") or item.get("source") or ""))
        if not host or "." not in host:
            continue
        counts[host] = counts.get(host, 0) + 1
    ranked = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    preferred = [host for host, _count in ranked[:8]]
    return counts, preferred


def remember_sources(user_id: str, items: list[dict]) -> None:
    row = query_one("SELECT site_hits FROM preferences WHERE user_id = ?", (user_id,))
    current = {}
    if row is not None and row["site_hits"]:
        loaded = json.loads(row["site_hits"])
        if isinstance(loaded, dict):
            current = loaded
    counts, preferred = absorb_source_hits(current, items)
    execute(
        "UPDATE preferences SET site_hits = ?, preferred_sites = ? WHERE user_id = ?",
        (
            json.dumps(counts, ensure_ascii=False),
            json.dumps(preferred, ensure_ascii=False),
            user_id,
        ),
    )


def bump_search_count(user_id: str) -> int:
    execute(
        "UPDATE preferences SET search_count = search_count + 1 WHERE user_id = ?",
        (user_id,),
    )
    row = query_one("SELECT search_count FROM preferences WHERE user_id = ?", (user_id,))
    return int(row["search_count"]) if row else 0


def merge_domains(user_sites: list[str], global_sites: list[str]) -> list[str]:
    seen: list[str] = []
    for host in [*user_sites, *global_sites]:
        text = str(host).strip().lower()
        if text and text not in seen:
            seen.append(text)
        if len(seen) >= 40:
            break
    return seen


def list_briefings(user_id: str) -> list[dict]:
    user_id = canonical_user_id(user_id) or user_id
    rows = query_all(
        """
        SELECT id, user_id, title, content, summary, query, items, status, error, created_at
        FROM briefings
        WHERE user_id = ?
        ORDER BY id DESC
        """,
        (user_id,),
    )
    return [row_briefing(row) for row in rows]


def get_briefing(briefing_id: int) -> dict | None:
    row = query_one("SELECT * FROM briefings WHERE id = ?", (briefing_id,))
    if row is None:
        return None
    steps = query_all(
        "SELECT * FROM agent_steps WHERE briefing_id = ? ORDER BY step_index",
        (briefing_id,),
    )
    return row_briefing(row, [row_step(step) for step in steps])


def list_user_ids() -> list[str]:
    return [row["id"] for row in query_all("SELECT id FROM users ORDER BY id")]


_user_locks: dict[int, asyncio.Lock] = {}


def _lock_for(user_id: str) -> asyncio.Lock:
    lock = _user_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _user_locks[user_id] = lock
    return lock


def _insert_briefing(
    user_id: str,
    title: str,
    content: str,
    summary: str,
    status: str,
    error: str | None,
    query: str = "",
    items: list | None = None,
) -> int:
    cur = execute(
        """
        INSERT INTO briefings (user_id, title, content, summary, query, items, status, error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            user_id,
            title,
            content,
            summary,
            query,
            json.dumps(items or [], ensure_ascii=False),
            status,
            error,
            now_iso(),
        ),
    )
    return int(cur.lastrowid)


def _insert_step(briefing_id: int, step: dict) -> None:
    execute(
        """
        INSERT INTO agent_steps (briefing_id, step_index, tool_name, arguments, result, ok, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            briefing_id,
            step["step_index"],
            step["tool_name"],
            step["arguments"],
            step["result"][:4000],
            1 if step["ok"] else 0,
            now_iso(),
        ),
    )


async def stream_briefing(settings: Settings, user_id: str) -> AsyncIterator[dict]:
    user = get_user(user_id)
    if user is None:
        yield {"type": "error", "message": "用户不存在"}
        return
    user_id = user["id"]
    if not settings.llm_ready:
        yield {"type": "error", "message": "未配置 OPENAI_API_KEY"}
        return
    if not settings.search_ready:
        yield {"type": "error", "message": "未配置 TAVILY_API_KEY"}
        return

    lock = _lock_for(user_id)
    if lock.locked():
        yield {"type": "error", "message": "该用户已有简报正在生成"}
        return

    async with lock:
        prefs = user["preferences"]
        recall = min(RECALL_LIMIT, max(1, int(prefs.get("recall_count") or settings.briefing_max_items)))
        custom = str(prefs.get("custom_prompt") or "").strip()
        sites = [str(item) for item in (prefs.get("preferred_sites") or [])]
        search_count = bump_search_count(user_id)
        prefs["search_count"] = search_count
        # 召回条数以用户填写为准；搜索时多取一点候选，方便过滤后仍能凑满。
        pool = min(RECALL_LIMIT, recall + 3)
        constraints = parse_custom_constraints(custom)
        geo = constraints.get("geo")
        # 最大搜索轮次 = 检索条数的一半（向上取整）；条数多时总时长跟着放宽。
        rounds = max(1, (recall + 1) // 2)
        if geo == "foreign":
            timeout = max(settings.foreign_timeout_seconds, 120 + rounds * 55)
            domains: list[str] = []
        else:
            timeout = max(settings.domestic_timeout_seconds, 90 + rounds * 50)
            domains = merge_domains(sites, list(settings.preferred_domains))
        step_budget = max(settings.max_agent_steps, rounds + recall + 2)
        options = SearchOptions(
            max_results=pool,
            max_rounds=rounds,
            min_items=min(settings.briefing_min_items, recall),
            max_items=recall,
            preferred_domains=domains,
            prefer_chinese=(prefs.get("language") or "zh") == "zh" and geo != "foreign",
            focus_terms=[*(prefs.get("topics") or []), *(prefs.get("keywords") or [])],
            geo=geo,
            custom_prompt=custom,
        )
        tools = NewsTools(api_key=settings.tavily_api_key, options=options)
        client = OpenAICompatibleClient(settings)
        steps: list[dict] = []
        done: dict | None = None
        latest_items: list = []

        try:
            async for event in run_react(
                chat=client.chat,
                tools=tools,
                user_name=user["name"],
                prefs=prefs,
                max_steps=step_budget,
                first_ready_seconds=settings.first_ready_seconds,
                total_timeout_seconds=timeout,
            ):
                if event["type"] == "tool_result":
                    steps.append(event)
                if event["type"] in {"sources_update", "first_ready", "done"} and event.get("items"):
                    latest_items = event["items"]
                if event["type"] == "done":
                    done = event
                if event["type"] == "error":
                    briefing_id = _insert_briefing(
                        user_id,
                        "生成失败",
                        "",
                        "",
                        "failed",
                        event["message"],
                        "",
                        latest_items,
                    )
                    for step in steps:
                        _insert_step(briefing_id, step)
                    event["briefing_id"] = briefing_id
                yield event
        except Exception as exc:
            message = f"生成中断: {exc}"
            briefing_id = _insert_briefing(
                user_id,
                "生成失败",
                "",
                "",
                "failed",
                message,
                "",
                latest_items,
            )
            for step in steps:
                _insert_step(briefing_id, step)
            yield {"type": "error", "message": message, "briefing_id": briefing_id}
            return

        if done is None:
            return
        done["title"] = briefing_title(prefs, datetime.now().strftime("%Y-%m-%d"))
        status = done.get("status") or ("ok" if not done["used_fallback"] else "partial")
        briefing_id = _insert_briefing(
            user_id,
            done["title"],
            done["content"],
            done["summary"],
            status,
            None if status == "ok" else (done.get("review") or "简报检查未通过"),
            done.get("query") or "",
            done.get("items") or latest_items,
        )
        for step in steps:
            _insert_step(briefing_id, step)
        remember_sources(user_id, done.get("items") or latest_items)
        saved = get_briefing(briefing_id)
        yield {"type": "saved", "briefing": saved}
