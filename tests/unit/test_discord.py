import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from babu.harness.models import RunEvent, RunRequest, RunState, RunStatus
from babu.plugins.discord import bot
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


def discord_plugin(harness):
    return DiscordPlugin(DiscordSettings(guild_id=3, channel_id=2, owner_user_ids=[1]), harness)


async def deliver(plugin, msg):
    # Bound the handler so that a blocking regression fails instead of hanging.
    await asyncio.wait_for(plugin.on_message(msg), 5)


def model_run(*events, after=None):
    async def start(request, sink):
        for kind in events:
            await sink(RunEvent(sequence=1, run_id="run", kind=kind))
        if after:
            await after()
        return RunState(id="run", request=request, status=RunStatus.COMPLETED, content="Created")

    return start


@pytest.mark.asyncio
async def test_transport_passes_identity_and_renders_model_progress():
    harness = AsyncMock()
    msg = message()
    status = SimpleNamespace(edit=AsyncMock())
    msg.channel.send = AsyncMock(return_value=status)
    start = model_run("ModelStarted", "ModelStarted")

    async def checked_start(request, sink):
        assert request.principal_id == "discord:1"
        assert request.session_id == "3:2"
        return await start(request, sink)

    harness.start.side_effect = checked_start
    plugin = discord_plugin(harness)
    await deliver(plugin, msg)
    msg.channel.send.assert_awaited_once()
    assert msg.channel.send.call_args.kwargs["file"].filename == "clawd-thinking.gif"
    status.edit.assert_awaited_once_with(content="Created", attachments=[])
    await plugin.close()


@pytest.mark.asyncio
async def test_model_run_does_not_wait_for_thinking_upload(monkeypatch):
    monkeypatch.setattr(bot, "THINKING_UPLOAD_WAIT_SECONDS", 0.01)
    harness = AsyncMock()
    msg = message()
    released = asyncio.Event()
    sink_returned = False

    status = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())

    async def send(content=None, file=None):
        if file is not None:
            await released.wait()
        return status

    msg.channel.send = AsyncMock(side_effect=send)

    async def start(request, sink):
        nonlocal sink_returned
        await asyncio.wait_for(sink(RunEvent(sequence=1, run_id="run", kind="ModelStarted")), 1)
        sink_returned = True
        return RunState(id="run", request=request, status=RunStatus.COMPLETED, content="Created")

    harness.start.side_effect = start
    plugin = discord_plugin(harness)
    try:
        await deliver(plugin, msg)
    finally:
        released.set()
    assert sink_returned
    msg.channel.send.assert_any_await("Created")
    await plugin.close()


class Stall:
    """A Discord request that outlives the wait, like one in a rate-limit sleep."""

    def __init__(self, result=None):
        self.result, self.started, self.released, self.cancelled = result, asyncio.Event(), asyncio.Event(), False

    async def __call__(self, *args, **kwargs):
        self.started.set()
        try:
            await self.released.wait()
        except asyncio.CancelledError:
            # Cancelling discord.py in a rate-limit sleep leaves its global gate closed.
            self.cancelled = True
            raise
        return self.result


async def fail(*args, **kwargs):
    raise RuntimeError("discord unavailable")


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", ["upload", "edit"])
async def test_failed_thinking_request_still_delivers_reply(monkeypatch, failing):
    monkeypatch.setattr(bot, "THINKING_UPLOAD_WAIT_SECONDS", 0.01)
    harness = AsyncMock()
    msg = message()
    status = SimpleNamespace(edit=AsyncMock(side_effect=fail if failing == "edit" else None), delete=AsyncMock())

    async def send(content=None, file=None):
        if file is not None and failing == "upload":
            await fail()
        return status

    msg.channel.send = AsyncMock(side_effect=send)
    harness.start.side_effect = model_run("ModelStarted")
    plugin = discord_plugin(harness)
    await deliver(plugin, msg)
    msg.channel.send.assert_any_await("Created")
    await plugin.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stalled", ["upload", "edit"])
async def test_stalled_thinking_request_is_abandoned_not_cancelled(monkeypatch, stalled):
    monkeypatch.setattr(bot, "THINKING_UPLOAD_WAIT_SECONDS", 0.01)
    harness = AsyncMock()
    msg = message()
    status = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
    stall = Stall(result=status)
    if stalled == "edit":
        status.edit = stall

    async def send(content=None, file=None):
        if file is not None and stalled == "upload":
            return await stall()
        return status

    msg.channel.send = AsyncMock(side_effect=send)
    harness.start.side_effect = model_run("ModelStarted")
    plugin = discord_plugin(harness)
    await deliver(plugin, msg)
    msg.channel.send.assert_any_await("Created")
    status.delete.assert_not_awaited()
    stall.released.set()
    await asyncio.wait_for(until(lambda: status.delete.await_count), 1)
    assert not stall.cancelled
    await plugin.close()


async def until(condition):
    while not condition():
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_cancelled_request_deletes_thinking_message_without_cancelling_upload():
    harness = AsyncMock()
    msg = message()
    status = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
    stall = Stall(result=status)

    async def send(content=None, file=None):
        return await stall() if file is not None else status

    msg.channel.send = AsyncMock(side_effect=send)

    async def cancel():
        await asyncio.wait_for(stall.started.wait(), 1)
        raise asyncio.CancelledError

    harness.start.side_effect = model_run("ModelStarted", after=cancel)
    plugin = discord_plugin(harness)
    with pytest.raises(asyncio.CancelledError):
        await deliver(plugin, msg)
    stall.released.set()
    await asyncio.wait_for(until(lambda: status.delete.await_count), 1)
    assert not stall.cancelled
    await plugin.close()


@pytest.mark.asyncio
async def test_cancelled_reply_deletes_upload_that_just_finished():
    harness = AsyncMock()
    msg = message()
    status = SimpleNamespace(edit=AsyncMock(), delete=AsyncMock())
    stall = Stall(result=status)

    async def send(content=None, file=None):
        return await stall() if file is not None else status

    msg.channel.send = AsyncMock(side_effect=send)
    harness.start.side_effect = model_run("ModelStarted")
    plugin = discord_plugin(harness)
    handler = asyncio.create_task(plugin.on_message(msg))
    await asyncio.wait_for(stall.started.wait(), 1)
    for _ in range(5):
        await asyncio.sleep(0)
    # The upload finishes in the same loop pass that cancels the waiting reply.
    stall.released.set()
    handler.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(handler, 5)
    await asyncio.wait_for(until(lambda: status.delete.await_count), 1)
    status.edit.assert_not_awaited()
    await plugin.close()


@pytest.mark.asyncio
async def test_cancelled_request_reports_finished_upload_failure(caplog):
    harness = AsyncMock()
    msg = message()
    msg.channel.send = AsyncMock(side_effect=fail)

    async def cancel():
        await asyncio.sleep(0.01)
        raise asyncio.CancelledError

    harness.start.side_effect = model_run("ModelStarted", after=cancel)
    plugin = discord_plugin(harness)
    with pytest.raises(asyncio.CancelledError):
        await deliver(plugin, msg)
    await asyncio.wait_for(until(lambda: "discord_thinking_failed" in caplog.text), 1)
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
