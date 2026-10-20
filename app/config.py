"""运行配置。密钥与检索参数都从环境变量或 .env 读取，方便以后接用户画像。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

# 中文来源：科技、综合、官方，以及娱乐站点。
DEFAULT_PREFERRED_DOMAINS = (
    "jiqizhixin.com",
    "qbitai.com",
    "36kr.com",
    "tmtpost.com",
    "leiphone.com",
    "wallstreetcn.com",
    "caixin.com",
    "qq.com",
    "cls.cn",
    "thepaper.cn",
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
    "ent.sina.com.cn",
    "yule.sohu.com",
    "ent.163.com",
    "ent.qq.com",
    "ent.ifeng.com",
)


def _optional_hour(raw: str | None) -> int | None:
    if raw is None or raw.strip() == "":
        return None
    hour = int(raw)
    if not 0 <= hour <= 23:
        raise ValueError("DAILY_HOUR 必须是 0 到 23")
    return hour


def _csv(raw: str | None, fallback: tuple[str, ...] = ()) -> list[str]:
    if raw is None or not raw.strip():
        return list(fallback)
    return [part.strip() for part in raw.split(",") if part.strip()]


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    openai_base_url: str
    openai_model: str
    tavily_api_key: str
    database_path: Path
    daily_hour: int | None
    max_agent_steps: int
    search_max_results: int
    search_max_rounds: int
    briefing_min_items: int
    briefing_max_items: int
    preferred_domains: list[str] = field(default_factory=list)
    first_ready_seconds: int = 60
    total_timeout_seconds: int = 180
    foreign_search_rounds: int = 4
    domestic_timeout_seconds: int = 120
    foreign_timeout_seconds: int = 300
    data_dir: Path = field(default_factory=Path)
    schema_path: Path = field(default_factory=Path)

    @property
    def llm_ready(self) -> bool:
        return bool(self.openai_api_key.strip())

    @property
    def search_ready(self) -> bool:
        return bool(self.tavily_api_key.strip())


def get_settings() -> Settings:
    data_dir = ROOT / "data"
    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.deepseek.com").rstrip("/"),
        openai_model=os.getenv("OPENAI_MODEL", "deepseek-flash"),
        tavily_api_key=os.getenv("TAVILY_API_KEY", ""),
        database_path=Path(os.getenv("DATABASE_PATH", str(data_dir / "kuafu.db"))),
        daily_hour=_optional_hour(os.getenv("DAILY_HOUR")),
        max_agent_steps=int(os.getenv("MAX_AGENT_STEPS", "6")),
        search_max_results=int(os.getenv("SEARCH_MAX_RESULTS", "5")),
        search_max_rounds=int(os.getenv("SEARCH_MAX_ROUNDS", "2")),
        briefing_min_items=int(os.getenv("BRIEFING_MIN_ITEMS", "3")),
        briefing_max_items=int(os.getenv("BRIEFING_MAX_ITEMS", "5")),
        preferred_domains=_csv(os.getenv("SEARCH_PREFERRED_DOMAINS"), DEFAULT_PREFERRED_DOMAINS),
        first_ready_seconds=int(os.getenv("FIRST_READY_SECONDS", "60")),
        total_timeout_seconds=int(os.getenv("TOTAL_TIMEOUT_SECONDS", "180")),
        foreign_search_rounds=int(os.getenv("FOREIGN_SEARCH_ROUNDS", "4")),
        domestic_timeout_seconds=int(os.getenv("DOMESTIC_TIMEOUT_SECONDS", "120")),
        foreign_timeout_seconds=int(os.getenv("FOREIGN_TIMEOUT_SECONDS", "300")),
        data_dir=data_dir,
        schema_path=ROOT / "sql" / "schema.sql",
    )
