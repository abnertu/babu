from typing import Protocol

from .models import AssistantResponse, Message, ToolDefinition


class ChatClient(Protocol):
    async def chat(self, messages: list[Message], tools: list[ToolDefinition]) -> AssistantResponse: ...
