"""OpenAI-compatible chat-completions client (Groq by default)."""

from __future__ import annotations

from typing import Protocol

import httpx

from service015.config import Settings
from service015.errors import DependencyUnavailable


class LlmUnavailable(DependencyUnavailable):
    def __init__(self, message: str) -> None:
        super().__init__("llm_unavailable", message)


class Llm(Protocol):
    def chat(self, messages: list[dict[str, str]]) -> str: ...


class OpenAICompatLlm:
    """POST {base_url}/chat/completions. Any failure raises LlmUnavailable; nothing is fabricated."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self._transport = transport
        self._timeout = timeout

    @classmethod
    def from_settings(cls, settings: Settings) -> "OpenAICompatLlm":
        return cls(settings.llm_base_url, settings.llm_api_key, settings.llm_model)

    def chat(self, messages: list[dict[str, str]]) -> str:
        if not self.api_key:
            raise LlmUnavailable("LLM_API_KEY is not configured")
        try:
            with httpx.Client(transport=self._transport, timeout=self._timeout) as client:
                resp = client.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    json={"model": self.model, "messages": messages, "temperature": 0.1},
                )
        except httpx.HTTPError as exc:
            raise LlmUnavailable(f"LLM endpoint unreachable: {type(exc).__name__}") from exc
        if resp.status_code != 200:
            raise LlmUnavailable(f"LLM endpoint answered HTTP {resp.status_code}")
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LlmUnavailable("LLM endpoint returned a malformed response") from exc
        if not isinstance(content, str) or not content.strip():
            raise LlmUnavailable("LLM endpoint returned an empty answer")
        return content.strip()
