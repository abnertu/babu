from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from babu.harness.models import RunRequest, RunState, RunStatus
from babu.plugins.discord.bot import DiscordPlugin
from babu.plugins.discord.config import DiscordSettings
from babu.plugins.discord.handler import MessageAuthorizer


def message(user=1, channel=2, guild=3, bot=False, content="hello"):
    @asynccontextmanager
    async def typing():
        yield

    return SimpleNamespace(
        id=100,
        author=SimpleNamespace(id=user, bot=bot),
        channel=SimpleNamespace(id=channel, typing=typing),
        guild=SimpleNamespace(id=guild) if guild else None,
        content=content,
    )


def test_authorizer_checks_all_discord_dimensions():
    auth = MessageAuthorizer(3, 2, [1, 4])
    assert auth.allows(message())
    assert auth.allows(message(user=4))
    assert not auth.allows(message(user=9))
    assert not auth.allows(message(channel=9))
    assert not auth.allows(message(guild=9))
    assert not auth.allows(message(guild=None))
    assert not auth.allows(message(bot=True))


@pytest.mark.asyncio
async def test_unauthorized_message_never_enters_harness():
    harness = AsyncMock()
    plugin = DiscordPlugin(DiscordSettings(guild_id=3, channel_id=2, owner_user_ids=[1]), harness)
    await plugin.on_message(message(user=9))
    harness.start.assert_not_called()
    harness.store.get.assert_not_called()
    await plugin.close()


@pytest.mark.asyncio
async def test_transport_passes_identity_and_renders_tool_progress():
    from babu.harness.models import RunEvent

    harness = AsyncMock()
    msg = message()
    status = SimpleNamespace(edit=AsyncMock())
    msg.channel.send = AsyncMock(return_value=status)

    async def start(request, sink):
        assert request.principal_id == "discord:1"
        assert request.session_id == "3:2"
        await sink(RunEvent(sequence=1, run_id="run", kind="ToolStarted"))
        return RunState(id="run", request=request, status=RunStatus.COMPLETED, content="Created")

    harness.start.side_effect = start
    plugin = DiscordPlugin(DiscordSettings(guild_id=3, channel_id=2, owner_user_ids=[1]), harness)
    await plugin.on_message(msg)
    assert msg.channel.send.call_args.kwargs["file"].filename == "clawd-thinking.gif"
    status.edit.assert_awaited_once_with(content="Created", attachments=[])
    await plugin.close()


@pytest.mark.asyncio
async def test_approval_uses_authenticated_principal():
    harness = AsyncMock()
    request = RunRequest(principal_id="discord:1", session_id="s", source="fixture", event_id="e", content="x")
    harness.resume.return_value = RunState(id="run", request=request, status=RunStatus.COMPLETED, content="Done")
    plugin = DiscordPlugin(DiscordSettings(guild_id=3, channel_id=2, owner_user_ids=[1]), harness)
    msg = message(content="!babu approve run")
    msg.channel.send = AsyncMock()
    await plugin.on_message(msg)
    assert harness.resume.call_args.args == ("run", "discord:1")
    assert harness.resume.call_args.kwargs["approved"] is True
    await plugin.close()
