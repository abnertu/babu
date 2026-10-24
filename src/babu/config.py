import os
import tomllib
from pathlib import Path
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


def xdg_directory(variable: str, fallback: str) -> Path:
    value = os.environ.get(variable, "")
    return Path(value) if value and Path(value).is_absolute() else Path.home() / fallback


def config_directory() -> Path:
    return xdg_directory("XDG_CONFIG_HOME", ".config") / "babu"


def state_directory() -> Path:
    return xdg_directory("XDG_STATE_HOME", ".local/state") / "babu"


def default_config_path() -> Path:
    return config_directory() / "config.toml"


class HarnessSettings(StrictModel):
    timezone: str = "Asia/Taipei"
    state_dir: Path = Field(default_factory=state_directory)
    instructions_path: Path | None = None
    instructions_max_bytes: Annotated[int, Field(gt=0, le=1048576)] = 32768
    max_model_calls: Annotated[int, Field(gt=0, le=100)] = 6
    max_tool_calls: Annotated[int, Field(gt=0, le=100)] = 10
    max_input_chars: Annotated[int, Field(gt=0, le=1000000)] = 16000
    max_context_chars: Annotated[int, Field(gt=0, le=1000000)] = 64000
    max_tool_result_bytes: Annotated[int, Field(gt=256, le=1000000)] = 32768
    run_timeout_seconds: Annotated[float, Field(gt=0, le=3600)] = 180
    tool_timeout_seconds: Annotated[float, Field(gt=0, le=600)] = 30
    history_messages: Annotated[int, Field(ge=0, le=100)] = 20
    session_ttl_seconds: Annotated[int, Field(gt=0)] = 7200
    approval_ttl_seconds: Annotated[int, Field(gt=0)] = 600

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator("state_dir", "instructions_path")
    @classmethod
    def expand_path(cls, value: Path | None) -> Path | None:
        return value.expanduser().resolve() if value is not None else None


class LLMSettings(StrictModel):
    base_url: HttpUrl = HttpUrl("http://127.0.0.1:8080/v1")
    model: str = "Qwen3.6-35B-A3B"
    temperature: Annotated[float, Field(ge=0, le=2)] = 0.2
    max_tokens: Annotated[int, Field(gt=0)] = 4096
    timeout_seconds: Annotated[float, Field(gt=0)] = 60


class ApplicationConfig(StrictModel):
    harness: HarnessSettings = Field(default_factory=HarnessSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    plugins: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @classmethod
    def from_toml(cls, path: Path | None = None) -> "ApplicationConfig":
        with (path or default_config_path()).expanduser().open("rb") as file:
            return cls.model_validate(tomllib.load(file))
