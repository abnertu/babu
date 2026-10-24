from dataclasses import dataclass

from .base import RegisteredTool, ToolRisk


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    requires_confirmation: bool = False
    reason: str | None = None


class Policy:
    def evaluate(self, tool: RegisteredTool) -> PolicyDecision:
        if tool.risk in {ToolRisk.READ, ToolRisk.WRITE_SAFE}:
            return PolicyDecision(allowed=True)
        return PolicyDecision(allowed=True, requires_confirmation=True, reason="confirmation required")
