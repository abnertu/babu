from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Any

from pydantic import BaseModel

from babu.harness.models import ExecutionContext
from babu.llm.models import ToolDefinition
from babu.schemas.common import ToolResult


class ToolRisk(StrEnum):
    READ = "read"
    WRITE_SAFE = "write_safe"
    WRITE_SENSITIVE = "write_sensitive"
    DESTRUCTIVE = "destructive"


ToolHandler = Callable[[Any, ExecutionContext], Awaitable[ToolResult]]


class RegisteredTool:
    def __init__(
        self, name: str, description: str, schema: type[BaseModel], handler: ToolHandler, risk: ToolRisk, binding: str = ""
    ) -> None:
        self.name, self.description, self.schema = name, description, schema
        self.handler, self.risk, self.binding = handler, risk, binding

    def definition(self) -> ToolDefinition:
        return ToolDefinition(name=self.name, description=self.description, parameters=self.schema.model_json_schema())

    def validate(self, arguments: dict[str, Any]) -> BaseModel:
        return self.schema.model_validate(arguments)
