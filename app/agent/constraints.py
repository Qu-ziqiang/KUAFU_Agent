"""把能结构化的自定义要求解析成参数，其余仍靠提示词硬约束。"""

from __future__ import annotations

import re
from urllib.parse import urlparse

CN_HOST_SUFFIXES = (
    ".cn",
    "cctv.com",
    "people.com.cn",
    "xinhuanet.com",
    "chinanews.com.cn",
    "thepaper.cn",
    "36kr.com",
    "jiqizhixin.com",
    "qbitai.com",
    "leiphone.com",
    "tmtpost.com",
    "cls.cn",
    "gmw.cn",
    "1905.com",
    "sohu.com",
    "163.com",
    "sina.com.cn",
    "ifeng.com",
    "qq.com",
    "bilibili.com",
    "douban.com",
    "weibo.com",
    "mgtv.com",
    "iqiyi.com",
    "youku.com",
    "yangshipin.cn",
)

CN_MARKERS = (
    "中国",
    "国内",
    "内地",
    "本土",
    "工信部",
    "工博会",
    "央视",
    "新华社",
    "人民日报",
    "国庆",
    "北京",
    "上海",
    "深圳",
    "杭州",
    "广州",
)


def parse_custom_constraints(custom_prompt: str) -> dict:
    text = (custom_prompt or "").strip()
    geo = None
    if re.search(r"(只看|仅看|只要|仅仅|仅输出|只输出).{0,6}(国外|海外|国际|境外)", text):
        geo = "foreign"
    elif re.search(r"(只看|仅看|只要|仅仅|仅输出|只输出).{0,6}(国内|境内)", text):
        geo = "domestic"
    elif "国外" in text or "海外" in text or "国际" in text:
        if "国内" not in text:
            geo = "foreign"
    elif "国内" in text or "境内" in text:
        if "国外" not in text and "海外" not in text:
            geo = "domestic"
    return {"geo": geo, "raw": text}


def host_of(url: str) -> str:
    return (urlparse(url).hostname or "").lower()


def is_chinese_source(url: str, title: str = "", summary: str = "") -> bool:
    host = host_of(url)
    if any(host == item or host.endswith(item) for item in CN_HOST_SUFFIXES):
        return True
    blob = f"{title} {summary}"
    return any(marker in blob for marker in CN_MARKERS)


def matches_geo(url: str, title: str, summary: str, geo: str | None) -> bool:
    if not geo:
        return True
    chinese = is_chinese_source(url, title, summary)
    if geo == "foreign":
        return not chinese
    if geo == "domestic":
        return chinese
    return True


def search_query_suffix(geo: str | None) -> str:
    if geo == "foreign":
        return "international overseas global -中国"
    if geo == "domestic":
        return "中国 国内"
    return ""


def emphasize_keywords(text: str, terms: list[str]) -> str:
    """给话题和关键词加粗。一次匹配，长词优先，已经在 ** ** 里的不再套一层。"""
    wanted = sorted({str(term).strip() for term in terms if str(term).strip() and len(str(term).strip()) >= 2}, key=len, reverse=True)
    if not wanted or not text:
        return text
    pattern = "|".join(re.escape(term) for term in wanted)
    parts = re.split(r"(\*\*.*?\*\*)", text)
    rendered = []
    for part in parts:
        if len(part) >= 4 and part.startswith("**") and part.endswith("**"):
            rendered.append(part)
            continue
        rendered.append(re.sub(pattern, lambda match: f"**{match.group(0)}**", part, flags=re.IGNORECASE))
    return "".join(rendered)


def emphasize_title(title: str) -> str:
    text = re.sub(r"[*#]+", "", str(title or "")).strip()
    text = re.split(r"[。！？]", text)[0].strip()
    if not text:
        return ""
    return f"**{text[:40]}**"


def emphasize_numbers(summary: str) -> str:
    """只加粗金额、百分比和带万/亿的数量，不给日期、分数、次数加粗。"""
    text = str(summary or "").replace("*", "")
    pattern = (
        r"(?<!\d)("
        r"\d+(?:\.\d+)?%"
        r"|\d+(?:\.\d+)?(?:万亿|亿|万)(?:美元|美金|元|人民币)?"
        r"|\$?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?:美元|美金|元|人民币)?"
        r")(?!\d)"
    )
    return re.sub(pattern, r"**\1**", text)
