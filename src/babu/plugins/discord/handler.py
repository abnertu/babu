from typing import Any

from babu.harness.models import RunState, RunStatus


class MessageAuthorizer:
    def __init__(self, guild_id: int, channel_id: int, owner_user_ids: list[int]) -> None:
        self.guild_id, self.channel_id, self.owner_user_ids = guild_id, channel_id, set(owner_user_ids)

    def allows(self, message: Any) -> bool:
        return (
            not message.author.bot
            and message.guild is not None
            and message.guild.id == self.guild_id
            and message.channel.id == self.channel_id
            and message.author.id in self.owner_user_ids
        )


def render_run(state: RunState) -> str:
    content = state.content or "Completed."
    if state.status == RunStatus.COMPLETED:
        return content
    prefix = {
        RunStatus.AWAITING_APPROVAL: f"Approval required. Use `!babu approve {state.id}` or `!babu cancel {state.id}`.",
        RunStatus.AWAITING_INPUT: f"Input required. Use `!babu answer {state.id} <details>`.",
        RunStatus.OUTCOME_UNKNOWN: "External outcome is unknown. Inspect the operation before retrying.",
        RunStatus.PARTIAL: "Partially completed; inspect the tool results.",
        RunStatus.CANCELLED: "Cancelled.",
        RunStatus.EXHAUSTED: "Execution limit reached.",
    }.get(state.status, "Failed.")
    return f"{prefix}\nRun: {state.id}\n\n{content}"
