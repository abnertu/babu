from typing import Any

from pydantic import ValidationError

from babu.harness.models import ExecutionContext
from babu.schemas.common import ToolResult

from .base import ToolRisk
from .policy import Policy
from .registry import ToolNotFound, ToolRegistry


class ToolExecutor:
    def __init__(self, registry: ToolRegistry, policy: Policy) -> None:
        self.registry, self.policy = registry, policy

    async def execute(
        self, name: str, arguments: dict[str, Any], context: ExecutionContext, confirmed: bool = False
    ) -> ToolResult:
        try:
            tool = self.registry.get(name)
            args = tool.validate(arguments)
        except ToolNotFound:
            return ToolResult.fail("UNKNOWN_TOOL", f"Unknown tool: {name}")
        except ValidationError:
            return ToolResult.fail("INVALID_ARGUMENTS", "Tool arguments do not match its schema", requires_input=True)
        decision = self.policy.evaluate(tool)
        if not decision.allowed:
            return ToolResult.fail("POLICY_DENIED", decision.reason or "Operation denied")
        if decision.requires_confirmation and not confirmed:
            return ToolResult.fail("CONFIRMATION_REQUIRED", "This operation requires confirmation")
        try:
            return ToolResult.model_validate(await tool.handler(args, context))
        except Exception:
            return ToolResult.fail("TOOL_ERROR", "Tool execution failed", outcome_unknown=tool.risk != ToolRisk.READ)
