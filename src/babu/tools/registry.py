import hashlib
import json

from babu import __version__
from babu.llm.models import ToolDefinition

from .base import RegisteredTool


class ToolNotFound(Exception):
    pass


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, RegisteredTool] = {}

    def register(self, tool: RegisteredTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFound(name) from exc

    def definitions(self) -> list[ToolDefinition]:
        return [tool.definition() for tool in self._tools.values()]

    def fingerprint(self) -> str:
        definitions = [
            {"definition": tool.definition().model_dump(), "risk": tool.risk, "binding": tool.binding}
            for _, tool in sorted(self._tools.items())
        ]
        payload = json.dumps({"version": __version__, "tools": definitions}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()
