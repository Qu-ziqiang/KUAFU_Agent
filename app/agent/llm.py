"""OpenAI 兼容 Chat Completions。"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from app.config import Settings


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str


@dataclass
class LLMMessage:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class LLMError(Exception):
    pass


class OpenAICompatibleClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def chat(self, messages: list[dict], tools: list[dict] | None = None) -> LLMMessage:
        if not self.settings.llm_ready:
            raise LLMError("未配置 OPENAI_API_KEY")
        url = f"{self.settings.openai_base_url}/chat/completions"
        payload = {
            "model": self.settings.openai_model,
            "messages": messages,
            "temperature": 0.3,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        if "deepseek.com" in self.settings.openai_base_url:
            payload["thinking"] = {"type": "disabled"}
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=90) as client:
                response = await client.post(url, headers=headers, json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(f"模型请求失败: {exc}") from exc
        if response.status_code >= 400:
            detail = response.text[:500]
            raise LLMError(f"模型返回 {response.status_code}: {detail}")
        data = response.json()
        try:
            message = data["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError("模型响应缺少 message") from exc
        calls = []
        for item in message.get("tool_calls") or []:
            function = item.get("function") or {}
            calls.append(
                ToolCall(
                    id=str(item.get("id") or ""),
                    name=str(function.get("name") or ""),
                    arguments=function.get("arguments") or "{}",
                )
            )
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return LLMMessage(content=content, tool_calls=calls, raw=message)
