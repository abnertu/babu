import asyncio
from dataclasses import dataclass
from typing import Any, cast

import httpx

from babu.schemas.common import ToolResult


class NotionError(Exception):
    def __init__(self, code: str, message: str, *, outcome_unknown: bool = False) -> None:
        super().__init__(message)
        self.code, self.outcome_unknown = code, outcome_unknown

    def result(self) -> ToolResult:
        return ToolResult.fail(
            self.code,
            str(self),
            outcome_unknown=self.outcome_unknown,
            requires_input=self.code in {"RELATION_NOT_FOUND", "RELATION_AMBIGUOUS"},
        )


@dataclass(frozen=True)
class NotionPage:
    results: list[dict[str, Any]]
    has_more: bool
    next_cursor: str | None


class NotionClient:
    def __init__(self, token: str, client: httpx.AsyncClient | None = None, retry_delay: float = 1) -> None:
        self._client = client or httpx.AsyncClient(
            base_url="https://api.notion.com/v1",
            headers={"Authorization": f"Bearer {token}", "Notion-Version": "2025-09-03"},
            timeout=30,
        )
        self._owns_client, self.retry_delay = client is None, retry_delay

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _request(
        self, method: str, path: str, payload: dict[str, object] | None = None, *, writes: bool = False
    ) -> dict[str, Any]:
        for attempt in range(3):
            retryable = False
            try:
                response = await self._client.request(method, path, json=payload)
                status = response.status_code
                retryable = status == 429 or (not writes and status in {500, 502, 503, 504})
                if retryable and attempt < 2:
                    try:
                        delay = min(max(float(response.headers.get("Retry-After", self.retry_delay * (2**attempt))), 0), 5)
                    except ValueError:
                        delay = self.retry_delay
                    await asyncio.sleep(delay)
                    continue
                if status >= 400:
                    raise NotionError(f"NOTION_{status}", "Notion rejected the request", outcome_unknown=writes and status >= 500)
                try:
                    return cast(dict[str, Any], response.json())
                except ValueError as exc:
                    raise NotionError("NOTION_RESPONSE", "Notion returned an invalid response", outcome_unknown=writes) from exc
            except httpx.TransportError as exc:
                if not writes and attempt < 2:
                    await asyncio.sleep(self.retry_delay * (2**attempt))
                    continue
                code = "NOTION_TIMEOUT" if isinstance(exc, httpx.TimeoutException) else "NOTION_NETWORK"
                raise NotionError(code, "Notion request could not be completed", outcome_unknown=writes) from exc
        raise AssertionError("Unreachable request state")

    async def create_page(
        self, data_source_id: str, properties: dict[str, object], children: list[dict[str, object]] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, object] = {"parent": {"data_source_id": data_source_id}, "properties": properties}
        if children:
            payload["children"] = children
        return await self._request("POST", "/pages", payload, writes=True)

    async def query_data_source(self, data_source_id: str, filter_body: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        body = await self._request("POST", f"/data_sources/{data_source_id}/query", filter_body or {})
        return cast(list[dict[str, Any]], body.get("results", []))

    async def retrieve_data_source(self, data_source_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/data_sources/{data_source_id}")

    async def search(self, query: str, page_size: int = 10) -> NotionPage:
        body = await self._request("POST", "/search", {"query": query, "page_size": page_size})
        return NotionPage(
            cast(list[dict[str, Any]], body.get("results", [])),
            bool(body.get("has_more", False)),
            cast(str | None, body.get("next_cursor")),
        )
