"""联网检索工具。新闻只留在当次调用的内存里，不写成本地语料。"""

from __future__ import annotations

import ipaddress
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

MAX_RESULTS = 5
MAX_PAGE_CHARS = 3500
MIN_ITEMS = 3
MAX_ITEMS = 5
FETCH_BYTES = 800_000


@dataclass
class SearchOptions:
    """检索与简报条数都可配；preferred_domains 以后可接用户画像。"""

    max_results: int = MAX_RESULTS
    max_rounds: int = 2
    min_items: int = MIN_ITEMS
    max_items: int = MAX_ITEMS
    preferred_domains: list[str] = field(default_factory=list)
    prefer_chinese: bool = True
    focus_terms: list[str] = field(default_factory=list)
    geo: str | None = None
    custom_prompt: str = ""

HIGH_HOSTS = (
    "openai.com",
    "deepmind.google",
    "research.google",
    "huggingface.co",
    "arxiv.org",
    "nvidia.com",
    "anthropic.com",
    "mistral.ai",
    "jiqizhixin.com",
    "qbitai.com",
    "36kr.com",
    "sohu.com",
    "cctv.com",
    "people.com.cn",
    "xinhuanet.com",
    "chinanews.com.cn",
    "163.com",
    "sina.com.cn",
    "ifeng.com",
    "gmw.cn",
    "1905.com",
    "douban.com",
    "weibo.com",
    "bilibili.com",
    "mgtv.com",
    "iqiyi.com",
    "youku.com",
    "mtime.com",
    "maoyan.com",
    "yangshipin.cn",
    "mit.edu",
    "stanford.edu",
    "nature.com",
    "science.org",
    "theverge.com",
    "technologyreview.com",
    "techcrunch.com",
    "arstechnica.com",
)

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "用搜索引擎查新闻。返回标题、原始链接、来源和摘要。不要把结果当成本地文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "检索词，用用户关心的话题和关键词"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_webpage",
            "description": "打开一条 http(s) 原文并抽取正文。如果是 PDF 或图片，请改用 ocr_document。",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ocr_document",
            "description": "解析 PDF 或图片。PDF 先抽文字层；图片和扫描件走本机 OCR。",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "trace_source",
            "description": "根据原始链接判断来源站点和可信度，不重新编造内容。",
            "parameters": {
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compose_briefing",
            "description": "把已经检索到的新闻收成简报。每条必须用工具返回过的原始链接。正文不要用加粗符号。",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "overview": {"type": "string", "description": "开头总览，大约 150 字，平实中文，不要 **"},
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "url": {"type": "string"},
                                "summary": {
                                    "type": "string",
                                    "description": "三到四句，把时间、地点、人物和事件写全，必须来自工具结果，不要 ** 或装饰符号",
                                },
                            },
                            "required": ["title", "url", "summary"],
                        },
                    },
                },
                "required": ["title", "overview", "items"],
            },
        },
    },
]


class ToolError(Exception):
    pass


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self._skip += 1
        if tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"} and self._skip:
            self._skip -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        if self._skip:
            return
        text = data.strip()
        if text:
            self.parts.append(text)


def host_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def normalize_url(url: str) -> str:
    parsed = urlparse(url.strip())
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme.lower()}://{host_of(url)}{path}"


def public_http_url(url: str) -> str:
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ToolError("只接受 http(s) 链接")
    host = parsed.hostname.lower()
    if host in {"localhost", "0.0.0.0"} or host.endswith(".local"):
        raise ToolError("不能访问本机地址")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved):
        raise ToolError("不能访问内网地址")
    return url.strip()


def credibility_for(url: str) -> str:
    host = host_of(url)
    if any(host == item or host.endswith("." + item) for item in HIGH_HOSTS):
        return "high"
    if urlparse(url).scheme == "https":
        return "medium"
    return "low"


def dumps(data) -> str:
    return json.dumps(data, ensure_ascii=False)


def plain_text(value: str) -> str:
    """清理装饰符号；保留 **加粗**，给标题和数字用。"""
    text = str(value or "")
    text = text.replace("__", "").replace("`", "")
    text = re.sub(r"^#{1,6}\s*", "", text.strip())
    return re.sub(r"[ \t]+", " ", text).replace("\n", " ").strip()


def extract_html(raw: str) -> tuple[str, str]:
    parser = _TextExtractor()
    parser.feed(raw)
    parser.close()
    text = re.sub(r"\s+", " ", " ".join(parser.parts)).strip()
    title = re.sub(r"\s+", " ", parser.title).strip()
    return title, text[:MAX_PAGE_CHARS]


def tavily_search(
    api_key: str,
    query: str,
    max_results: int = MAX_RESULTS,
    include_domains: list[str] | None = None,
) -> list[dict]:
    if not api_key.strip():
        raise ToolError("未配置 TAVILY_API_KEY")
    body: dict = {
        "api_key": api_key,
        "query": query,
        "search_depth": "basic",
        "topic": "news",
        "max_results": max_results,
        "include_answer": False,
    }
    if include_domains:
        body["include_domains"] = include_domains[:40]

    def _once(payload: dict) -> list[dict]:
        try:
            with httpx.Client(timeout=20) as client:
                response = client.post("https://api.tavily.com/search", json=payload)
        except httpx.HTTPError as exc:
            raise ToolError(f"搜索请求失败: {exc}") from exc
        if response.status_code >= 400:
            raise ToolError(f"搜索返回 {response.status_code}: {response.text[:300]}")
        found = []
        for item in response.json().get("results") or []:
            url = str(item.get("url") or "").strip()
            if not url:
                continue
            found.append(
                {
                    "title": str(item.get("title") or "").strip(),
                    "url": url,
                    "source": host_of(url),
                    "summary": str(item.get("content") or "").strip()[:500],
                }
            )
        return found

    found = _once(body)
    # 中文站优先没命中时，立刻放开域名再搜一次，保证有材料可写。
    if not found and include_domains:
        body.pop("include_domains", None)
        found = _once(body)
    return found


def download(url: str) -> tuple[bytes, str]:
    public_http_url(url)
    try:
        with httpx.Client(timeout=25, follow_redirects=True) as client:
            response = client.get(url, headers={"User-Agent": "kuafu-news-agent/1.0"})
    except httpx.HTTPError as exc:
        raise ToolError(f"打开链接失败: {exc}") from exc
    if response.status_code >= 400:
        raise ToolError(f"打开链接返回 {response.status_code}")
    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    return response.content[:FETCH_BYTES], content_type


def looks_like_pdf(url: str, content_type: str, data: bytes) -> bool:
    return "pdf" in content_type or url.lower().split("?")[0].endswith(".pdf") or data.startswith(b"%PDF")


def looks_like_image(url: str, content_type: str) -> bool:
    if content_type.startswith("image/"):
        return True
    path = url.lower().split("?")[0]
    return path.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".bmp"))


def pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ToolError("未安装 pypdf，无法读取 PDF") from exc
    import io

    reader = PdfReader(io.BytesIO(data))
    pages = []
    for index, page in enumerate(reader.pages[:4]):
        pages.append(page.extract_text() or "")
        if index >= 3:
            break
    return re.sub(r"\s+", " ", "\n".join(pages)).strip()


def ocr_image_bytes(data: bytes) -> str:
    try:
        import pytesseract
        from PIL import Image
    except ImportError as exc:
        raise ToolError("图片 OCR 需要安装 pytesseract 和 Pillow，以及系统里的 Tesseract") from exc
    import io

    try:
        image = Image.open(io.BytesIO(data))
        text = pytesseract.image_to_string(image, lang="chi_sim+eng")
    except Exception as exc:
        raise ToolError(f"OCR 失败: {exc}") from exc
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise ToolError("OCR 没有识别出文字")
    return text[:MAX_PAGE_CHARS]


class NewsTools:
    def __init__(
        self,
        search: Callable[[str], list[dict]] | None = None,
        fetcher=None,
        api_key: str = "",
        options: SearchOptions | None = None,
    ):
        self._search = search
        self._fetcher = fetcher
        self.api_key = api_key
        self.options = options or SearchOptions()
        self.seen: dict[str, dict] = {}
        self.queries: list[str] = []
        self.search_rounds = 0
        self.composed: dict | None = None
        self.last_new_items: list[dict] = []
        self.needs_language_pass: bool = False

    def remember(self, url: str, **fields) -> None:
        key = normalize_url(url)
        current = self.seen.get(key, {"url": url})
        current.update({name: value for name, value in fields.items() if value is not None})
        current["url"] = url
        self.seen[key] = current

    def lookup(self, url: str) -> dict | None:
        return self.seen.get(normalize_url(url))

    def focus_terms(self) -> list[str]:
        terms = []
        for raw in self.options.focus_terms:
            term = str(raw or "").strip()
            if len(term) < 2 or term in terms:
                continue
            terms.append(term)
        return terms

    def term_variants(self, term: str) -> list[str]:
        """常见别名：中文稿常写「检索增强生成」而不是 RAG。"""
        key = term.lower().strip()
        aliases = {
            "rag": ["rag", "retrieval-augmented", "retrieval augmented", "检索增强生成", "检索增强"],
            "llm": ["llm", "large language model", "大语言模型", "大模型"],
            "agent": ["agent", "智能体"],
            "开源模型": ["开源模型", "开源大模型", "开源 llm", "开源大语言模型"],
        }
        found = aliases.get(key) or aliases.get(term)
        return list(dict.fromkeys([term, *(found or [])]))

    def on_topic(self, title: str, summary: str = "") -> bool:
        """话题/关键词相关性（含别名）。改写后的正文也要再验，防止检索摘要夹带查询词。"""
        terms = self.focus_terms()
        if not terms:
            return True
        blob = f"{title or ''} {summary or ''}".lower()
        hits = [
            term
            for term in terms
            if any(variant.lower() in blob for variant in self.term_variants(term))
        ]
        if not hits:
            return False
        if len(terms) == 1:
            return True
        # 多个关注点：最长的那个（通常是主题）要命中，或至少命中两个词。
        # 避免只靠摘要里残留的一个短检索词把无关新闻放进来。
        longest = max(terms, key=len)
        longest_hit = any(variant.lower() in blob for variant in self.term_variants(longest))
        return longest_hit or len(hits) >= 2

    def effective_min_items(self) -> int:
        if self.focus_terms():
            return 1
        return self.options.min_items

    def recall_ready(self) -> bool:
        """相关结果达到条数上限就召回。无关新闻不算。"""
        ready = 0
        target = self.options.max_items
        for item in self.seen.values():
            url = item.get("url") or ""
            title = (item.get("title") or "").strip()
            summary = (item.get("summary") or item.get("text") or "").strip()
            if not url.startswith("http") or not title or not summary:
                continue
            if not self.on_topic(title, summary):
                continue
            ready += 1
            if ready >= target:
                return True
        return False

    def draft_items(self, limit: int | None = None) -> list[dict]:
        """limit 默认跟召回上限一致；改写阶段可加大，把候选池用完再补搜。"""
        cap = self.options.max_items if limit is None else max(1, int(limit))
        items = []
        for item in self.seen.values():
            url = item.get("url") or ""
            title = (item.get("title") or "").strip()
            summary = (item.get("summary") or item.get("text") or "").strip()
            if not url.startswith("http") or not title:
                continue
            if not self.on_topic(title, summary):
                continue
            items.append(
                {
                    "title": title,
                    "url": url,
                    "source": item.get("source") or host_of(url),
                    "published_at": item.get("published_at"),
                    "summary": summary[:500],
                    "ocr": bool(item.get("ocr")),
                    "ocr_source_type": item.get("ocr_source_type"),
                    "credibility": item.get("credibility") or credibility_for(url),
                }
            )
            if len(items) >= cap:
                break
        return items

    def draft_content(self, title: str | None = None) -> str:
        items = self.draft_items()
        heading = plain_text(title) or "每日 AI 简报（生成中）"
        lines = [heading, "", "正在根据检索结果整理，可先点开来源。", ""]
        for item in items:
            summary = plain_text(item["summary"]) or "摘要生成中"
            lines.append(f"- {plain_text(item['title'])}：{summary} [打开原文]({item['url']})")
        return "\n".join(lines).strip()

    def web_search(self, query: str) -> str:
        text = (query or "").strip()
        if not text:
            raise ToolError("query 不能为空")
        if self.search_rounds >= self.options.max_rounds:
            raise ToolError(
                f"已搜索 {self.search_rounds} 次，达到上限 {self.options.max_rounds}。"
                "请立刻用已有结果调用 compose_briefing，不要再搜。"
            )
        missing = [term for term in self.focus_terms() if term not in text]
        if missing:
            text = f"{' '.join(missing)} {text}"
        from app.agent.constraints import matches_geo, search_query_suffix

        suffix = search_query_suffix(self.options.geo)
        if suffix:
            text = f"{text} {suffix}"
        elif self.options.prefer_chinese and "中文" not in text and "中国" not in text:
            text = f"{text} 中文"
        # 国外资讯不要再锁中文站；国内才用优先域名。
        use_domains = self.options.prefer_chinese and self.options.geo != "foreign"
        domains = self.options.preferred_domains if use_domains else []
        found = (
            self._search(text)
            if self._search
            else tavily_search(
                self.api_key,
                text,
                max_results=self.options.max_results,
                include_domains=domains or None,
            )
        )
        self.search_rounds += 1
        self.queries.append(text)
        new_items = []
        relevant = []
        for item in found:
            title = str(item.get("title") or "")
            summary = str(item.get("summary") or "")
            if not self.on_topic(title, summary):
                continue
            if not matches_geo(str(item.get("url") or ""), title, summary, self.options.geo):
                continue
            relevant.append(item)
        for item in relevant:
            before = normalize_url(item["url"]) not in self.seen
            self.remember(
                item["url"],
                title=item.get("title") or "",
                source=item.get("source") or host_of(item["url"]),
                summary=item.get("summary") or "",
                credibility=credibility_for(item["url"]),
            )
            if before:
                known = self.lookup(item["url"]) or {}
                new_items.append(
                    {
                        "title": known.get("title") or item.get("title"),
                        "url": known.get("url") or item["url"],
                        "source": known.get("source"),
                        "summary": (known.get("summary") or "")[:500],
                        "ocr": False,
                        "credibility": known.get("credibility"),
                    }
                )
        self.last_new_items = new_items
        if not relevant:
            terms = "、".join(self.focus_terms()) or "当前话题"
            return f"没有与「{terms}」相关的结果。不要用其他领域的新闻凑满条数，可以换更贴切的检索词再搜一次。"
        return dumps(relevant)

    def fetch_webpage(self, url: str) -> str:
        target = public_http_url(url)
        if self.lookup(target) is None:
            raise ToolError("这个链接不在本次搜索结果里")
        if self._fetcher:
            page = self._fetcher(target)
            content_type = page.get("content_type") or "text/html"
            title = page.get("title") or ""
            text = page.get("text") or ""
            data = b""
        else:
            data, content_type = download(target)
            if looks_like_pdf(target, content_type, data) or looks_like_image(target, content_type):
                kind = "pdf" if looks_like_pdf(target, content_type, data) else "image"
                raise ToolError(f"这是{kind}，请改用 ocr_document")
            title, text = extract_html(data.decode("utf-8", errors="replace"))
        if not text:
            raise ToolError("没有抽出正文")
        self.remember(
            target,
            title=title or None,
            text=text[:MAX_PAGE_CHARS],
            summary=text[:220],
            content_type=content_type,
            ocr=False,
        )
        known = self.lookup(target) or {}
        self.last_new_items = [
            {
                "title": known.get("title") or title,
                "url": known.get("url") or target,
                "source": known.get("source") or host_of(target),
                "summary": text[:220],
                "ocr": False,
                "credibility": known.get("credibility") or credibility_for(target),
            }
        ]
        return dumps({"title": title, "url": target, "content_type": content_type, "text": text[:MAX_PAGE_CHARS]})

    def ocr_document(self, url: str) -> str:
        target = public_http_url(url)
        if self.lookup(target) is None:
            raise ToolError("这个链接不在本次搜索结果里")
        if self._fetcher:
            page = self._fetcher(target)
            text = page.get("text") or ""
            kind = page.get("ocr_source_type") or "pdf"
            if not text:
                raise ToolError("没有解析出文字")
        else:
            data, content_type = download(target)
            if looks_like_image(target, content_type):
                text = ocr_image_bytes(data)
                kind = "image"
            elif looks_like_pdf(target, content_type, data):
                text = pdf_text(data)
                kind = "pdf"
                if len(text) < 80:
                    raise ToolError("PDF 几乎没有文字层。扫描件需要服务器安装 Tesseract，当前无法继续识别")
            else:
                raise ToolError("这个地址不是 PDF 或图片")
        self.remember(target, text=text[:MAX_PAGE_CHARS], ocr=True, ocr_source_type=kind, credibility=credibility_for(target))
        return dumps({"url": target, "ocr": True, "ocr_source_type": kind, "text": text[:MAX_PAGE_CHARS]})

    def trace_source(self, url: str) -> str:
        target = public_http_url(url)
        known = self.lookup(target)
        if known is None:
            raise ToolError("这个链接不在本次检索结果里，不能当作来源")
        payload = {
            "url": known.get("url") or target,
            "source": known.get("source") or host_of(target),
            "credibility": credibility_for(target),
            "published_at": known.get("published_at"),
        }
        self.remember(target, credibility=payload["credibility"], source=payload["source"])
        return dumps(payload)

    def auto_compose_from_seen(self, title: str, overview: str) -> dict | None:
        candidates = []
        for item in self.seen.values():
            url = item.get("url") or ""
            item_title = (item.get("title") or "").strip()
            summary = (item.get("summary") or item.get("text") or "").strip()
            if not url.startswith("http") or not item_title or not summary:
                continue
            if not self.on_topic(item_title, summary):
                continue
            candidates.append(
                {
                    "title": item_title,
                    "url": url,
                    "summary": summary[:500],
                }
            )
            if len(candidates) >= self.options.max_items:
                break
        if not candidates:
            if not self.focus_terms():
                return None
            terms = "、".join(self.focus_terms())
            self.composed = {
                "title": plain_text(title) or "新闻简报",
                "overview": f"没有找到与「{terms}」相关的新闻。",
                "content": f"{plain_text(title) or '新闻简报'}\n\n没有找到与「{terms}」相关的新闻。",
                "items": [],
                "query": "；".join(self.queries),
            }
            return self.composed
        self.compose_briefing(title, overview, candidates[: self.options.max_items], allow_short=True)
        return self.composed

    def compose_briefing(
        self,
        title: str,
        overview: str,
        items: list,
        allow_short: bool = False,
    ) -> str:
        if isinstance(items, str):
            items = json.loads(items)
        if not isinstance(items, list):
            raise ToolError("items 必须是数组")
        min_items = 1 if allow_short else self.effective_min_items()
        max_items = self.options.max_items
        if len(items) > max_items:
            raise ToolError(f"简报需要 {min_items} 到 {max_items} 条，当前 {len(items)} 条")
        # 条数不够且还能搜时，禁止提前收成，逼模型继续 web_search。
        if (
            not allow_short
            and len(items) < max_items
            and self.search_rounds < self.options.max_rounds
        ):
            raise ToolError(
                f"目标 {max_items} 条，当前只有 {len(items)} 条，还差 {max_items - len(items)} 条。"
                "请继续 web_search 补齐后再调用 compose_briefing。"
            )
        kept = []
        for raw in items:
            if not isinstance(raw, dict):
                raise ToolError("每条新闻必须是对象")
            url = str(raw.get("url") or "").strip()
            known = self.lookup(url)
            if known is None:
                raise ToolError(f"链接未在本次检索中出现: {url}")
            kept.append(raw)
        items = kept
        if self.focus_terms() and not items:
            terms = "、".join(self.focus_terms())
            heading = plain_text(title) or "新闻简报"
            lead = f"没有找到与「{terms}」相关的新闻。"
            self.composed = {
                "title": heading,
                "overview": lead,
                "content": f"{heading}\n\n{lead}",
                "items": [],
                "query": "；".join(self.queries),
            }
            return f"没有与「{terms}」相关的条目，已拒绝用无关新闻凑数"
        if not (min_items <= len(items) <= max_items):
            raise ToolError(f"简报需要 {min_items} 到 {max_items} 条，当前 {len(items)} 条")
        cleaned = []
        for raw in items:
            if not isinstance(raw, dict):
                raise ToolError("每条新闻必须是对象")
            url = str(raw.get("url") or "").strip()
            known = self.lookup(url)
            if known is None:
                raise ToolError(f"链接未在本次检索中出现: {url}")
            summary = str(raw.get("summary") or "").strip()
            item_title = plain_text(str(raw.get("title") or known.get("title") or ""))
            summary = plain_text(summary)
            if not item_title or not summary:
                raise ToolError("每条都要有标题和一两句摘要")
            cleaned.append(
                {
                    "title": item_title,
                    "url": known.get("url") or url,
                    "source": known.get("source") or host_of(url),
                    "published_at": known.get("published_at"),
                    "summary": summary[:520],
                    "ocr": bool(known.get("ocr")),
                    "ocr_source_type": known.get("ocr_source_type"),
                    "credibility": known.get("credibility") or credibility_for(url),
                }
            )
        from app.agent.constraints import emphasize_keywords, emphasize_numbers, emphasize_title

        heading = plain_text(title) or "每日 AI 简报"
        lead = emphasize_keywords(emphasize_numbers(plain_text(overview)), self.focus_terms())
        lines = [heading, ""]
        for item in cleaned:
            # 条目本身存纯文本；** 只写进简报正文，避免来源卡片露出星号。
            plain_title = re.sub(r"[*#]+", "", item["title"]).strip()
            plain_summary = item["summary"].replace("*", "")
            item["title"] = plain_title
            item["summary"] = plain_summary
            lines.append(
                f"- {emphasize_title(plain_title)}："
                f"{emphasize_keywords(emphasize_numbers(plain_summary), self.focus_terms())} "
                f"[打开原文]({item['url']})"
            )
        if lead:
            lines.extend(["", lead])
        content = "\n".join(lines).strip()
        self.composed = {
            "title": heading,
            "overview": lead,
            "content": content,
            "items": cleaned,
            "query": "；".join(self.queries),
        }
        return f"已汇总 {len(cleaned)} 条，每条都保留了原始链接"

    def execute(self, name: str, arguments: dict) -> str:
        arguments = arguments or {}
        if name == "web_search":
            return self.web_search(str(arguments.get("query") or ""))
        if name == "fetch_webpage":
            return self.fetch_webpage(str(arguments.get("url") or ""))
        if name == "ocr_document":
            return self.ocr_document(str(arguments.get("url") or ""))
        if name == "trace_source":
            return self.trace_source(str(arguments.get("url") or ""))
        if name == "compose_briefing":
            return self.compose_briefing(
                str(arguments.get("title") or ""),
                str(arguments.get("overview") or ""),
                arguments.get("items") or [],
            )
        raise ToolError(f"未知工具: {name}")
