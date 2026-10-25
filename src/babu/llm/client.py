from collections.abc import Sequence
from typing import Any

import httpx

from .models import AssistantResponse, Message, ToolCall, ToolDefinition


class LLMUnavailable(Exception):
    """The OpenAI-compatible inference endpoint cannot be used."""


class LLMClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        temperature: float,
        max_tokens: int,
        timeout: float = 60,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owns_client = client is None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def health(self) -> bool:
        try:
            response = await self._client.get(f"{self.base_url.rsplit('/v1', 1)[0]}/health")
            return response.is_success
        except httpx.HTTPError:
            return False

    async def chat(self, messages: Sequence[Message], tools: Sequence[ToolDefinition]) -> AssistantResponse:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [message.model_dump(exclude_none=True) for message in messages],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if tools:
            payload["tools"] = [{"type": "function", "function": tool.model_dump()} for tool in tools]
            payload["tool_choice"] = "auto"
        try:
            response = await self._client.post(f"{self.base_url}/chat/completions", json=payload)
            response.raise_for_status()
            body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            status = f" HTTP {response.status_code}" if "response" in locals() else ""
            raise LLMUnavailable(f"LLM request failed:{status} ({type(exc).__name__})") from exc
        try:
            choice = body["choices"][0]
            if choice.get("finish_reason") in {"length", "content_filter"}:
                raise ValueError("Incomplete model response")
            message = choice["message"]
            calls = [
                ToolCall(
                    id=call.get("id", ""),
                    name=call["function"]["name"],
                    arguments=_parse_arguments(call["function"].get("arguments", "{}")),
                )
                for call in message.get("tool_calls", [])
            ]
            if any(not call.id or not call.name for call in calls) or len({call.id for call in calls}) != len(calls):
                raise ValueError("Invalid tool call identifiers")
            result = AssistantResponse(
                content=message.get("content"), reasoning_content=message.get("reasoning_content"), tool_calls=calls
            )
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise LLMUnavailable(f"LLM returned malformed tool arguments ({type(exc).__name__})") from exc
        return result


def _parse_arguments(value: str | dict[str, object]) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    import json

    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("tool arguments must be an object")
    return parsed
