from pydantic import BaseModel, ConfigDict, Field


class Message(BaseModel):
    role: str
    content: str | None = None
    tool_call_id: str | None = None
    tool_calls: list[dict[str, object]] | None = None


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, object] = Field(default_factory=dict)


class AssistantResponse(BaseModel):
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)


class ToolDefinition(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    description: str
    parameters: dict[str, object]
