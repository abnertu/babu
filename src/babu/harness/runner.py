import asyncio
import json
import logging
import time
from collections.abc import Callable
from datetime import datetime
from uuid import uuid4
from weakref import WeakValueDictionary
from zoneinfo import ZoneInfo

from babu.config import HarnessSettings
from babu.harness.conversion import ScriptConverter
from babu.harness.models import (
    TERMINAL_STATUSES,
    EventSink,
    ExecutionContext,
    RunRequest,
    RunState,
    RunStatus,
)
from babu.harness.prompt import MISSING_REPLY, system_prompt
from babu.harness.store import RunCancelled, RunStore
from babu.llm.models import Message
from babu.llm.protocol import ChatClient
from babu.schemas.common import ToolResult
from babu.tools.base import ToolRisk
from babu.tools.executor import ToolExecutor
from babu.tools.registry import ToolNotFound, ToolRegistry

logger = logging.getLogger(__name__)


class RunRunner:
    def __init__(
        self,
        llm: ChatClient,
        registry: ToolRegistry,
        executor: ToolExecutor,
        store: RunStore,
        settings: HarnessSettings,
        preferences: str = "",
        *,
        calendar_clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.llm, self.registry, self.executor, self.store = llm, registry, executor, store
        self.settings, self.preferences = settings, preferences
        self._calendar_clock = calendar_clock
        self._converter = ScriptConverter(settings.output_conversion) if settings.output_conversion else None
        self._locks: WeakValueDictionary[tuple[str, str], asyncio.Lock] = WeakValueDictionary()

    def _lock(self, principal: str, session: str) -> asyncio.Lock:
        return self._locks.setdefault((principal, session), asyncio.Lock())

    async def _emit(self, state: RunState, kind: str, sink: EventSink | None, **data: object) -> None:
        event = await self.store.event(state.id, kind, data)
        logger.info("run_event run_id=%s kind=%s", state.id, kind)
        if sink:
            try:
                await sink(event)
            except Exception:
                logger.warning("transport_event_failed run_id=%s kind=%s", state.id, kind)

    async def start(self, request: RunRequest, sink: EventSink | None = None) -> RunState:
        if len(request.content) > self.settings.max_input_chars:
            raise ValueError("Input exceeds configured size limit")
        async with self._lock(request.principal_id, request.session_id):
            owner = await self.store.acquire(request.principal_id, request.session_id, self.settings.run_timeout_seconds + 60)
            try:
                state = await self.store.by_event(request.source, request.event_id, request.principal_id)
                if state is not None:
                    if state.request.session_id != request.session_id or state.request.content != request.content:
                        raise ValueError("Source event was reused with different input")
                    if state.status != RunStatus.RUNNING and state.status != RunStatus.QUEUED:
                        return state
                else:
                    state = RunState(id=uuid4().hex, request=request, toolset_id=self.registry.fingerprint())
                    now = self._calendar_clock() if self._calendar_clock else datetime.now(ZoneInfo(self.settings.timezone))
                    state.messages = [
                        Message(role="system", content=system_prompt(now, self.settings.timezone, self.preferences))
                    ]
                    history = await self.store.history(
                        request.principal_id,
                        request.session_id,
                        self.settings.session_ttl_seconds,
                        self.settings.history_messages // 2,
                    )
                    for previous in history:
                        state.messages.extend(
                            [
                                Message(
                                    role="user",
                                    content="\n\n".join(
                                        message.content or ""
                                        for message in previous.messages[previous.history_length :]
                                        if message.role == "user"
                                    ),
                                ),
                                Message(role="assistant", content=previous.content),
                            ]
                        )
                    state.history_length = len(state.messages)
                    state.messages.append(Message(role="user", content=request.content))
                    await self.store.create(state)
                    await self._emit(state, "RunStarted", sink)
                return await self._drive(state, sink)
            finally:
                await self.store.release(owner)

    async def resume(
        self,
        run_id: str,
        principal_id: str,
        *,
        approved: bool | None = None,
        content: str | None = None,
        sink: EventSink | None = None,
    ) -> RunState:
        initial = await self.store.get(run_id, principal_id)
        async with self._lock(principal_id, initial.request.session_id):
            owner = await self.store.acquire(principal_id, initial.request.session_id, self.settings.run_timeout_seconds + 60)
            try:
                state = await self.store.get(run_id, principal_id)
                if state.status in TERMINAL_STATUSES:
                    return state
                if state.status == RunStatus.OUTCOME_UNKNOWN:
                    raise ValueError("External outcome is unknown; inspect the operation before starting a new run")
                if state.status == RunStatus.AWAITING_APPROVAL:
                    if approved is None:
                        raise ValueError("Approval decision required")
                    if not approved or time.time() > (state.approval_expires_at or 0):
                        return await self.cancel(run_id, principal_id)
                    operation = f"{state.id}:{state.tool_calls}"
                    state.approved_operations.append(operation)
                    await self._emit(state, "ApprovalGranted", sink, operation_id=operation)
                elif state.status == RunStatus.AWAITING_INPUT:
                    if not content or len(content) > self.settings.max_input_chars:
                        raise ValueError("A clarification within the input size limit is required")
                    state.messages.append(Message(role="user", content=content))
                elif approved is not None or content is not None:
                    raise ValueError("Run is not waiting for this input")
                state.status = RunStatus.RUNNING
                await self.store.save(state)
                await self._emit(state, "RunResumed", sink)
                return await self._drive(state, sink)
            finally:
                await self.store.release(owner)

    async def cancel(self, run_id: str, principal_id: str) -> RunState:
        state = await self.store.cancel(run_id, principal_id)
        if state.status == RunStatus.CANCELLED:
            await self._emit(state, "RunCancelled", None)
        return state

    async def _finish(self, state: RunState, status: RunStatus, content: str, sink: EventSink | None) -> RunState:
        state.status, state.content = status, content
        try:
            await self.store.save(state)
        except RunCancelled:
            current = await self.store.get(state.id, state.request.principal_id)
            state.status, state.content = current.status, current.content
            return state
        await self._emit(state, "RunFinished", sink, status=str(status), successful_tools=state.successful_tools)
        return state

    async def _drive(self, state: RunState, sink: EventSink | None) -> RunState:
        started = time.monotonic()
        elapsed = state.elapsed_seconds
        state.status = RunStatus.RUNNING
        await self.store.save(state)
        try:
            if await self._uncertain_write(state):
                return await self._finish(
                    state,
                    RunStatus.OUTCOME_UNKNOWN,
                    "An earlier write requires external verification before any continuation.",
                    sink,
                )
            remaining = self.settings.run_timeout_seconds - elapsed
            if remaining <= 0:
                return await self._finish(state, RunStatus.EXHAUSTED, "Run time budget exhausted.", sink)
            async with asyncio.timeout(remaining):
                if state.toolset_id != self.registry.fingerprint():
                    if await self._uncertain_write(state):
                        return await self._finish(
                            state, RunStatus.OUTCOME_UNKNOWN, "An earlier write needs external verification.", sink
                        )
                    return await self._finish(state, RunStatus.FAILED, "Tool configuration changed; start a new run.", sink)
                while True:
                    if (await self.store.get(state.id, state.request.principal_id)).status == RunStatus.CANCELLED:
                        raise RunCancelled
                    state.elapsed_seconds = elapsed + time.monotonic() - started
                    if state.pending_calls:
                        paused = await self._tools(state, sink)
                        if paused:
                            return state
                        continue
                    if state.model_calls >= self.settings.max_model_calls:
                        return await self._finish(
                            state, RunStatus.EXHAUSTED, "Model call budget exhausted. Completed actions are preserved.", sink
                        )
                    if (
                        sum(
                            len(m.content or "") + len(m.reasoning_content or "") + len(json.dumps(m.tool_calls or []))
                            for m in state.messages
                        )
                        > self.settings.max_context_chars
                    ):
                        return await self._finish(state, RunStatus.EXHAUSTED, "Context size limit reached.", sink)
                    state.model_calls += 1
                    await self.store.save(state)
                    await self._emit(state, "ModelStarted", sink, model_call=state.model_calls)
                    response = await self.llm.chat(state.messages, self.registry.definitions())
                    if not response.tool_calls:
                        reply = (response.content or "").strip()
                        retry = state.model_calls < self.settings.max_model_calls and state.messages[-1].content != MISSING_REPLY
                        if not reply and retry:
                            # Some models end a tool run without text; ask once for the reply.
                            state.messages.append(Message(role="system", content=MISSING_REPLY))
                            await self.store.save(state)
                            continue
                        if reply and self._converter:
                            reply = self._converter.convert(reply)
                        status = RunStatus.PARTIAL if state.failed_tools else RunStatus.COMPLETED
                        return await self._finish(state, status, reply or "Completed.", sink)
                    state.messages.append(
                        Message(
                            role="assistant",
                            content=response.content,
                            reasoning_content=response.reasoning_content,
                            tool_calls=[
                                {
                                    "id": call.id,
                                    "type": "function",
                                    "function": {
                                        "name": call.name,
                                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                                    },
                                }
                                for call in response.tool_calls
                            ],
                        )
                    )
                    state.pending_calls, state.cursor = response.tool_calls, 0
                    await self.store.save(state)
        except RunCancelled:
            return await self.store.get(state.id, state.request.principal_id)
        except TimeoutError:
            if await self._uncertain_write(state):
                return await self._finish(
                    state, RunStatus.OUTCOME_UNKNOWN, "Run deadline interrupted a write; verify its external outcome.", sink
                )
            return await self._finish(state, RunStatus.EXHAUSTED, "Run time budget exhausted; inspect completed actions.", sink)
        except asyncio.CancelledError:
            await self._emit(state, "RunInterrupted", None)
            raise
        except Exception as exc:
            category = type(exc).__name__
            logger.warning("run_failed run_id=%s category=%s", state.id, category)
            if await self._uncertain_write(state):
                return await self._finish(
                    state, RunStatus.OUTCOME_UNKNOWN, "An interrupted write requires external verification.", sink
                )
            await self._emit(state, "RunError", sink, category=category)
            return await self._finish(state, RunStatus.FAILED, f"Run failed ({category}). Inspect its event trace.", sink)
        finally:
            state.elapsed_seconds = elapsed + time.monotonic() - started
            try:
                await self.store.save(state)
            except RunCancelled:
                pass

    async def _uncertain_write(self, state: RunState) -> bool:
        if not state.pending_calls or state.cursor >= len(state.pending_calls):
            return False
        execution = await self.store.execution(f"{state.id}:{state.tool_calls}")
        if not execution or execution[0] == "completed":
            return False
        return execution[2]

    async def _tools(self, state: RunState, sink: EventSink | None) -> bool:
        while state.cursor < len(state.pending_calls):
            if (await self.store.get(state.id, state.request.principal_id)).status == RunStatus.CANCELLED:
                raise RunCancelled
            if state.tool_calls >= self.settings.max_tool_calls:
                await self._finish(
                    state, RunStatus.EXHAUSTED, "Tool call budget exhausted. Completed actions are preserved.", sink
                )
                return True
            call = state.pending_calls[state.cursor]
            operation = f"{state.id}:{state.tool_calls}"
            try:
                tool = self.registry.get(call.name)
                writes = tool.risk != ToolRisk.READ
            except ToolNotFound:
                writes = False
            execution = await self.store.execution(operation)
            if execution:
                writes = writes or execution[2]
            if execution and execution[0] == "completed" and execution[1] is not None:
                result = execution[1]
            elif execution and writes:
                state.status, state.content = (
                    RunStatus.OUTCOME_UNKNOWN,
                    f"Operation {operation} needs external outcome verification.",
                )
                await self.store.save(state)
                await self._emit(state, "OutcomeUnknown", sink, operation_id=operation, tool=call.name)
                return True
            else:
                try:
                    tool = self.registry.get(call.name)
                    decision = self.executor.policy.evaluate(tool)
                    tool.validate(call.arguments)
                    if decision.requires_confirmation and operation not in state.approved_operations:
                        state.status = RunStatus.AWAITING_APPROVAL
                        state.content = (
                            f"Approval required for {call.name}.\nArguments:\n"
                            f"{json.dumps(call.arguments, ensure_ascii=False, indent=2)}"
                        )
                        state.approval_expires_at = time.time() + self.settings.approval_ttl_seconds
                        await self.store.save(state)
                        await self._emit(
                            state, "ApprovalRequested", sink, operation_id=operation, tool=call.name, arguments=call.arguments
                        )
                        return True
                except (ToolNotFound, ValueError):
                    pass
                await self.store.begin_execution(operation, state.id, call.name, call.arguments, writes)
                await self._emit(state, "ToolStarted", sink, operation_id=operation, tool=call.name)
                context = ExecutionContext(state.request.principal_id, state.request.session_id, state.id, operation)
                try:
                    async with asyncio.timeout(self.settings.tool_timeout_seconds):
                        result = await self.executor.execute(
                            call.name, call.arguments, context, confirmed=operation in state.approved_operations
                        )
                except TimeoutError:
                    result = ToolResult.fail("TOOL_TIMEOUT", "Tool deadline exceeded", outcome_unknown=writes)
                await self.store.finish_execution(operation, result)
            if result.outcome_unknown:
                state.status, state.content = (
                    RunStatus.OUTCOME_UNKNOWN,
                    f"Operation {operation} needs external outcome verification.",
                )
                await self.store.save(state)
                await self._emit(state, "OutcomeUnknown", sink, operation_id=operation, tool=call.name)
                return True
            raw = result.model_dump_json()
            if len(raw.encode()) > self.settings.max_tool_result_bytes:
                # The full receipt stays in the execution ledger; the model gets a bounded summary.
                raw = ToolResult(
                    success=result.success,
                    data={"receipt": operation, "output_omitted": True},
                    error_code=result.error_code,
                    requires_input=result.requires_input,
                ).model_dump_json()
            state.messages.append(Message(role="tool", tool_call_id=call.id, content=raw))
            state.tool_calls += 1
            state.cursor += 1
            if result.success:
                state.successful_tools += 1
            elif not result.requires_input:
                state.failed_tools += 1
            await self.store.save(state)
            await self._emit(state, "ToolFinished", sink, operation_id=operation, tool=call.name, success=result.success)
            if result.requires_input:
                # Close the rest of the assistant's batch without executing actions before clarification.
                for skipped in state.pending_calls[state.cursor :]:
                    state.messages.append(
                        Message(
                            role="tool",
                            tool_call_id=skipped.id,
                            content=ToolResult.fail("SKIPPED", "Waiting for clarification").model_dump_json(),
                        )
                    )
                state.pending_calls, state.cursor = [], 0
                state.status, state.content = (
                    RunStatus.AWAITING_INPUT,
                    result.error_message or "Please clarify the tool arguments.",
                )
                await self.store.save(state)
                await self._emit(state, "InputRequested", sink)
                return True
        state.pending_calls, state.cursor = [], 0
        await self.store.save(state)
        return False
