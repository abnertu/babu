from pathlib import Path
from typing import Annotated, Any, cast

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from babu.config import StrictModel


class DiscordSettings(StrictModel):
    enabled: bool = False
    guild_id: Annotated[int, Field(gt=0)]
    channel_id: Annotated[int, Field(gt=0)]
    owner_user_ids: list[Annotated[int, Field(gt=0)]]

    @model_validator(mode="after")
    def authorized_users(self) -> "DiscordSettings":
        if not self.owner_user_ids:
            raise ValueError("At least one authorized Discord user is required")
        self.owner_user_ids = list(dict.fromkeys(self.owner_user_ids))
        return self


class DiscordSecrets(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)
    token: SecretStr = Field(validation_alias="DISCORD_TOKEN", min_length=1)

    @classmethod
    def load(cls, env_file: Path | None) -> "DiscordSecrets":
        return cast(DiscordSecrets, cast(Any, cls)(_env_file=env_file))
