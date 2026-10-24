import asyncio
import json

import pytest

from babu.config import HarnessSettings
from babu.harness.models import RunRequest, RunState, RunStatus
from babu.harness.runner import RunRunner
from babu.harness.store import RunStore, SessionBusyError
from babu.llm.models import AssistantResponse, ToolCall
from babu.schemas.common import StrictModel, ToolResult
from babu.storage.db import Database
from babu.tools.base import RegisteredTool, ToolRisk
from babu.tools.executor import ToolExecutor
from babu.tools.policy import Policy
from babu.tools.registry import ToolRegistry


class Args(StrictModel):
    title: str


class ScriptedLLM:
    def __init__(self, responses):
        self.responses = list(responses)
        self.histories = []

    async def chat(self, messages, tools):
        self.histories.append([m.model_dump() for m in messages])
        return self.responses.pop(0)


def call(title="fixture"):
    return AssistantResponse(tool_calls=[ToolCall(id="call-1", name="task", arguments={"title": title})])


def request(event="event-1", principal="local:1", content="Create a task"):
    return RunRequest(principal_id=principal, session_id="session", source="fixture", event_id=event, content=content)


@pytest.fixture
async def database(tmp_path):
    db = Database(f"sqlite+aiosqlite:///{tmp_path / 'state.db'}")
    await db.create_schema()
    yield db
    await db.close()


def runner(db, llm, handler, risk=ToolRisk.WRITE_SAFE, store=None, **limits):
    registry = ToolRegistry()
    registry.register(RegisteredTool("task", "Create task", Args, handler, risk))
    return RunRunner(llm, registry, ToolExecutor(registry, Policy()), store or RunStore(db), HarnessSettings(**limits))


@pytest.mark.asyncio
async def test_successful_receipt_survives_restart_and_duplicate_delivery(database):
    writes = []

    async def handler(args, context):
        writes.append(context.operation_id)
        return ToolResult.ok({"task_id": "external-task"})

    class CrashAfterReceipt(RunStore):
        async def finish_execution(self, operation_id, result):
            await super().finish_execution(operation_id, result)
            raise asyncio.CancelledError

    llm = ScriptedLLM([call(), AssistantResponse(content="Created")])
    first = runner(database, llm, handler, store=CrashAfterReceipt(database))
    with pytest.raises(asyncio.CancelledError):
        await first.start(request())
    # Reopen SQLite and reconstruct the runner, keeping no runtime checkpoint in memory.
    reopened = Database(str(database.engine.url))
    try:
        restored = runner(reopened, llm, handler)
        result = await restored.start(request())
        assert result.status == RunStatus.COMPLETED
        assert result.content == "Created"
        assert len(writes) == 1
        assert json.loads(llm.histories[-1][-1]["content"])["data"]["task_id"] == "external-task"
        again = await restored.start(request())
        assert again.id == result.id
        assert len(llm.histories) == 2
        assert len(writes) == 1
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_interrupted_write_is_not_repeated_without_a_receipt(database):
    attempts = 0

    async def handler(args, context):
        nonlocal attempts
        attempts += 1
        raise asyncio.CancelledError

    llm = ScriptedLLM([call()])
    first = runner(database, llm, handler)
    with pytest.raises(asyncio.CancelledError):
        await first.start(request())
    restored = runner(database, llm, handler)
    result = await restored.start(request())
    assert result.status == RunStatus.OUTCOME_UNKNOWN
    assert attempts == 1
    with pytest.raises(ValueError, match="unknown"):
        await restored.resume(result.id, "local:1")


@pytest.mark.asyncio
async def test_approval_is_bound_to_principal_and_saved_operation(database):
    executed = []

    async def handler(args, context):
        executed.append((args.title, context.operation_id))
        return ToolResult.ok()

    llm = ScriptedLLM([call("original"), AssistantResponse(content="Done")])
    harness = runner(database, llm, handler, ToolRisk.WRITE_SENSITIVE)
    pending = await harness.start(request())
    assert pending.status == RunStatus.AWAITING_APPROVAL
    assert executed == []
    with pytest.raises(ValueError, match="not found"):
        await harness.resume(pending.id, "local:2", approved=True)
    restored = runner(database, llm, handler, ToolRisk.WRITE_SENSITIVE)
    complete = await restored.resume(pending.id, "local:1", approved=True)
    assert complete.status == RunStatus.COMPLETED
    assert executed == [("original", f"{pending.id}:0")]
    await restored.resume(pending.id, "local:1", approved=True)
    assert len(executed) == 1


@pytest.mark.asyncio
async def test_exhaustion_preserves_success_instead_of_reporting_generic_failure(database):
    writes = []

    async def handler(args, context):
        writes.append(args.title)
        return ToolResult.ok({"id": "created"})

    harness = runner(database, ScriptedLLM([call()]), handler, max_model_calls=1)
    result = await harness.start(request())
    assert result.status == RunStatus.EXHAUSTED
    assert result.successful_tools == 1
    assert writes == ["fixture"]
    assert "preserved" in result.content


@pytest.mark.asyncio
async def test_history_is_scoped_to_principal_and_session(database):
    async def handler(args, context):
        return ToolResult.ok()

    llm = ScriptedLLM(
        [
            AssistantResponse(content="private answer"),
            AssistantResponse(content="other answer"),
            AssistantResponse(content="next answer"),
        ]
    )
    harness = runner(database, llm, handler)
    await harness.start(request(content="private input"))
    await harness.start(request("event-2", "local:2"))
    await harness.start(request("event-3"))
    assert len(llm.histories[1]) == 2
    assert "private input" not in str(llm.histories[1])
    assert [m["content"] for m in llm.histories[2]][1:3] == ["private input", "private answer"]


@pytest.mark.asyncio
async def test_clarification_resumes_without_executing_rest_of_batch(database):
    executed = []

    async def handler(args, context):
        if args.title == "ambiguous":
            return ToolResult.fail("AMBIGUOUS", "Which account?", requires_input=True)
        executed.append(args.title)
        return ToolResult.ok()

    response = call("ambiguous")
    response.tool_calls.append(ToolCall(id="call-2", name="task", arguments={"title": "must-not-run"}))
    llm = ScriptedLLM([response, call("clarified"), AssistantResponse(content="Done")])
    harness = runner(database, llm, handler)
    state = await harness.start(request())
    assert state.status == RunStatus.AWAITING_INPUT
    assert executed == []
    result = await harness.resume(state.id, "local:1", content="Use cash")
    assert result.status == RunStatus.COMPLETED
    assert executed == ["clarified"]
    assert llm.histories[1][-1]["content"] == "Use cash"
    assert len([m for m in llm.histories[1] if m["role"] == "tool"]) == 2


@pytest.mark.asyncio
async def test_session_lease_excludes_other_processes_and_allows_stale_recovery(database):
    store = RunStore(database)
    owner = await store.acquire("local:1", "session", 60)
    with pytest.raises(SessionBusyError):
        await RunStore(database).acquire("local:1", "session", 60)
    await store.release(owner)
    await store.acquire("local:1", "session", -1)
    recovered = await store.acquire("local:1", "session", 60)
    await store.release(recovered)


@pytest.mark.asyncio
async def test_cancellation_cannot_be_undone_by_inflight_model_response(database):
    entered, release = asyncio.Event(), asyncio.Event()

    class SlowLLM:
        async def chat(self, messages, tools):
            entered.set()
            await release.wait()
            return AssistantResponse(content="Must not win")

    async def handler(args, context):
        raise AssertionError("No tool should execute")

    harness = runner(database, SlowLLM(), handler)
    task = asyncio.create_task(harness.start(request()))
    await entered.wait()
    state = (await harness.store.list_runs("local:1"))[0]
    await harness.cancel(state.id, "local:1")
    release.set()
    result = await task
    assert result.status == RunStatus.CANCELLED
    assert (await harness.store.get(state.id, "local:1")).status == RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_whole_run_timeout_during_write_is_unknown(database):
    async def handler(args, context):
        await asyncio.sleep(10)
        return ToolResult.ok()

    harness = runner(database, ScriptedLLM([call()]), handler, run_timeout_seconds=1, tool_timeout_seconds=10)
    result = await harness.start(request())
    assert result.status == RunStatus.OUTCOME_UNKNOWN
    assert result.successful_tools == 0


@pytest.mark.asyncio
async def test_oversized_tool_output_preserves_receipt_and_bounds_model_context(database):
    async def handler(args, context):
        return ToolResult.ok({"long": "x" * 5000})

    llm = ScriptedLLM([call(), AssistantResponse(content="Done")])
    harness = runner(database, llm, handler, max_tool_result_bytes=512)
    result = await harness.start(request())
    output = json.loads(llm.histories[-1][-1]["content"])
    assert output["success"] is True
    assert output["data"]["output_omitted"] is True
    receipt = await harness.store.execution(output["data"]["receipt"])
    assert len(receipt[1].data["long"]) == 5000
    assert result.status == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_same_session_requests_are_serialized_and_share_completed_history(database):
    entered, release = asyncio.Event(), asyncio.Event()
    histories = []

    class OrderedLLM:
        async def chat(self, messages, tools):
            histories.append([m.content for m in messages])
            if len(histories) == 1:
                entered.set()
                await release.wait()
            return AssistantResponse(content=f"reply {len(histories)}")

    async def handler(args, context):
        return ToolResult.ok()

    harness = runner(database, OrderedLLM(), handler)
    first = asyncio.create_task(harness.start(request(content="first")))
    await entered.wait()
    second = asyncio.create_task(harness.start(request("event-2", content="second")))
    release.set()
    await asyncio.gather(first, second)
    assert histories[1][1:3] == ["first", "reply 1"]


@pytest.mark.asyncio
async def test_expired_approval_cannot_execute_and_receipts_are_owner_scoped(database):
    executed = []

    async def handler(args, context):
        executed.append(args.title)
        return ToolResult.ok()

    harness = runner(database, ScriptedLLM([call()]), handler, ToolRisk.DESTRUCTIVE)
    state = await harness.start(request())
    state.approval_expires_at = 0
    await harness.store.save(state)
    cancelled = await harness.resume(state.id, "local:1", approved=True)
    assert cancelled.status == RunStatus.CANCELLED
    assert executed == []
    with pytest.raises(ValueError, match="not found"):
        await harness.store.receipts(state.id, "local:2")


@pytest.mark.asyncio
async def test_plugin_removal_cannot_reclassify_interrupted_write_as_read(database):
    async def handler(args, context):
        raise asyncio.CancelledError

    original = runner(database, ScriptedLLM([call()]), handler)
    with pytest.raises(asyncio.CancelledError):
        await original.start(request())
    state = (await original.store.list_runs("local:1"))[0]
    empty = ToolRegistry()
    changed = RunRunner(ScriptedLLM([]), empty, ToolExecutor(empty, Policy()), RunStore(database), HarnessSettings())
    result = await changed.resume(state.id, "local:1")
    assert result.status == RunStatus.OUTCOME_UNKNOWN
    assert (await changed.store.execution(f"{state.id}:0"))[0] == "started"


@pytest.mark.asyncio
async def test_tool_binding_change_invalidates_pending_approval(database):
    executed = []

    async def handler(args, context):
        executed.append(args.title)
        return ToolResult.ok()

    original = runner(database, ScriptedLLM([call()]), handler, ToolRisk.WRITE_SENSITIVE)
    state = await original.start(request())
    changed = runner(database, ScriptedLLM([]), handler, ToolRisk.WRITE_SENSITIVE)
    changed.registry.get("task").binding = "different destination"
    result = await changed.resume(state.id, "local:1", approved=True)
    assert result.status == RunStatus.FAILED
    assert "configuration changed" in result.content
    assert executed == []


@pytest.mark.asyncio
async def test_cancellation_does_not_replace_concurrent_completion(database):
    class CompleteBetweenReadAndCancel(RunStore):
        completed = False

        async def get(self, run_id, principal_id):
            state = await super().get(run_id, principal_id)
            if not self.completed:
                self.completed = True
                late = state.model_copy(update={"status": RunStatus.COMPLETED, "content": "Already done"})
                await self.save(late)
            return state

    state = RunState(id="fixture-run", request=request())
    store = CompleteBetweenReadAndCancel(database)
    await store.create(state)
    result = await store.cancel(state.id, "local:1")
    assert result.status == RunStatus.COMPLETED
    assert result.content == "Already done"


def test_calendar_names_monday_based_weeks_on_sunday():
    from datetime import datetime

    from babu.harness.prompt import calendar

    text = calendar(datetime(2026, 10, 25, 23, 30))
    assert "Today: 2026-10-25 (Sunday)" in text
    assert "This week: 2026-10-19 to 2026-10-25" in text
    assert "Next week: 2026-10-26 to 2026-11-01" in text
