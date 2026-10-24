import json
import time
from uuid import uuid4

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.sqlite import insert

from babu.harness.models import TERMINAL_STATUSES, RunEvent, RunState, RunStatus
from babu.schemas.common import ToolResult
from babu.storage.db import Database
from babu.storage.models import EventRecord, ExecutionRecord, RunRecord, SessionLease


class SessionBusyError(Exception):
    pass


class RunCancelled(Exception):
    pass


class RunStore:
    def __init__(self, database: Database) -> None:
        self.database = database

    @staticmethod
    def _state(row: RunRecord) -> RunState:
        state = RunState.model_validate_json(row.state_json)
        state.status = RunStatus(row.status)
        if state.status == RunStatus.CANCELLED:
            state.content = "Run cancelled. Already completed external actions are preserved."
        return state

    async def get(self, run_id: str, principal_id: str) -> RunState:
        async with self.database.session() as session:
            row = await session.get(RunRecord, run_id)
            if row is None or row.principal_id != principal_id:
                raise ValueError("Run not found")
            return self._state(row)

    async def by_event(self, source: str, event_id: str, principal_id: str) -> RunState | None:
        async with self.database.session() as session:
            row = (
                await session.execute(
                    select(RunRecord).where(
                        RunRecord.source == source,
                        RunRecord.event_id == event_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return None
            if row.principal_id != principal_id:
                raise ValueError("Event belongs to another principal")
            return self._state(row)

    async def create(self, state: RunState) -> None:
        request = state.request
        async with self.database.session() as session:
            session.add(
                RunRecord(
                    id=state.id,
                    principal_id=request.principal_id,
                    session_id=request.session_id,
                    source=request.source,
                    event_id=request.event_id,
                    status=state.status,
                    state_json=state.model_dump_json(),
                    updated_at=time.time(),
                )
            )
            await session.commit()

    async def save(self, state: RunState) -> None:
        async with self.database.session() as session:
            result = await session.execute(
                update(RunRecord)
                .where(
                    RunRecord.id == state.id,
                    RunRecord.status != RunStatus.CANCELLED,
                )
                .values(state_json=state.model_dump_json(), status=state.status, updated_at=time.time())
            )
            await session.commit()
            if result.rowcount != 1:  # type: ignore[attr-defined]
                raise RunCancelled

    async def cancel(self, run_id: str, principal_id: str) -> RunState:
        state = await self.get(run_id, principal_id)
        if state.status in {RunStatus.COMPLETED, RunStatus.PARTIAL, RunStatus.FAILED, RunStatus.EXHAUSTED}:
            return state
        async with self.database.session() as session:
            await session.execute(
                update(RunRecord)
                .where(
                    RunRecord.id == run_id,
                    RunRecord.status.not_in(TERMINAL_STATUSES),
                )
                .values(status=RunStatus.CANCELLED, updated_at=time.time())
            )
            await session.commit()
        return await self.get(run_id, principal_id)

    async def list_runs(self, principal_id: str, limit: int = 20) -> list[RunState]:
        async with self.database.session() as session:
            rows = (
                (
                    await session.execute(
                        select(RunRecord)
                        .where(RunRecord.principal_id == principal_id)
                        .order_by(RunRecord.updated_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return [self._state(row) for row in rows]

    async def history(self, principal_id: str, session_id: str, ttl: int, limit: int) -> list[RunState]:
        if not limit:
            return []
        async with self.database.session() as session:
            rows = (
                (
                    await session.execute(
                        select(RunRecord)
                        .where(
                            RunRecord.principal_id == principal_id,
                            RunRecord.session_id == session_id,
                            RunRecord.status.in_([RunStatus.COMPLETED, RunStatus.PARTIAL]),
                            RunRecord.updated_at >= time.time() - ttl,
                        )
                        .order_by(RunRecord.updated_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return [self._state(row) for row in reversed(rows)]

    async def event(self, run_id: str, kind: str, data: dict[str, object] | None = None) -> RunEvent:
        async with self.database.session() as session:
            row = EventRecord(
                run_id=run_id, kind=kind, created_at=time.time(), data_json=json.dumps(data or {}, ensure_ascii=False)
            )
            session.add(row)
            await session.commit()
            return RunEvent(sequence=row.sequence, run_id=run_id, kind=kind, created_at=row.created_at, data=data or {})

    async def events(self, run_id: str, principal_id: str) -> list[RunEvent]:
        await self.get(run_id, principal_id)
        async with self.database.session() as session:
            rows = (
                (await session.execute(select(EventRecord).where(EventRecord.run_id == run_id).order_by(EventRecord.sequence)))
                .scalars()
                .all()
            )
            return [
                RunEvent(
                    sequence=row.sequence, run_id=run_id, kind=row.kind, created_at=row.created_at, data=json.loads(row.data_json)
                )
                for row in rows
            ]

    async def execution(self, operation_id: str) -> tuple[str, ToolResult | None, bool] | None:
        async with self.database.session() as session:
            row = await session.get(ExecutionRecord, operation_id)
            if row is None:
                return None
            return row.status, ToolResult.model_validate_json(row.result_json) if row.result_json else None, row.writes

    async def begin_execution(
        self, operation_id: str, run_id: str, name: str, arguments: dict[str, object], writes: bool
    ) -> None:
        async with self.database.session() as session:
            await session.execute(
                insert(ExecutionRecord)
                .values(
                    id=operation_id,
                    run_id=run_id,
                    tool_name=name,
                    writes=writes,
                    arguments_json=json.dumps(arguments, sort_keys=True),
                    status="started",
                )
                .on_conflict_do_nothing(index_elements=["id"])
            )
            await session.commit()

    async def finish_execution(self, operation_id: str, result: ToolResult) -> None:
        async with self.database.session() as session:
            await session.execute(
                update(ExecutionRecord)
                .where(ExecutionRecord.id == operation_id)
                .values(
                    status="unknown" if result.outcome_unknown else "completed",
                    result_json=result.model_dump_json(),
                )
            )
            await session.commit()

    async def receipts(self, run_id: str, principal_id: str) -> list[dict[str, object]]:
        await self.get(run_id, principal_id)
        async with self.database.session() as session:
            rows = (await session.execute(select(ExecutionRecord).where(ExecutionRecord.run_id == run_id))).scalars().all()
            return [
                {
                    "operation_id": row.id,
                    "tool": row.tool_name,
                    "writes": row.writes,
                    "status": row.status,
                    "arguments": json.loads(row.arguments_json),
                    "result": json.loads(row.result_json) if row.result_json else None,
                }
                for row in rows
            ]

    async def acquire(self, principal_id: str, session_id: str, ttl: float) -> str:
        owner = uuid4().hex
        async with self.database.session() as session:
            result = await session.execute(
                insert(SessionLease)
                .values(
                    id=uuid4().hex,
                    principal_id=principal_id,
                    session_id=session_id,
                    owner=owner,
                    expires_at=time.time() + ttl,
                )
                .on_conflict_do_update(
                    index_elements=["principal_id", "session_id"],
                    set_={"owner": owner, "expires_at": time.time() + ttl},
                    where=SessionLease.expires_at < time.time(),
                )
            )
            await session.commit()
            if result.rowcount != 1:  # type: ignore[attr-defined]
                raise SessionBusyError("Session is active in another process; retry after its lease expires")
        return owner

    async def release(self, owner: str) -> None:
        async with self.database.session() as session:
            await session.execute(delete(SessionLease).where(SessionLease.owner == owner))
            await session.commit()
