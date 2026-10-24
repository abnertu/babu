from pathlib import Path
from typing import Annotated, Any, cast

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from babu.config import StrictModel


class NotionDatabaseSettings(StrictModel):
    data_source_id: Annotated[str, Field(min_length=1)]
    properties: dict[str, str] = Field(default_factory=dict)


class NotionUserDatabases(StrictModel):
    tasks: NotionDatabaseSettings
    finance: NotionDatabaseSettings
    projects: NotionDatabaseSettings | None = None
    categories: NotionDatabaseSettings | None = None
    accounts: NotionDatabaseSettings | None = None


class NotionSettings(StrictModel):
    enabled: bool = False
    default: NotionUserDatabases | None = None
    users: dict[str, NotionUserDatabases] = Field(default_factory=dict)

    @model_validator(mode="after")
    def require_databases(self) -> "NotionSettings":
        if self.enabled and self.default is None and not self.users:
            raise ValueError("Enabled Notion plugin requires default databases or per-principal databases")
        return self

    def get_for_user(self, principal_id: str) -> NotionUserDatabases:
        result = self.users.get(principal_id, self.default)
        if result is None:
            raise ValueError("No Notion databases configured for this principal")
        return result


class NotionSecrets(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", hide_input_in_errors=True)
    token: SecretStr = Field(validation_alias="NOTION_TOKEN", min_length=1)

    @classmethod
    def load(cls, env_file: Path | None) -> "NotionSecrets":
        return cast(NotionSecrets, cast(Any, cls)(_env_file=env_file))
