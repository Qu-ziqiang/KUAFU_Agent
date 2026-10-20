from app.agent.constraints import matches_geo, parse_custom_constraints
from app.agent.prompts import briefing_title
from app.agent.tools import NewsTools, SearchOptions, ToolError, credibility_for


def test_search_records_links_and_compose_limits_length():
    tools = NewsTools(
        search=lambda query: [
            {"title": f"新闻{i}", "url": f"https://openai.com/news/{i}", "source": "OpenAI", "summary": "进展"}
            for i in range(1, 6)
        ],
        options=SearchOptions(max_items=5, min_items=3),
    )
    tools.web_search("open models")
    assert credibility_for("https://openai.com/news/1") == "high"
    try:
        tools.compose_briefing("过长", "总览", [
            {"title": f"新闻{i}", "url": f"https://openai.com/news/{i}", "summary": "一两句说明"}
            for i in range(1, 7)
        ])
    except ToolError as exc:
        assert "条" in str(exc)
    else:
        raise AssertionError("超过上限应该被拒绝")


def test_auto_compose_from_seen():
    tools = NewsTools(search=lambda query: [
        {"title": f"新闻{i}", "url": f"https://openai.com/news/{i}", "source": "OpenAI", "summary": f"摘要{i}"}
        for i in range(1, 5)
    ])
    tools.web_search("open models")
    composed = tools.auto_compose_from_seen("每日简报", "自动收成。")
    assert composed is not None
    assert len(composed["items"]) == 4
    assert "https://openai.com/news/" in composed["content"]
    assert "**新闻1**" in composed["content"]


def test_briefing_title_uses_topic_and_keyword():
    assert briefing_title({"topics": ["歌手"], "keywords": ["陶喆"]}, "2026-10-19") == "歌手 · 陶喆简报 2026-10-19"


def test_custom_foreign_filters_domestic_sources():
    assert parse_custom_constraints("只看国外")["geo"] == "foreign"
    assert matches_geo("https://techcrunch.com/a", "OpenAI raises", "US market", "foreign")
    assert not matches_geo("https://www.thepaper.cn/a", "研华亮相中国工博会", "上海展会", "foreign")
    tools = NewsTools(
        search=lambda query: [
            {"title": "OpenAI enterprise", "url": "https://techcrunch.com/openai", "source": "techcrunch.com", "summary": "US revenue"},
            {"title": "研华亮相中国工博会", "url": "https://www.thepaper.cn/china", "source": "thepaper.cn", "summary": "上海国内展会"},
        ],
        options=SearchOptions(max_items=5, min_items=1, focus_terms=["AI"], geo="foreign"),
    )
    message = tools.web_search("AI")
    assert "工博会" not in message
    assert len(tools.draft_items()) == 1


def test_off_topic_hits_are_dropped():
    tools = NewsTools(
        search=lambda query: [
            {"title": "央视环球综艺秀播出", "url": "https://tv.cctv.com/variety", "source": "cctv.com", "summary": "综艺节目"},
            {"title": "英伟达开源工具", "url": "https://36kr.com/nvidia", "source": "36kr.com", "summary": "人工智能芯片"},
        ],
        options=SearchOptions(max_items=5, min_items=3, focus_terms=["综艺", "陶喆"]),
    )
    message = tools.web_search("最新消息")
    assert "综艺" in tools.queries[-1]
    assert "陶喆" in tools.queries[-1]
    assert "英伟达" not in message
    assert len(tools.draft_items()) == 1
    tools.compose_briefing(
        "综艺简报",
        "今天的综艺节目。",
        [{"title": "央视综艺", "url": "https://tv.cctv.com/variety", "summary": "环球综艺秀在播。"}],
        allow_short=True,
    )
    assert len(tools.composed["items"]) == 1
    assert "环球" in tools.composed["content"] and "**综艺**" in tools.composed["content"]


def test_keyword_stuffed_agriculture_is_off_topic():
    """搜索摘要里夹带查询词、正文其实是农业新闻时，改写后应判为不相关。"""
    tools = NewsTools(
        options=SearchOptions(focus_terms=["开源模型", "RAG"]),
    )
    assert tools.on_topic(
        "开源模型 RAG 最新进展",
        "开源模型 RAG 国内 河北唐山港经济开发区鼓励农户发展葡萄、桃、番茄等设施农业",
    )
    assert not tools.on_topic(
        "唐山港经济开发区发展设施农业",
        "河北唐山港经济开发区鼓励农户发展葡萄、桃、番茄等设施农业，成为当地乡村振兴新亮点。",
    )
    assert tools.on_topic(
        "企业知识库上线",
        "方案采用检索增强生成混合架构，并支持开源模型私有化部署。",
    )


def test_private_url_is_blocked():
    tools = NewsTools(search=lambda query: [
        {"title": "内网", "url": "http://127.0.0.1/secret", "source": "local", "summary": "不行"}
    ])
    tools.web_search("local")
    try:
        tools.fetch_webpage("http://127.0.0.1/secret")
    except ToolError as exc:
        assert "内网" in str(exc)
    else:
        raise AssertionError("内网地址应该被拒绝")
