import httpx
import pytest

from babu.plugins.notion.client import NotionClient, NotionError


@pytest.mark.asyncio
async def test_read_retries_transient_response_but_write_does_not():
    attempts = []

    def respond(request):
        attempts.append(request.url.path)
        if len(attempts) == 1 or request.url.path == "/v1/pages":
            return httpx.Response(503)
        return httpx.Response(200, json={"results": [{"id": "page"}]})

    async with httpx.AsyncClient(base_url="https://fixture.invalid/v1", transport=httpx.MockTransport(respond)) as http:
        client = NotionClient("synthetic", client=http, retry_delay=0)
        assert await client.query_data_source("tasks") == [{"id": "page"}]
        assert len(attempts) == 2
        with pytest.raises(NotionError) as error:
            await client.create_page("tasks", {})
        assert error.value.outcome_unknown
        assert len(attempts) == 3


@pytest.mark.asyncio
async def test_write_transport_failure_is_unknown_and_not_retried():
    attempts = 0

    def respond(request):
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("fixture")

    async with httpx.AsyncClient(base_url="https://fixture.invalid/v1", transport=httpx.MockTransport(respond)) as http:
        with pytest.raises(NotionError) as error:
            await NotionClient("synthetic", client=http, retry_delay=0).create_page("tasks", {})
        assert error.value.result().outcome_unknown
        assert attempts == 1
