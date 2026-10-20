import asyncio
import json

from app.agent.llm import LLMMessage, ToolCall
from app.agent.loop import run_react
from app.agent.tools import NewsTools, SearchOptions, ToolError


def sample_results():
    return [
        {
            "title": f"agent 新闻{i}",
            "url": f"https://jiqizhixin.com/a{i}",
            "source": "jiqizhixin.com",
            "summary": f"关于 LLM 的摘要{i}",
        }
        for i in range(1, 5)
    ]


def tools() -> NewsTools:
    return NewsTools(
        search=lambda query: sample_results(),
        options=SearchOptions(
            max_results=5,
            max_rounds=2,
            min_items=3,
            max_items=4,
            focus_terms=["agent", "LLM"],
        ),
    )


class ScriptedModel:
    def __init__(self, rounds: list[LLMMessage]):
        self.rounds = rounds

    async def chat(self, messages, tool_schemas=None):
        if tool_schemas:
            names = [item["function"]["name"] for item in tool_schemas]
            assert "web_search" in names
        return self.rounds.pop(0)


async def collect(rounds: list[LLMMessage], news: NewsTools | None = None) -> list[dict]:
    events = []
    async for event in run_react(
        chat=ScriptedModel(rounds).chat,
        tools=news or tools(),
        user_name="测试",
        prefs={"topics": ["agent"], "keywords": ["LLM"], "language": "zh"},
        max_steps=8,
        first_ready_seconds=60,
        total_timeout_seconds=180,
        today="2026-10-19",
    ):
        events.append(event)
    return events


def test_search_streams_item_by_item_then_overview():
    # 1 次搜工具 + 4 条逐条总结 + 1 次收尾总览
    rounds = [
        LLMMessage(None, [ToolCall("1", "web_search", json.dumps({"query": "agent LLM"}))]),
        LLMMessage("中文标题1\n这是第一条关于 agent 与 LLM 的摘要。", []),
        LLMMessage("中文标题2\n这是第二条关于 agent 与 LLM 的摘要。", []),
        LLMMessage("中文标题3\n这是第三条关于 agent 与 LLM 的摘要。", []),
        LLMMessage("中文标题4\n这是第四条关于 agent 与 LLM 的摘要。", []),
        LLMMessage("今天几条新闻说明 agent 与 LLM 都在推进。", []),
    ]
    events = asyncio.run(collect(rounds))
    types = [event["type"] for event in events]
    assert "item_draft" in types
    assert "sources_update" in types
    assert "first_ready" in types
    done = next(event for event in events if event["type"] == "done")
    assert done["status"] == "ok"
    assert len(done["items"]) == 4
    assert "**agent**" in done["content"] and "**LLM**" in done["content"]
    assert done["content"].index("中文标题1") < done["content"].rindex("agent")


def test_recall_stops_at_configured_count():
    hits = [
        {"title": f"综艺 News {i}", "url": f"https://36kr.com/p/{i}", "source": "36kr.com", "summary": f"English summary {i} 陶喆"}
        for i in range(1, 6)
    ]
    news = NewsTools(
        search=lambda query: hits,
        options=SearchOptions(
            max_results=5,
            max_rounds=2,
            min_items=3,
            max_items=5,
            focus_terms=["综艺", "陶喆"],
        ),
    )
    seen: dict = {}

    class WatchingModel(ScriptedModel):
        async def chat(self, messages, tool_schemas=None):
            seen["calls"] = seen.get("calls", 0) + 1
            seen["tools"] = None if tool_schemas is None else [item["function"]["name"] for item in tool_schemas]
            seen["prompt"] = json.dumps(messages, ensure_ascii=False)
            return self.rounds.pop(0)

    rounds = [LLMMessage(None, [ToolCall("1", "web_search", json.dumps({"query": "variety"}))])]
    for i in range(1, 6):
        rounds.append(LLMMessage(f"Polished Title {i}\nPolished summary {i} about 综艺 and 陶喆.", []))
    rounds.append(LLMMessage("Closing overview about the matched stories.", []))
    model = WatchingModel(rounds)

    async def collect_once():
        events = []
        async for event in run_react(
            chat=model.chat,
            tools=news,
            user_name="tester",
            prefs={"topics": ["综艺"], "keywords": ["陶喆"], "language": "zh"},
            max_steps=6,
            today="2026-10-19",
        ):
            events.append(event)
        return events

    events = asyncio.run(collect_once())
    done = next(event for event in events if event["type"] == "done")
    assert done["status"] == "ok"
    assert len(done["items"]) == 5
    assert "Polished Title 1" in done["content"]
    assert "English summary" not in done["content"]
    assert seen["calls"] == 7  # 1 search + 5 items + 1 overview
    assert seen["tools"] is None
    assert "Closing overview about the matched stories." in done["content"]
    assert done["content"].index("Polished Title 1") < done["content"].index("Closing overview")


def test_search_round_limit():
    news = tools()
    news.web_search("一次")
    news.web_search("两次")
    try:
        news.web_search("三次")
    except ToolError as exc:
        assert "上限" in str(exc)
    else:
        raise AssertionError("第三次搜索应被拒绝")


def test_skip_triggers_refill_search():
    """改写丢掉一条后，监督层补搜，凑满召回条数。"""
    calls = {"search": 0}

    def search(query: str):
        calls["search"] += 1
        if calls["search"] == 1:
            return [
                {"title": "agent keep 1", "url": "https://techcrunch.com/1", "source": "techcrunch.com", "summary": "agent LLM one"},
                {"title": "agent drop", "url": "https://techcrunch.com/2", "source": "techcrunch.com", "summary": "agent LLM drop"},
                {"title": "agent keep 2", "url": "https://techcrunch.com/3", "source": "techcrunch.com", "summary": "agent LLM two"},
            ]
        return [
            {"title": "agent keep 3", "url": "https://theverge.com/4", "source": "theverge.com", "summary": "agent LLM three"},
        ]

    news = NewsTools(
        search=search,
        options=SearchOptions(
            max_results=3,
            max_rounds=2,
            min_items=1,
            max_items=3,
            focus_terms=["agent", "LLM"],
            geo="foreign",
        ),
    )
    rounds = [
        LLMMessage(None, [ToolCall("1", "web_search", json.dumps({"query": "agent LLM"}))]),
        LLMMessage("Keep One\nSummary one about agent and LLM.", []),
        LLMMessage("SKIP", []),
        LLMMessage("Keep Two\nSummary two about agent and LLM.", []),
        LLMMessage("Keep Three\nSummary three about agent and LLM.", []),
        LLMMessage("Closing after refill.", []),
    ]
    events = asyncio.run(
        collect(
            rounds,
            news,
        )
    )
    done = next(event for event in events if event["type"] == "done")
    assert calls["search"] == 2
    assert len(done["items"]) == 3
    assert "Keep Three" in done["content"]
    assert any(event.get("message", "").startswith("相关条目不足") for event in events if event["type"] == "status")
