"""Evaluate synthetic workflows through Babu against an explicit loopback model."""

import argparse
import asyncio
import copy
import hashlib
import json
import tempfile
import time
from datetime import datetime
from importlib.resources import files
from pathlib import Path
from typing import Any, Literal
from zoneinfo import ZoneInfo

import httpx
from pydantic import AwareDatetime, Field, model_validator

from babu.config import HarnessSettings, StrictModel
from babu.harness.models import RunRequest, RunState, RunStatus
from babu.harness.runner import RunRunner
from babu.harness.store import RunStore
from babu.llm.client import LLMClient
from babu.plugins.notion.client import NotionClient
from babu.plugins.notion.config import NotionSettings
from babu.plugins.notion.plugin import NotionPlugin
from babu.storage.db import Database
from babu.tools.executor import ToolExecutor
from babu.tools.policy import Policy
from babu.tools.registry import ToolRegistry


class Step(StrictModel):
    action: Literal["start", "answer", "resume", "cancel"] = "start"
    content: str | None = None
    approved: bool | None = None

    @model_validator(mode="after")
    def validate_action(self) -> "Step":
        if self.action in {"start", "answer"} and (not self.content or self.approved is not None):
            raise ValueError("start and answer require content and cannot approve an operation")
        if self.action == "cancel" and (self.content is not None or self.approved is not None):
            raise ValueError("cancel cannot supply input or approval")
        return self


class Fault(StrictModel):
    method: Literal["GET", "POST"] = "POST"
    path: str
    status: int = Field(ge=400, le=599)
    after: int = Field(default=0, ge=0)
    occurrences: int = Field(default=1, gt=0)


class NotionFixture(StrictModel):
    schemas: dict[str, dict[str, Any]]
    pages: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    faults: list[Fault] = Field(default_factory=list)
    delay_seconds: float = Field(default=0, ge=0, le=5)


class Expectations(StrictModel):
    statuses: list[RunStatus | list[RunStatus]] = Field(min_length=1)
    writes: list[dict[str, Any]] = Field(default_factory=list)
    writes_after_steps: list[list[dict[str, Any]]] | None = None
    reply_contains: list[str] = Field(default_factory=list)
    reply_excludes: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    review_rubric: str = Field(min_length=1)


class Case(StrictModel):
    id: str = Field(min_length=1)
    steps: list[Step] = Field(min_length=1)
    notion: NotionFixture
    expect: Expectations

    @model_validator(mode="after")
    def validate_steps(self) -> "Case":
        if self.steps[0].action != "start" or len(self.expect.statuses) != len(self.steps):
            raise ValueError("cases must start a run and declare one expected status per step")
        if self.expect.writes_after_steps is not None and len(self.expect.writes_after_steps) != len(self.steps):
            raise ValueError("write checkpoints require one expectation per step")
        if any(isinstance(status, list) and not status for status in self.expect.statuses):
            raise ValueError("status alternatives cannot be empty")
        return self


class Suite(StrictModel):
    format_version: Literal[1] = 1
    calendar: AwareDatetime
    cases: list[Case] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_cases(self) -> "Suite":
        if len({case.id for case in self.cases}) != len(self.cases):
            raise ValueError("case IDs must be unique")
        return self


def subset(expected: Any, actual: Any) -> bool:
    """Match JSON subsets, retaining array order and length."""
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(key in actual and subset(value, actual[key]) for key, value in expected.items())
    if isinstance(expected, list):
        return (
            isinstance(actual, list)
            and len(expected) == len(actual)
            and all(subset(a, b) for a, b in zip(expected, actual, strict=True))
        )
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        return isinstance(actual, (int, float)) and not isinstance(actual, bool) and expected == actual
    return type(expected) is type(actual) and expected == actual


def property_value(prop: dict[str, Any]) -> Any:
    kind = prop.get("type") or next((key for key in prop if key != "id"), "")
    value = prop.get(kind)
    if kind in {"title", "rich_text"}:
        return "".join(item.get("plain_text", item.get("text", {}).get("content", "")) for item in (value or []))
    if kind in {"status", "select"}:
        return value.get("name") if value else None
    if kind == "date":
        return value.get("start") if value else None
    return value


def write_check(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> tuple[bool, bool]:
    remaining = list(actual)
    complete = True
    for expected_write in expected:
        matched_index = next(
            (index for index, actual_write in enumerate(remaining) if subset(expected_write, actual_write)), None
        )
        if matched_index is None:
            complete = False
        else:
            remaining.pop(matched_index)
    return complete and not remaining, bool(remaining)


def matches(page: dict[str, Any], condition: dict[str, Any]) -> bool:
    if not condition:
        return True
    if "and" in condition:
        return all(matches(page, item) for item in condition["and"])
    if "or" in condition:
        return any(matches(page, item) for item in condition["or"])
    value = property_value(page.get("properties", {}).get(condition["property"], {}))
    operations = next(value for key, value in condition.items() if key != "property")
    for operator, wanted in operations.items():
        if operator == "equals":
            result = value == wanted
        elif operator == "does_not_equal":
            result = value != wanted
        elif operator == "contains":
            result = wanted.casefold() in str(value or "").casefold()
        elif operator == "on_or_after":
            result = value is not None and str(value)[:10] >= wanted
        elif operator == "on_or_before":
            result = value is not None and str(value)[:10] <= wanted
        elif operator == "is_empty":
            result = (value is None or value == "" or value == []) == wanted
        else:
            raise ValueError(f"unsupported fixture filter: {operator}")
        if not result:
            return False
    return True


class FakeNotion:
    """Mock HTTP only; the real plugin constructs every tool request."""

    def __init__(self, fixture: NotionFixture) -> None:
        self.fixture = fixture
        self.pages = copy.deepcopy(fixture.pages)
        self.requests: list[dict[str, Any]] = []
        self.writes: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self._fault_calls = [0] * len(fixture.faults)

    async def handle(self, request: httpx.Request) -> httpx.Response:
        try:
            return await self._respond(request)
        except Exception as exc:
            self.errors.append(f"{type(exc).__name__}: {exc}")
            raise

    async def _respond(self, request: httpx.Request) -> httpx.Response:
        if self.fixture.delay_seconds:
            await asyncio.sleep(self.fixture.delay_seconds)
        path = request.url.path.removeprefix("/v1")
        body = json.loads(request.content) if request.content else {}
        record: dict[str, Any] = {"method": request.method, "path": path, "body": body}
        self.requests.append(record)
        for index, fault in enumerate(self.fixture.faults):
            if request.method == fault.method and path == fault.path:
                occurrence = self._fault_calls[index]
                self._fault_calls[index] += 1
                if fault.after <= occurrence < fault.after + fault.occurrences:
                    record["status"] = fault.status
                    return httpx.Response(
                        fault.status, json={"message": "Synthetic fixture failure"}, headers={"Retry-After": "0"}
                    )
        parts = path.strip("/").split("/")
        if len(parts) == 2 and parts[0] == "data_sources" and request.method == "GET":
            source = parts[1]
            record["status"] = 200 if source in self.fixture.schemas else 404
            return httpx.Response(record["status"], json=self.fixture.schemas.get(source, {}))
        if len(parts) == 3 and parts[0] == "data_sources" and parts[2] == "query" and request.method == "POST":
            source = parts[1]
            if source not in self.fixture.schemas:
                record["status"] = 404
                return httpx.Response(404, json={"message": "Unknown synthetic data source"})
            selected = [page for page in self.pages.get(source, []) if matches(page, body.get("filter", {}))]
            for sort in reversed(body.get("sorts", [])):
                selected.sort(
                    key=lambda page: str(property_value(page.get("properties", {}).get(sort["property"], {})) or ""),
                    reverse=sort.get("direction") == "descending",
                )
            size = int(body.get("page_size", 100))
            record["status"] = 200
            return httpx.Response(200, json={"results": selected[:size], "has_more": len(selected) > size, "next_cursor": None})
        if path == "/pages" and request.method == "POST":
            source = body.get("parent", {}).get("data_source_id")
            schema = self.fixture.schemas.get(source, {}).get("properties", {})
            properties = body.get("properties", {})
            if not schema or any(name not in schema for name in properties):
                record["status"] = 400
                return httpx.Response(400, json={"message": "Invalid synthetic page properties"})
            normalized = copy.deepcopy(properties)
            for name, value in normalized.items():
                value["type"] = schema[name]["type"]
                for item in value.get("title", []) + value.get("rich_text", []):
                    item.setdefault("plain_text", item.get("text", {}).get("content", ""))
            page = {"id": f"synthetic-{len(self.writes) + 1}", "url": "https://fixture.invalid/page", "properties": normalized}
            self.writes.append(copy.deepcopy(body))
            self.pages.setdefault(source, []).append(page)
            record["status"] = 200
            return httpx.Response(200, json=page)
        record["status"] = 400
        message = f"unsupported fixture request: {request.method} {path}"
        raise ValueError(message)


class RecordingTransport(httpx.AsyncBaseTransport):
    def __init__(self, seed: int | None, inner: httpx.AsyncBaseTransport | None = None) -> None:
        self.seed = seed
        self.inner = inner if inner is not None else httpx.AsyncHTTPTransport(trust_env=False)
        self.calls: list[dict[str, Any]] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        payload = json.loads(await request.aread())
        if self.seed is not None:
            payload["seed"] = self.seed
        headers = {
            key: value for key, value in request.headers.items() if key.lower() not in {"content-length", "transfer-encoding"}
        }
        forwarded = httpx.Request(request.method, request.url, json=payload, headers=headers, extensions=request.extensions)
        record: dict[str, Any] = {"request": payload}
        self.calls.append(record)
        started = time.monotonic()
        try:
            response = await self.inner.handle_async_request(forwarded)
            await response.aread()
            record["status"] = response.status_code
            try:
                record["response"] = response.json()
            except ValueError:
                record["response_text"] = response.text[:4096]
            return response
        except BaseException as error:
            record["error"] = type(error).__name__
            raise
        finally:
            record["elapsed_seconds"] = time.monotonic() - started

    async def aclose(self) -> None:
        await self.inner.aclose()


async def evaluate_case(
    case: Case,
    *,
    calendar: datetime,
    base_url: str,
    model: str,
    settings: HarnessSettings,
    temperature: float = 0.2,
    max_tokens: int = 4096,
    timeout: float = 600,
    seed: int | None = None,
    model_transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, Any]:
    endpoint = httpx.URL(base_url)
    if (
        endpoint.scheme != "http"
        or endpoint.host not in {"127.0.0.1", "::1"}
        or endpoint.username
        or endpoint.password
        or endpoint.query
    ):
        raise ValueError("evaluation requires an explicit HTTP loopback endpoint without credentials or query parameters")
    fake = FakeNotion(case.notion)
    recorder = RecordingTransport(seed, model_transport)
    states: list[dict[str, Any]] = []
    writes_by_step: list[list[dict[str, Any]]] = []
    receipts: dict[str, list[dict[str, object]]] = {}
    error: str | None = None
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="babu-evaluation-") as scratch:
        database = Database(f"sqlite+aiosqlite:///{Path(scratch) / 'state.db'}")
        await database.create_schema()
        try:
            async with (
                httpx.AsyncClient(
                    base_url="https://fixture.invalid/v1", transport=httpx.MockTransport(fake.handle), trust_env=False
                ) as notion_http,
                httpx.AsyncClient(transport=recorder, timeout=timeout, trust_env=False) as model_http,
            ):
                llm = LLMClient(base_url, model, temperature, max_tokens, timeout, client=model_http)
                notion_settings = NotionSettings.model_validate(
                    {"enabled": True, "default": {"tasks": {"data_source_id": "tasks"}, "finance": {"data_source_id": "finance"}}}
                )
                plugin = NotionPlugin(notion_settings, NotionClient("", client=notion_http, retry_delay=0))
                registry = ToolRegistry()
                plugin.register(registry)
                store = RunStore(database)
                preferences = files("babu.harness").joinpath("default_preferences.md").read_text(encoding="utf-8")
                harness = RunRunner(
                    llm,
                    registry,
                    ToolExecutor(registry, Policy()),
                    store,
                    settings.model_copy(update={"state_dir": Path(scratch)}),
                    preferences,
                    calendar_clock=lambda: calendar.astimezone(ZoneInfo(settings.timezone)),
                )
                state: RunState | None = None
                for index, step in enumerate(case.steps):
                    if step.action == "start" or (
                        step.action == "answer" and state is not None and state.status == RunStatus.COMPLETED
                    ):
                        state = await harness.start(
                            RunRequest(
                                principal_id="evaluation:synthetic",
                                session_id=case.id,
                                source="evaluation",
                                event_id=f"{case.id}:{index}",
                                content=step.content or "",
                            )
                        )
                    elif state is None:
                        raise ValueError("run control requires an earlier start")
                    elif step.action == "answer":
                        if state.status != RunStatus.AWAITING_INPUT:
                            raise ValueError("answer requires a completed clarification or an awaiting-input run")
                        state = await harness.resume(state.id, "evaluation:synthetic", content=step.content)
                    elif step.action == "resume":
                        state = await harness.resume(
                            state.id, "evaluation:synthetic", approved=step.approved, content=step.content
                        )
                    else:
                        state = await harness.cancel(state.id, "evaluation:synthetic")
                    states.append(state.model_dump(mode="json"))
                    writes_by_step.append(copy.deepcopy(fake.writes))
                    receipts[state.id] = await store.receipts(state.id, "evaluation:synthetic")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            await database.close()
    elapsed = time.monotonic() - started
    write_matches, unexpected_write = write_check(case.expect.writes, fake.writes)
    checkpoint_matches = True
    if case.expect.writes_after_steps is not None:
        checkpoint_matches = len(writes_by_step) == len(case.expect.writes_after_steps)
        for expected_writes, actual_writes in zip(case.expect.writes_after_steps, writes_by_step, strict=False):
            matched, unexpected = write_check(expected_writes, actual_writes)
            checkpoint_matches = checkpoint_matches and matched
            unexpected_write = unexpected_write or unexpected
    content = states[-1]["content"] if states else ""
    used_tools = {receipt["tool"] for batch in receipts.values() for receipt in batch}
    checks = {
        "fixture_supported": not fake.errors,
        "statuses": len(states) == len(case.expect.statuses)
        and all(
            state["status"] in (expected_status if isinstance(expected_status, list) else [expected_status])
            for state, expected_status in zip(states, case.expect.statuses, strict=False)
        ),
        "writes": write_matches,
        "write_checkpoints": checkpoint_matches,
        "reply_contains": all(text in content for text in case.expect.reply_contains),
        "reply_excludes": all(text not in content for text in case.expect.reply_excludes),
        "required_tools": all(name in used_tools for name in case.expect.required_tools),
    }
    return {
        "case_id": case.id,
        "elapsed_seconds": elapsed,
        "states": states,
        "receipts": receipts,
        "notion_requests": fake.requests,
        "fixture_errors": fake.errors,
        "successful_writes": fake.writes,
        "writes_by_step": writes_by_step,
        "client_calls": recorder.calls,
        "checks": checks,
        "automatic_checks_passed": error is None and all(checks.values()),
        "critical_failures": ["unexpected_or_duplicate_successful_write"] if unexpected_write else [],
        "rubric_review_required": True,
        "review_rubric": case.expect.review_rubric,
        "driver_error": error,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--max-tokens", type=int, default=4096)
    parser.add_argument("--timeout", type=float, default=600)
    parser.add_argument("--run-timeout", type=float, default=900)
    parser.add_argument("--output-conversion", choices=["s2t", "s2tw", "s2hk"], default="s2tw")
    args = parser.parse_args(argv)
    if not 0 <= args.temperature <= 2 or args.max_tokens <= 0 or args.timeout <= 0:
        parser.error("invalid temperature, token budget or timeout")
    raw = args.cases.read_bytes()
    suite = Suite.model_validate_json(raw)
    settings = HarnessSettings(run_timeout_seconds=args.run_timeout, output_conversion=args.output_conversion)
    results: list[dict[str, Any]] = []
    report = {
        "suite_sha256": hashlib.sha256(raw).hexdigest(),
        "model": args.model,
        "base_url": args.base_url,
        "seed": args.seed,
        "temperature": args.temperature,
        "max_tokens": args.max_tokens,
        "client_timeout_seconds": args.timeout,
        "calendar": suite.calendar.isoformat(),
        "harness": settings.model_dump(mode="json", exclude={"state_dir", "instructions_path"}),
        "rubric_review_required": True,
        "results": results,
    }
    with args.output.open("x", encoding="utf-8") as output:
        output.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    async def run() -> None:
        for case in suite.cases:
            results.append(
                await evaluate_case(
                    case,
                    calendar=suite.calendar,
                    base_url=args.base_url,
                    model=args.model,
                    settings=settings,
                    seed=args.seed,
                    temperature=args.temperature,
                    max_tokens=args.max_tokens,
                    timeout=args.timeout,
                )
            )
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    asyncio.run(run())
    return 0 if all(result["automatic_checks_passed"] for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
