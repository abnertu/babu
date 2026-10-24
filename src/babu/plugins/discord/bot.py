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


class DiscordPlugin(commands.Bot):
    def __init__(self, settings: DiscordSettings, harness: RunRunner) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        super().__init__(command_prefix="!babu ", intents=intents)
        self.authorizer = MessageAuthorizer(settings.guild_id, settings.channel_id, settings.owner_user_ids)
        self.harness = harness

    async def on_message(self, message: Any) -> None:
        if not self.authorizer.allows(message) or not message.content.strip():
            return
        principal = f"discord:{message.author.id}"
        session_id = f"{message.guild.id}:{message.channel.id}"
        status_message = None

        async def show_event(event: RunEvent) -> None:
            nonlocal status_message
            if event.kind == "ToolStarted" and status_message is None:
                status_message = await message.channel.send(file=discord.File(THINKING_GIF_PATH, filename="clawd-thinking.gif"))

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
        if status_message:
            await status_message.edit(content=chunks[0], attachments=[])
            chunks = chunks[1:]
        for chunk in chunks:
            await message.channel.send(chunk)

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
