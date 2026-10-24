import os
import sys
from uuid import uuid4

from babu.harness.models import RunEvent, RunRequest


def principal_id() -> str:
    return f"local:{os.getuid()}"


def request(content: str, session_id: str) -> RunRequest:
    return RunRequest(principal_id=principal_id(), session_id=session_id, source="cli", event_id=uuid4().hex, content=content)


async def progress(event: RunEvent) -> None:
    if event.kind in {"ToolStarted", "ApprovalRequested", "OutcomeUnknown"}:
        print(f"[{event.run_id}] {event.kind}: {event.data.get('tool', '')}", file=sys.stderr)
