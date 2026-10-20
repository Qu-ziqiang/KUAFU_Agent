"""LangGraph 状态图。模型只在 agent 节点里决定工具，review 节点检查返回值。"""

from __future__ import annotations

import json
import operator
import re
from collections.abc import Awaitable, Callable
from typing import Annotated, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.graph.state import CompiledStateGraph

from app.agent.llm import LLMError
from app.agent.prompts import briefing_language, briefing_title, build_task, custom_clause, rewrite_task, system_prompt
from app.agent.tools import TOOL_SCHEMAS, ToolError, NewsTools

MAX_REVIEW_ROUNDS = 2
ITEM_RE = re.compile(r"\d+\.\s")

ChatFn = Callable[[list[dict], list[dict]], Awaitable]


class BriefingState(TypedDict):
    messages: Annotated[list, add_messages]
    steps: Annotated[list, operator.add]
    title: str
    content: str
    items: list
    query: str
    status: str
    review: str
    step_count: int
    review_rounds: int


def message_text(message) -> str:
    content = message.get("content") if isinstance(message, dict) else getattr(message, "content", "")
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text", "")))
            else:
                parts.append(str(part))
        return "".join(parts).strip()
    return str(content or "").strip()


def to_openai_messages(messages: list) -> list[dict]:
    converted = []
    for message in messages:
        if isinstance(message, dict):
            converted.append(message)
            continue
        role = {"ai": "assistant", "human": "user"}.get(message.type, message.type)
        if role == "assistant":
            payload: dict = {"role": "assistant", "content": message_text(message)}
            tool_calls = []
            for call in message.tool_calls or []:
                tool_calls.append(
                    {
                        "id": call.get("id") or "",
                        "type": "function",
                        "function": {
                            "name": call.get("name") or "",
                            "arguments": json.dumps(call.get("args") or {}, ensure_ascii=False),
                        },
                    }
                )
            if tool_calls:
                payload["tool_calls"] = tool_calls
            converted.append(payload)
            continue
        if role == "tool":
            converted.append(
                {
                    "role": "tool",
                    "tool_call_id": message.tool_call_id,
                    "content": message_text(message) or str(message.content or ""),
                }
            )
            continue
        converted.append({"role": role, "content": message_text(message)})
    return converted


def assistant_payload(message) -> dict:
    payload: dict = {"role": "assistant", "content": message.content}
    if message.tool_calls:
        payload["tool_calls"] = [
            {
                "id": call.id or f"call_{index}",
                "type": "function",
                "function": {"name": call.name, "arguments": call.arguments or "{}"},
            }
            for index, call in enumerate(message.tool_calls, start=1)
        ]
        if payload["content"] is None:
            payload["content"] = ""
    return payload


def last_assistant_text(messages: list) -> str:
    for message in reversed(messages):
        role = message.get("role") if isinstance(message, dict) else getattr(message, "type", "")
        if role in {"assistant", "ai"}:
            text = message_text(message)
            if text:
                return text
    return ""


def item_count(content: str) -> int:
    count = 0
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("- ") or stripped.startswith("* ") or ITEM_RE.match(stripped):
            count += 1
    return count


def pending_calls(message) -> tuple[list[dict], list[dict]]:
    valid = []
    for call in getattr(message, "tool_calls", None) or []:
        valid.append(
            {
                "id": call.get("id") or "",
                "name": call.get("name") or "",
                "arguments": call.get("args") if isinstance(call.get("args"), dict) else {},
                "raw_arguments": json.dumps(call.get("args") or {}, ensure_ascii=False),
            }
        )
    invalid = []
    for call in getattr(message, "invalid_tool_calls", None) or []:
        invalid.append(
            {
                "id": call.get("id") or "",
                "name": call.get("name") or "unknown",
                "raw_arguments": str(call.get("args") or ""),
                "error": str(call.get("error") or "参数不是合法 JSON"),
            }
        )
    return valid, invalid


def build_graph(
    *,
    chat: ChatFn,
    tools: NewsTools,
    user_name: str,
    prefs: dict,
    max_steps: int,
    today: str,
) -> CompiledStateGraph:
    def prepare(state: BriefingState) -> dict:
        return {
            "messages": [
                {"role": "system", "content": system_prompt(briefing_language(prefs))},
                {
                    "role": "user",
                    "content": build_task(
                        user_name,
                        prefs,
                        today,
                        tools.options.max_rounds,
                        tools.options.max_items,
                    ),
                },
            ]
        }

    async def agent(state: BriefingState) -> dict:
        schemas = TOOL_SCHEMAS
        if tools.needs_language_pass and tools.composed is None:
            schemas = [item for item in TOOL_SCHEMAS if item["function"]["name"] == "compose_briefing"]
        try:
            message = await chat(to_openai_messages(state["messages"]), schemas)
        except LLMError as exc:
            return {"status": "failed", "review": str(exc)}
        return {"messages": [assistant_payload(message)]}

    def execute_tools(state: BriefingState) -> dict:
        valid, invalid = pending_calls(state["messages"][-1])
        steps = []
        tool_messages = []
        used = state["step_count"]

        def push(name: str, raw_arguments: str, result: str, ok: bool, call_id: str) -> None:
            nonlocal used
            used += 1
            steps.append(
                {
                    "step_index": used,
                    "tool_name": name,
                    "arguments": raw_arguments,
                    "result": result,
                    "ok": ok,
                }
            )
            if call_id:
                tool_messages.append({"role": "tool", "tool_call_id": call_id, "content": result})

        for call in valid:
            if tools.needs_language_pass and call["name"] != "compose_briefing":
                push(
                    call["name"],
                    call["raw_arguments"],
                    f"已召回 {tools.options.max_items} 条。请用{briefing_language(prefs)}调用 compose_briefing，不要再检索。",
                    False,
                    call["id"],
                )
                continue
            if tools.composed is not None and call["name"] != "compose_briefing":
                push(
                    call["name"],
                    call["raw_arguments"],
                    "简报已经收成，不要再检索。",
                    False,
                    call["id"],
                )
                continue
            if used >= max_steps:
                push(call["name"], call["raw_arguments"], "已达到工具步数上限，这一步未执行", False, call["id"])
                continue
            # 留给 compose_briefing 至少一步
            if (
                tools.composed is None
                and used >= max_steps - 1
                and call["name"] != "compose_briefing"
            ):
                push(
                    call["name"],
                    call["raw_arguments"],
                    f"步数将尽。请立刻调用 compose_briefing，尽量收成最多 {tools.options.max_items} 条。",
                    False,
                    call["id"],
                )
                continue
            if (
                call["name"] == "compose_briefing"
                and tools.composed is None
                and not tools.recall_ready()
                and tools.search_rounds < tools.options.max_rounds
            ):
                need = tools.options.max_items - len(tools.draft_items(limit=40))
                push(
                    call["name"],
                    call["raw_arguments"],
                    f"相关条目还不够，还差约 {max(need, 1)} 条。请继续 web_search，不要现在收成。",
                    False,
                    call["id"],
                )
                continue
            try:
                result = tools.execute(call["name"], call["arguments"])
                ok = True
            except (ToolError, json.JSONDecodeError) as exc:
                result = f"工具执行失败: {exc}"
                ok = False
            push(call["name"], call["raw_arguments"], result, ok, call["id"])
            if ok and call["name"] == "web_search" and tools.composed is None and tools.recall_ready():
                tools.needs_language_pass = True

        for call in invalid:
            push(call["name"], call["raw_arguments"], f"工具执行失败: {call['error']}", False, call["id"])

        updates: dict = {"messages": tool_messages, "steps": steps, "step_count": used}
        if tools.needs_language_pass and tools.composed is None:
            updates["messages"] = list(tool_messages) + [
                {
                    "role": "user",
                    "content": rewrite_task(
                        briefing_language(prefs),
                        tools.draft_items(),
                        briefing_title(prefs, today),
                        custom_clause(prefs),
                    ),
                }
            ]
        elif tools.composed is None and used >= max_steps - 2:
            updates["messages"] = list(tool_messages) + [
                {
                    "role": "user",
                    "content": (
                        "材料已经够了。下一步必须调用 compose_briefing，"
                        "用本次检索里出现过的链接收成 4 到 5 条，不要再搜索。"
                    ),
                }
            ]
        return updates

    def review(state: BriefingState) -> dict:
        composed = tools.composed or {}
        items = composed.get("items") or []
        content = composed.get("content") or ""
        title = briefing_title(prefs, today)
        reasons: list[str] = []
        min_items = tools.effective_min_items()
        max_items = tools.options.max_items
        if not composed:
            reasons.append("还没有调用 compose_briefing")
        elif tools.focus_terms() and not items:
            return {
                **{
                    "title": title,
                    "content": content or f"没有找到与「{'、'.join(tools.focus_terms())}」相关的新闻。",
                    "items": [],
                    "query": composed.get("query") or "",
                },
                "status": "partial",
                "review": "没有相关新闻，未用其他领域的条目凑数",
            }
        elif not (min_items <= len(items) <= max_items):
            reasons.append(f"新闻条目应为 {min_items} 到 {max_items} 条，当前 {len(items)} 条")
        else:
            for item in items:
                if not str(item.get("url") or "").startswith("http"):
                    reasons.append("有条目缺少原始链接")
                    break
        if reasons and state["step_count"] >= max_steps:
            reasons.append("工具步数已用完")

        payload = {
            "title": title,
            "content": content,
            "items": items,
            "query": composed.get("query") or "",
        }
        if not reasons:
            return {
                **payload,
                "status": "ok",
                "review": f"通过：{len(items)} 条，均带原始链接",
            }

        reason = "；".join(reasons)
        rounds = state["review_rounds"] + 1
        if state["step_count"] >= max_steps or rounds >= MAX_REVIEW_ROUNDS:
            if not composed:
                auto = tools.auto_compose_from_seen(
                    briefing_title(prefs, today),
                    "根据本次检索自动收成的简报。",
                )
                if auto:
                    return {
                        "title": auto["title"],
                        "content": auto["content"],
                        "items": auto["items"],
                        "query": auto.get("query") or "",
                        "status": "partial",
                        "review": reason + "；已用检索结果自动收稿",
                        "review_rounds": rounds,
                    }
            return {
                **payload,
                "content": content or last_assistant_text(state["messages"]) or reason,
                "status": "partial",
                "review": reason,
                "review_rounds": rounds,
            }
        return {
            "status": "running",
            "review": reason,
            "review_rounds": rounds,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"简报检查未通过：{reason}。"
                        "请只使用本次工具返回过的原始链接，重新调用 compose_briefing。"
                        f"输出语言必须是{briefing_language(prefs)}，不要照抄外文摘要。"
                        "条数控制在 4 到 5 条，不要编造。"
                    ),
                }
            ],
        }

    def after_agent(state: BriefingState) -> str:
        if state["status"] == "failed":
            return "end"
        valid, invalid = pending_calls(state["messages"][-1])
        if valid or invalid:
            return "tools"
        return "review"

    def after_tools(state: BriefingState) -> str:
        # 搜满条数后交给 loop 一条条总结，不再回 agent 整页 compose。
        if tools.needs_language_pass and tools.composed is None:
            return "end"
        recalled = (tools.composed or {}).get("items") or []
        if len(recalled) >= tools.options.max_items:
            return "review"
        if state["step_count"] >= max_steps:
            return "review"
        return "agent"

    def after_review(state: BriefingState) -> str:
        if state["status"] in {"ok", "partial", "failed"}:
            return "end"
        return "agent"

    graph = StateGraph(BriefingState)
    graph.add_node("prepare", prepare)
    graph.add_node("agent", agent)
    graph.add_node("tools", execute_tools)
    graph.add_node("review", review)
    graph.add_edge(START, "prepare")
    graph.add_edge("prepare", "agent")
    graph.add_conditional_edges("agent", after_agent, {"tools": "tools", "review": "review", "end": END})
    graph.add_conditional_edges("tools", after_tools, {"agent": "agent", "review": "review", "end": END})
    graph.add_conditional_edges("review", after_review, {"agent": "agent", "end": END})
    return graph.compile()
