import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from pydantic import Field

from babu.config import StrictModel
from babu.llm.models import Message, ToolCall


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_INPUT = "awaiting_input"
    OUTCOME_UNKNOWN = "outcome_unknown"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    EXHAUSTED = "exhausted"
    CANCELLED = "cancelled"


TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.PARTIAL, RunStatus.FAILED, RunStatus.EXHAUSTED, RunStatus.CANCELLED}


class RunRequest(StrictModel):
    principal_id: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=256)
    source: str = Field(min_length=1, max_length=64)
    event_id: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1)


@dataclass(frozen=True)
class ExecutionContext:
    principal_id: str
    session_id: str
    run_id: str
    operation_id: str


class RunState(StrictModel):
    id: str
    request: RunRequest
    status: RunStatus = RunStatus.QUEUED
    messages: list[Message] = Field(default_factory=list)
    pending_calls: list[ToolCall] = Field(default_factory=list)
    cursor: int = 0
    toolset_id: str = ""
    history_length: int = 0
    model_calls: int = 0
    tool_calls: int = 0
    approved_operations: list[str] = Field(default_factory=list)
    approval_expires_at: float | None = None
    content: str = ""
    successful_tools: int = 0
    failed_tools: int = 0
    elapsed_seconds: float = 0


class RunEvent(StrictModel):
    sequence: int
    run_id: str
    kind: str
    created_at: float = Field(default_factory=time.time)
    data: dict[str, object] = Field(default_factory=dict)


EventSink = Callable[[RunEvent], Awaitable[None]]
