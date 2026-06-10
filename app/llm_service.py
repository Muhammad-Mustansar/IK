import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from app.config import get_settings


class LLMService:
    def __init__(self) -> None:
        self._settings = get_settings()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._settings.openai_api_key}",
            "Content-Type": "application/json",
        }

    async def create_embedding(self, text: str) -> list[float]:
        payload = {
            "model": self._settings.openai_embedding_model,
            "input": text,
        }
        async with httpx.AsyncClient(
            base_url=self._settings.openai_base_url,
            timeout=60.0,
        ) as client:
            response = await client.post("/embeddings", json=payload, headers=self._headers())
            response.raise_for_status()
            data = response.json()
            return data["data"][0]["embedding"]

    async def chat_completion(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> str:
        payload = {
            "model": self._settings.openai_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        async with httpx.AsyncClient(
            base_url=self._settings.openai_base_url,
            timeout=120.0,
        ) as client:
            response = await client.post("/chat/completions", json=payload, headers=self._headers())
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    async def chat_completion_stream(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.3,
        max_tokens: int = 1024,
    ) -> AsyncIterator[str]:
        payload: dict[str, Any] = {
            "model": self._settings.openai_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        async with httpx.AsyncClient(
            base_url=self._settings.openai_base_url,
            timeout=120.0,
        ) as client:
            async with client.stream(
                "POST",
                "/chat/completions",
                json=payload,
                headers=self._headers(),
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if data_str == "[DONE]":
                        break
                    chunk = json.loads(data_str)
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content


def get_llm_service() -> LLMService:
    return LLMService()
