from app.agent.constraints import parse_custom_constraints


def briefing_title(prefs: dict, today: str) -> str:
    """标题固定带上话题和关键词，不让模型另起名字。"""
    topics = [str(item).strip() for item in (prefs or {}).get("topics") or [] if str(item).strip()]
    keywords = [str(item).strip() for item in (prefs or {}).get("keywords") or [] if str(item).strip()]
    parts = []
    if topics:
        parts.append("、".join(topics[:3]))
    if keywords:
        parts.append("、".join(keywords[:3]))
    subject = " · ".join(parts) or "新闻"
    return f"{subject}简报 {today}"


def briefing_language(prefs: dict) -> str:
    """前端「简报语言」下拉框的值，原样作为提示词参数。"""
    code = (prefs or {}).get("language") or "zh"
    if code == "en":
        return "English"
    return "简体中文"


def custom_clause(prefs: dict) -> str:
    text = str((prefs or {}).get("custom_prompt") or "").strip()
    if not text:
        return ""
    constraints = parse_custom_constraints(text)
    geo = constraints.get("geo")
    lines = [
        "【硬性自定义要求，优先级最高，必须严格执行】",
        text,
        "任何不符合该要求的新闻都不得写入简报，不得用无关新闻凑数。",
    ]
    if geo == "foreign":
        lines.append("结构化约束 geo=foreign：只保留国外/海外来源与事件，拒绝中国国内站点与国内活动报道。")
        lines.append("这是国外检索方案：轮数和时间都更宽。相关条目不够时继续 web_search，不要搜一两次就停。")
    elif geo == "domestic":
        lines.append("结构化约束 geo=domestic：只保留国内来源或国内事件。")
    return "\n".join(lines)


def system_prompt(language: str) -> str:
    return f"""你是每日新闻助手。话题由用户指定，可以是人工智能，也可以是娱乐、社会、时政等普通新闻。新闻只能来自本次工具返回的结果。
规则：
- 输出语言：{language}。这是硬性参数。title、overview、每条 title 和 summary 必须全部使用{language}。
- 来源标题或摘要若是别的语言，改写成{language}，不要照抄原文句子。URL 保持工具返回的原样，不要翻译链接。
- 检索词必须包含用户的话题和关键词。用户自定义要求优先级最高，必须严格执行。
- 每条新闻必须真的在讲这些话题或关键词，并且满足自定义要求。不满足就丢掉，不要凑数。
- 写进简报的链接必须是工具结果里的原始链接，不要编造。
- 不要输出 * 或 **。标题和关键词由系统加粗。不要用 ## 小标题和表情。
- 收成后用一两句话说明选了什么，不要再调用工具。
"""


def build_task(name: str, prefs: dict, today: str, max_search_rounds: int, max_items: int) -> str:
    topics = "、".join(prefs.get("topics") or []) or "AI"
    keywords = "、".join(prefs.get("keywords") or []) or "（无）"
    language = briefing_language(prefs)
    return f"""请为用户「{name}」整理 {today} 的新闻简报。按下面的话题和关键词检索，不要限定在人工智能。

结构化参数（已写入流程，不要改）：
- 话题: {topics}
- 关键词: {keywords}
- 输出语言: {language}
- 召回条数上限: {max_items}
- 最多搜索次数: {max_search_rounds}（一次 web_search 算一轮，不是逐站扫一遍）
- 用户偏爱网站（按历史来源出现次数自动记下，不是手填）: {"、".join(prefs.get("preferred_sites") or []) or "（还没有，先用内置中文站点）"}
- 该用户已检索次数: {int(prefs.get("search_count") or 0)}

{custom_clause(prefs)}

约束：检索词里要同时带上话题和关键词。只收符合自定义要求且相关的新闻。没有相关结果时直接说明，不要用无关新闻补齐。
简报标题必须正好是「{briefing_title(prefs, today)}」，不要另起名字。
总览写满大约 150 字，放在最后。每条摘要写三到四句。不要自己加粗，不要写星号。
"""


def item_polish_prompt(language: str, item: dict, custom: str = "", focus: str = "") -> str:
    extra = f"\n{custom}" if custom.strip() else ""
    focus_line = f"\n用户关注：{focus}。正文必须真的在讲这些话题/关键词，只是顺带提到中国/国内不算。不相关就 SKIP。" if focus.strip() else ""
    return f"""请把下面这条检索结果改写成简报条目。
输出语言：{language}。
先判断这条是否满足自定义要求，以及是否与用户关注的话题/关键词真正相关。若不满足，只输出一行：SKIP{focus_line}
若满足，只输出两行纯文本，不要任何 * 号：
第一行：短标题，不超过 20 个字
第二行：三到四句摘要，保留原文里的金额和百分比，不要加粗，不要写星号。
不要链接，不要编号，不要 ##。材料里没有的不要编。{extra}

标题：{item.get('title') or ''}
摘要：{item.get('summary') or ''}
链接：{item.get('url') or ''}
"""


def overview_prompt(language: str, items: list[dict], custom: str = "") -> str:
    extra = f"\n{custom}" if custom.strip() else ""
    lines = [
        f"根据下面已经写好的条目，用{language}写一段约 150 字的收尾总览。",
        "放在简报最后，串联今天几条新闻的关系或趋势。只输出总览正文，不要标题，不要列表，不要星号。",
    ]
    if extra.strip():
        lines.append(extra.strip())
    lines.append("条目：")
    for item in items:
        lines.append(f"- {item.get('title') or ''}：{item.get('summary') or ''}")
    return "\n".join(lines)


def rewrite_task(language: str, items: list[dict], title: str, custom: str = "") -> str:
    lines = [
        "检索条数已够。下一步只能调用 compose_briefing，不要再搜索或打开网页。",
        f"title 必须正好是「{title}」，不要另起名字。",
        f"输出语言：{language}。overview、items 里的 title 和 summary 必须全部是{language}。",
        "overview 写大约 150 字并放在最后。每条 summary 写三到四句。不要输出星号。",
        "只使用和用户话题、关键词有关且满足自定义要求的条目。",
    ]
    if custom.strip():
        lines.append(custom.strip())
    for item in items:
        lines.append(f"- {item.get('title') or ''} | {item.get('url') or ''} | {item.get('summary') or ''}")
    return "\n".join(lines)
