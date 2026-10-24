import httpx
import pytest

from babu.llm.client import LLMClient, LLMUnavailable
from babu.llm.models import Message


@pytest.mark.asyncio
async def test_malformed_tool_arguments_are_rejected():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json={
                "choices": [
                    {"message": {"content": None, "tool_calls": [{"id": "1", "function": {"name": "tool", "arguments": "[]"}}]}}
                ]
            },
        )
    )
    client = httpx.AsyncClient(transport=transport)
    llm = LLMClient("http://llama/v1", "model", 0.2, 100, client=client)
    with pytest.raises(LLMUnavailable):
        await llm.chat([Message(role="user", content="x")], [])
    await client.aclose()


@pytest.mark.parametrize(
    "choice",
    [
        {"message": {"content": "unfinished"}, "finish_reason": "length"},
        {"message": {"content": "blocked"}, "finish_reason": "content_filter"},
        {},
    ],
)
@pytest.mark.asyncio
async def test_incomplete_model_responses_never_appear_successful(choice):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"choices": [choice]}))
    ) as http:
        client = LLMClient("http://fixture/v1", "fixture", 0, 100, client=http)
        with pytest.raises(LLMUnavailable):
            await client.chat([Message(role="user", content="fixture")], [])
