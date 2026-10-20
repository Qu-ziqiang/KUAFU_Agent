from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


def _clean_list(values: list[str], limit: int = 20) -> list[str]:
    seen: list[str] = []
    for item in values:
        text = item.strip()
        if text and text not in seen:
            seen.append(text[:40])
        if len(seen) >= limit:
            break
    return seen


def clean_site(value: str) -> str:
    text = value.strip().lower()
    if "://" in text:
        text = text.split("://", 1)[1]
    text = text.split("/")[0].split("?")[0]
    if text.startswith("www."):
        text = text[4:]
    return text[:80]


def clean_sites(values: list[str], limit: int = 10) -> list[str]:
    seen: list[str] = []
    for item in values:
        host = clean_site(item)
        if host and "." in host and host not in seen:
            seen.append(host)
        if len(seen) >= limit:
            break
    return seen


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    email: str | None = Field(default=None, max_length=120)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("姓名不能为空")
        return text

    @field_validator("email")
    @classmethod
    def strip_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        return text or None


RECALL_LIMIT = 30


class PreferenceIn(BaseModel):
    topics: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    language: str = "zh"
    recall_count: int = 5
    custom_prompt: str = "仅仅输出国内的资讯"

    @field_validator("topics", "keywords")
    @classmethod
    def clean_items(cls, value: list[str]) -> list[str]:
        return _clean_list(value)

    @field_validator("language")
    @classmethod
    def clean_language(cls, value: str) -> str:
        text = value.strip().lower() or "zh"
        if text not in {"zh", "en"}:
            raise ValueError("language 只能是 zh 或 en")
        return text

    @field_validator("recall_count")
    @classmethod
    def clean_recall(cls, value: int) -> int:
        return min(RECALL_LIMIT, max(1, int(value)))

    @field_validator("custom_prompt")
    @classmethod
    def clean_custom(cls, value: str) -> str:
        return value.strip()[:500]
