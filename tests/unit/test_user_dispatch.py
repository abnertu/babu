from unittest.mock import AsyncMock

import pytest

from babu.harness.models import ExecutionContext
from babu.plugins.notion.config import NotionSettings
from babu.plugins.notion.plugin import NotionPlugin
from babu.plugins.notion.schemas.task import CreateTaskArgs


@pytest.mark.asyncio
async def test_notion_routes_using_explicit_principal_context():
    settings = NotionSettings.model_validate(
        {
            "enabled": True,
            "default": {
                "tasks": {"data_source_id": "default-tasks"},
                "finance": {"data_source_id": "default-finance"},
            },
            "users": {
                "discord:100": {
                    "tasks": {"data_source_id": "private-tasks"},
                    "finance": {"data_source_id": "private-finance"},
                }
            },
        }
    )
    client = AsyncMock()
    client.retrieve_data_source.return_value = {"properties": {"Name": {"type": "title"}}}
    client.create_page.return_value = {"id": "task-id"}
    plugin = NotionPlugin(settings, client)
    assert (await plugin.create_task(CreateTaskArgs(title="Private"), ExecutionContext("discord:100", "s", "r", "op1"))).success
    assert client.create_page.call_args.args[0] == "private-tasks"
    assert (await plugin.create_task(CreateTaskArgs(title="Default"), ExecutionContext("local:1", "s", "r", "op2"))).success
    assert client.create_page.call_args.args[0] == "default-tasks"
    assert plugin.services(ExecutionContext("discord:100", "s", "r", "op3"))[0].data_source_id == "private-tasks"
