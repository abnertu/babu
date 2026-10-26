import asyncio
import logging
from pathlib import Path
from typing import Any

import discord
from discord.ext import commands

from babu.harness.models import RunEvent, RunRequest, RunState
from babu.harness.runner import RunRunner
from babu.harness.store import SessionBusyError

from .config import DiscordSettings
from .handler import MessageAuthorizer, render_run

logger = logging.getLogger(__name__)
THINKING_GIF_PATH = Path(__file__).resolve().parents[2] / "assets/clawd-thinking.gif"
THINKING_UPLOAD_WAIT_SECONDS = 10.0


class DiscordPlugin(commands.Bot):
    def __init__(self, settings: DiscordSettings, harness: RunRunner) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!babu ", intents=intents)
        self.authorizer = MessageAuthorizer(settings.guild_id, settings.channel_id, settings.owner_user_ids)
        self.harness = harness
        self._cleanups: set[asyncio.Task[Any]] = set()

    async def on_message(self, message: Any) -> None:
        if not self.authorizer.allows(message) or not message.content.strip():
            return
        principal = f"discord:{message.author.id}"
        session_id = f"{message.guild.id}:{message.channel.id}"
        upload: asyncio.Task[Any] | None = None

        async def post_thinking() -> Any:
            return await message.channel.send(file=discord.File(THINKING_GIF_PATH, filename="clawd-thinking.gif"))

        async def show_event(event: RunEvent) -> None:
            nonlocal upload
            # Upload in the background: the model call must not wait for Discord.
            if event.kind == "ModelStarted" and upload is None:
                upload = asyncio.create_task(post_thinking())

        try:
            try:
                async with message.channel.typing():
                    text = message.content.strip()
                    if text.startswith("!babu "):
                        state = await self._command(text[6:], principal, show_event)
                    else:
                        state = await self.harness.start(
                            RunRequest(
                                principal_id=principal,
                                session_id=session_id,
                                source="discord",
                                event_id=str(message.id),
                                content=text,
                            ),
                            show_event,
                        )
                text = render_run(state)
            except (ValueError, RuntimeError, SessionBusyError) as exc:
                text = str(exc)
            except Exception:
                logger.warning("discord_request_failed")
                text = "Request failed. Check the run trace."
            chunks = [text[index : index + 1900] for index in range(0, len(text), 1900)]
            if upload is not None:
                # _replace_thinking owns the upload from here; finally must not abandon it.
                pending, upload = upload, None
                if await self._replace_thinking(pending, chunks[0]):
                    chunks = chunks[1:]
            for chunk in chunks:
                await message.channel.send(chunk)
        finally:
            if upload is not None:
                self._abandon(upload)

    async def _replace_thinking(self, upload: asyncio.Task[Any], content: str) -> bool:
        if not await self._settle(upload):
            return False
        status_message = upload.result()
        edit = asyncio.ensure_future(status_message.edit(content=content, attachments=[]))
        return await self._settle(edit, status_message)

    async def _settle(self, request: asyncio.Future[Any], target: Any = None) -> bool:
        # Never cancel a Discord request: discord.py keeps its global rate-limit
        # gate closed when a request is cancelled during the rate-limit sleep.
        try:
            await asyncio.wait({request}, timeout=THINKING_UPLOAD_WAIT_SECONDS)
        except asyncio.CancelledError:
            self._abandon(request, target)
            raise
        if not request.done():
            self._abandon(request, target)
        if not request.done() or request.cancelled() or request.exception() is not None:
            logger.warning("discord_thinking_failed")
            return False
        return True

    def _abandon(self, request: asyncio.Future[Any], target: Any = None) -> None:
        """Delete the thinking message once an abandoned request finishes."""

        def cleanup(done: asyncio.Future[Any]) -> None:
            if done.cancelled():
                return
            if done.exception() is not None:
                logger.warning("discord_thinking_failed")
                if target is None:
                    return
            delete = asyncio.create_task((target or done.result()).delete())
            self._cleanups.add(delete)
            delete.add_done_callback(finish)

        def finish(delete: asyncio.Task[Any]) -> None:
            self._cleanups.discard(delete)
            if not delete.cancelled() and delete.exception() is not None:
                logger.warning("discord_thinking_cleanup_failed")

        request.add_done_callback(cleanup)

    async def _command(self, text: str, principal: str, sink: Any) -> RunState:
        parts = text.split(maxsplit=2)
        if len(parts) < 2:
            raise ValueError("Use !babu show/resume/approve/cancel <run-id>, or !babu answer <run-id> <details>.")
        action, run_id = parts[:2]
        if action == "show":
            return await self.harness.store.get(run_id, principal)
        if action == "cancel":
            return await self.harness.cancel(run_id, principal)
        if action == "approve":
            return await self.harness.resume(run_id, principal, approved=True, sink=sink)
        if action == "answer" and len(parts) == 3:
            return await self.harness.resume(run_id, principal, content=parts[2], sink=sink)
        if action == "resume":
            return await self.harness.resume(run_id, principal, sink=sink)
        raise ValueError("Unknown Babu command")
