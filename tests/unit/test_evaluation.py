import json
import os
import subprocess
import sys
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from babu.config import HarnessSettings
from babu.evaluation import Case, Fault, evaluate_case


def case(**changes):
    data = {
        "id": "synthetic-task",
        "steps": [{"content": "明天提醒我繳水費。"}],
        "notion": {
            "schemas": {
                "tasks": {"properties": {"Name": {"type": "title"}, "Due": {"type": "date"}}},
                "finance": {"properties": {"Name": {"type": "title"}, "Amount": {"type": "number"}}},
            }
        },
        "expect": {
            "statuses": ["completed"],
            "writes": [{"parent": {"data_source_id": "tasks"}, "properties": {"Due": {"date": {"start": "2026-10-26"}}}}],
            "required_tools": ["notion_create_task"],
            "reply_contains": ["任務"],
            "review_rubric": "Confirm the requested task, date, and truthful Traditional Chinese reply.",
        },
    }
    data.update(changes)
    return Case.model_validate(data)


def response(content=None, calls=None, finish="stop"):
    return {"choices": [{"message": {"content": content, "tool_calls": calls or []}, "finish_reason": finish}]}


def tool(name="notion_create_task", arguments=None, identifier="task-1"):
    return {
        "id": identifier,
        "function": {
            "name": name,
            "arguments": json.dumps(arguments if arguments is not None else {"title": "繳水費", "due_at": "2026-10-26"}),
        },
    }


async def evaluate(item, replies, **options):
    pending = iter(replies)

    def respond(request):
        return httpx.Response(200, json=next(pending))

    return await evaluate_case(
        item,
        calendar=datetime.fromisoformat("2026-10-25T12:00:00+00:00"),
        base_url="http://127.0.0.1:18080/v1",
        model="isolated:existing-profile",
        settings=HarnessSettings(output_conversion="s2tw", **options),
        model_transport=httpx.MockTransport(respond),
        seed=29,
    )


@pytest.mark.asyncio
async def test_real_client_runner_and_notion_plugin_write_only_to_fixture():
    first_reply = response(calls=[tool()])
    first_reply["choices"][0]["message"]["reasoning_content"] = "synthetic tool reasoning"
    result = await evaluate(case(), [first_reply, response("已建立任务。")])
    assert result["automatic_checks_passed"]
    assert result["successful_writes"][0]["properties"]["Name"]["title"][0]["text"]["content"] == "繳水費"
    assert result["states"][0]["content"] == "已建立任務。"
    assert result["client_calls"][-1]["response"]["choices"][0]["message"]["content"] == "已建立任务。"
    history = result["client_calls"][1]["request"]["messages"]
    assert (
        next(message for message in history if message["role"] == "assistant")["reasoning_content"] == "synthetic tool reasoning"
    )
    first = result["client_calls"][0]["request"]
    assert first["seed"] == 29
    assert "Current datetime: 2026-10-25T20:00:00+08:00" in first["messages"][0]["content"]
    assert len(result["receipts"]) == 1
    assert result["driver_error"] is None
    assert result["rubric_review_required"]


@pytest.mark.asyncio
async def test_completed_status_cannot_replace_missing_write_evidence():
    result = await evaluate(case(), [response("任務已建立。")])
    assert result["states"][0]["status"] == "completed"
    assert not result["automatic_checks_passed"]
    assert not result["checks"]["writes"]
    assert not result["checks"]["required_tools"]
    assert result["rubric_review_required"]


@pytest.mark.asyncio
async def test_duplicate_successful_write_is_critical_even_with_completed_reply():
    result = await evaluate(case(), [response(calls=[tool(), tool(identifier="task-2")]), response("任務已建立。")])
    assert len(result["successful_writes"]) == 2
    assert result["critical_failures"] == ["unexpected_or_duplicate_successful_write"]
    assert not result["automatic_checks_passed"]


@pytest.mark.asyncio
async def test_definite_notion_failure_reaches_real_harness_and_remains_a_failed_write():
    item = case()
    item.notion.faults = [Fault(path="/pages", status=400)]
    item.expect.statuses = ["partial"]
    item.expect.writes = []
    result = await evaluate(item, [response(calls=[tool()]), response("任務建立失敗，尚未新增。")])
    assert result["automatic_checks_passed"]
    assert result["successful_writes"] == []
    assert result["states"][0]["failed_tools"] == 1
    assert json.loads(result["client_calls"][1]["request"]["messages"][-1]["content"])["success"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad", [response("截斷", finish="length"), response(calls=[{"id": "bad", "function": {"name": "x", "arguments": "{"}}])]
)
async def test_incomplete_or_malformed_model_response_cannot_complete_workflow(bad):
    item = case()
    item.expect.statuses = ["failed"]
    item.expect.writes = []
    item.expect.required_tools = []
    item.expect.reply_contains = []
    result = await evaluate(item, [bad])
    assert result["automatic_checks_passed"]
    assert result["states"][0]["status"] == "failed"
    assert result["client_calls"][0]["response"] == bad


@pytest.mark.asyncio
async def test_calendar_freeze_does_not_stop_run_deadline():
    import asyncio

    async def slow(request):
        await asyncio.sleep(10)
        return httpx.Response(200, json=response("too late"))

    item = case()
    item.expect.statuses = ["exhausted"]
    item.expect.writes = []
    item.expect.required_tools = []
    item.expect.reply_contains = []
    result = await evaluate_case(
        item,
        calendar=datetime.fromisoformat("2026-10-25T12:00:00+00:00"),
        base_url="http://127.0.0.1:18080/v1",
        model="fixture",
        settings=HarnessSettings(run_timeout_seconds=0.05),
        model_transport=httpx.MockTransport(slow),
    )
    assert result["automatic_checks_passed"]
    assert result["elapsed_seconds"] < 3
    assert result["client_calls"][0]["error"] == "CancelledError"


@pytest.mark.asyncio
async def test_query_fixture_applies_real_plugin_date_and_status_filters():
    item = case()
    item.notion.schemas["tasks"]["properties"]["Status"] = {"type": "status"}

    def page(name, date, status):
        return {
            "properties": {
                "Name": {"type": "title", "title": [{"plain_text": name}]},
                "Due": {"type": "date", "date": {"start": date}},
                "Status": {"type": "status", "status": {"name": status}},
            }
        }

    item.notion.pages["tasks"] = [
        page("due", "2026-11-01T15:00:00+08:00", "Doing"),
        page("finished", "2026-10-26", "Done"),
        page("later", "2026-11-03", "To Do"),
    ]
    item.expect.writes = []
    item.expect.required_tools = ["notion_list_tasks"]
    item.expect.reply_contains = ["due"]
    result = await evaluate(
        item,
        [
            response(calls=[tool("notion_list_tasks", {"due_from": "2026-10-26", "due_to": "2026-11-01"})]),
            response("due：進行中。"),
        ],
    )
    output = json.loads(result["client_calls"][1]["request"]["messages"][-1]["content"])
    assert output["data"]["count"] == 1
    assert output["data"]["tasks"][0]["Name"] == "due"
    assert result["automatic_checks_passed"]


@pytest.mark.asyncio
@pytest.mark.parametrize("first", [response("請問要提醒什麼？"), response(calls=[tool(arguments={})])])
async def test_clarification_accepts_natural_reply_and_harness_input_without_approving(first):
    item = case(
        steps=[{"content": "先問我要提醒什麼，收到回答後再建立。"}, {"action": "answer", "content": "明天提醒我繳水費。"}],
        expect={**case().expect.model_dump(), "statuses": [["completed", "awaiting_input"], "completed"]},
    )
    item.expect.writes_after_steps = [[], item.expect.writes]
    result = await evaluate(item, [first, response(calls=[tool()]), response("已建立任務。")])
    assert result["automatic_checks_passed"]
    assert result["writes_by_step"][0] == []
    assert len(result["writes_by_step"][1]) == 1
    assert result["client_calls"][1]["request"]["messages"][-1]["content"] == "明天提醒我繳水費。"


@pytest.mark.asyncio
async def test_correct_final_record_does_not_hide_a_write_before_clarification():
    item = case(
        steps=[{"content": "先問我要提醒什麼，收到回答後再建立。"}, {"action": "answer", "content": "明天提醒我繳水費。"}],
        expect={**case().expect.model_dump(), "statuses": ["completed", "completed"]},
    )
    item.expect.writes_after_steps = [[], item.expect.writes]
    result = await evaluate(item, [response(calls=[tool()]), response("任務已建立。"), response("任務保持原樣。")])
    assert result["checks"]["writes"]
    assert not result["checks"]["write_checkpoints"]
    assert result["critical_failures"]
    assert not result["automatic_checks_passed"]


@pytest.mark.asyncio
async def test_fixture_errors_cannot_pass_as_expected_tool_failure():
    item = case()
    item.notion.schemas["tasks"]["properties"]["Status"] = {"type": "status"}
    item.notion.pages["tasks"] = [{"properties": {"Status": {"type": "status", "status": 42}}}]
    item.expect.statuses = ["partial"]
    item.expect.writes = []
    item.expect.required_tools = ["notion_list_tasks"]
    result = await evaluate(
        item, [response(calls=[tool("notion_list_tasks", {"state": "unfinished"})]), response("任務查詢失敗。")]
    )
    assert result["checks"]["statuses"]
    assert result["fixture_errors"]
    assert not result["automatic_checks_passed"]


@pytest.mark.asyncio
async def test_external_model_endpoint_is_rejected_before_any_model_request():
    with pytest.raises(ValueError, match="loopback"):
        await evaluate_case(
            case(),
            calendar=datetime.now().astimezone(),
            base_url="https://remote.invalid/v1",
            model="fixture",
            settings=HarnessSettings(),
        )


def test_module_cli_uses_explicit_suite_and_preserves_existing_output(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response("測試回覆。")).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    item = case()
    item.expect.writes = []
    item.expect.required_tools = []
    item.expect.reply_contains = ["測試回覆"]
    suite = tmp_path / "cases.json"
    suite.write_text(json.dumps({"calendar": "2026-10-25T20:00:00+08:00", "cases": [item.model_dump(mode="json")]}))
    output = tmp_path / "result.json"
    command = [
        sys.executable,
        "-m",
        "babu.evaluation",
        "--cases",
        str(suite),
        "--output",
        str(output),
        "--base-url",
        f"http://127.0.0.1:{server.server_port}/v1",
        "--model",
        "fixture",
        "--seed",
        "11",
    ]
    env = {"PATH": os.defpath, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    try:
        completed = subprocess.run(command, capture_output=True, text=True, env=env, timeout=10)
        assert completed.returncode == 0, completed.stderr
        report = json.loads(output.read_text())
        assert report["results"][0]["automatic_checks_passed"]
        assert len(report["suite_sha256"]) == 64
        previous = output.read_bytes()
        assert subprocess.run(command, capture_output=True, env=env, timeout=10).returncode != 0
        assert output.read_bytes() == previous
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
