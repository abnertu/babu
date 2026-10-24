from pathlib import Path

from babu.config import ApplicationConfig
from babu.plugins.discord.config import DiscordSecrets, DiscordSettings
from babu.plugins.notion.config import NotionSecrets, NotionSettings


class PluginConfiguration:
    """Only built-in, explicitly named plugins may be loaded by the host."""

    def __init__(self, config: ApplicationConfig) -> None:
        unknown = config.plugins.keys() - {"discord", "notion"}
        if unknown:
            raise ValueError(f"Unknown plugins: {', '.join(sorted(unknown))}")
        self.discord: DiscordSettings | None = None
        self.notion: NotionSettings | None = None
        for name, raw in config.plugins.items():
            # Disabled plugins need no settings or credentials. A misspelled enable flag is still rejected.
            if raw.get("enabled") is False or raw == {}:
                if set(raw) - {"enabled"}:
                    model = DiscordSettings if name == "discord" else NotionSettings
                    model.model_validate(raw)
                continue
            if name == "discord":
                self.discord = DiscordSettings.model_validate(raw)
            else:
                self.notion = NotionSettings.model_validate(raw)

    def check_secrets(self, env_file: Path | None) -> None:
        if self.discord and self.discord.enabled:
            DiscordSecrets.load(env_file)
        if self.notion and self.notion.enabled:
            NotionSecrets.load(env_file)
