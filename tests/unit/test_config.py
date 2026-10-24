from pathlib import Path

import pytest
from pydantic import ValidationError

from babu.config import ApplicationConfig, default_config_path, state_directory
from babu.plugins.loader import PluginConfiguration
from babu.plugins.notion.config import NotionSecrets


def test_xdg_paths_and_explicit_config_override(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    path = tmp_path / "config/babu/config.toml"
    path.parent.mkdir(parents=True)
    path.write_text("[harness]\nmax_tool_calls = 3\n")
    assert default_config_path() == path
    assert state_directory() == tmp_path / "state/babu"
    assert ApplicationConfig.from_toml().harness.max_tool_calls == 3
    alternate = tmp_path / "alternate.toml"
    alternate.write_text("[harness]\nmax_tool_calls = 7\n")
    assert ApplicationConfig.from_toml(alternate).harness.max_tool_calls == 7
    monkeypatch.setenv("XDG_CONFIG_HOME", "relative")
    assert default_config_path() == Path.home() / ".config/babu/config.toml"


def test_plugins_are_optional_and_unknown_configuration_is_rejected(monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    PluginConfiguration(ApplicationConfig()).check_secrets(None)
    PluginConfiguration(ApplicationConfig(plugins={"discord": {"enabled": False}})).check_secrets(None)
    with pytest.raises(ValueError, match="Unknown plugins"):
        PluginConfiguration(ApplicationConfig(plugins={"shell": {"enabled": True}}))
    with pytest.raises(ValidationError):
        ApplicationConfig.model_validate({"app": {"timezone": "UTC"}})
    with pytest.raises(ValidationError):
        PluginConfiguration(
            ApplicationConfig(plugins={"discord": {"enabled": True, "guild_id": 1, "channel_id": 2, "owner_user_ids": []}})
        )


def test_plugin_credentials_are_required_only_when_enabled_and_never_rendered(tmp_path, monkeypatch):
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    config = ApplicationConfig(
        plugins={
            "notion": {
                "enabled": True,
                "default": {
                    "tasks": {"data_source_id": "tasks"},
                    "finance": {"data_source_id": "finance"},
                },
            }
        }
    )
    plugins = PluginConfiguration(config)
    with pytest.raises(ValidationError):
        plugins.check_secrets(None)
    env = tmp_path / "agent.env"
    env.write_text("NOTION_TOKEN=synthetic-private\n")
    plugins.check_secrets(env)
    assert "synthetic-private" not in repr(NotionSecrets.load(env))
