# Babu

A single-host agent harness with durable runs, a local CLI, and optional Discord and Notion plugins.

[![Babu application validation](https://github.com/abnertu/babu/actions/workflows/check.yml/badge.svg?branch=main)](https://github.com/abnertu/babu/actions/workflows/check.yml)
[![Python](https://img.shields.io/badge/dynamic/toml?url=https%3A%2F%2Fraw.githubusercontent.com%2Fabnertu%2Fbabu%2Fmain%2Fpyproject.toml&query=%24.project%5B%22requires-python%22%5D&label=Python&logo=python&logoColor=white&color=3776AB)](https://www.python.org/)

## Install

Install Python 3.13 and [uv](https://docs.astral.sh/uv/).
Run an OpenAI-compatible llama-swap endpoint with a tool-capable model.

```bash
git clone https://github.com/abnertu/babu.git babu
cd babu
uv sync --locked
mkdir -p "${XDG_CONFIG_HOME:-$HOME/.config}/babu"
cp config.example.toml "${XDG_CONFIG_HOME:-$HOME/.config}/babu/config.toml"
```

Set `[llm].base_url` and `model` in the configuration. Both service plugins are disabled in the example, so local chat needs no Discord or Notion credentials.

## Usage

```bash
uv run babu check-config
uv run babu check-runtime
uv run babu chat 'Hello' --session personal
uv run babu runs
uv run babu show RUN_ID --events
uv run babu show RUN_ID --receipts
```

`check-config` validates configuration and plugin schemas; `check-runtime` also checks credentials for enabled plugins. Neither contacts external services. `chat` makes model calls and may execute enabled tools.

Runs persist their model/tool messages, completed tool receipts, execution counters, and events. Repeated delivery of the same source event reuses its run. Use these commands to resume work, supply missing input, approve operations, or cancel runs:

```bash
uv run babu resume RUN_ID
uv run babu resume RUN_ID --input 'Use the cash account'
uv run babu resume RUN_ID --approve
uv run babu cancel RUN_ID
```

Approval applies only to the saved operation and its original arguments. A run with an uncertain external write stops as `outcome_unknown`; inspect the external system before starting a replacement run. Completed writes remain completed when a run is cancelled or exhausts its budget.

## Configuration and state

| Content | Default location |
| --- | --- |
| Configuration | `$XDG_CONFIG_HOME/babu/config.toml`, or `~/.config/babu/config.toml` |
| Optional environment file | The same configuration directory, `agent.env` |
| SQLite state | `$XDG_STATE_HOME/babu/babu.db`, or `~/.local/state/babu/babu.db` |

No root access is needed when Babu runs as your user. Every command accepts `--config PATH` and `--env-file PATH`; `harness.state_dir` overrides the state directory. Explicit paths support `~`. Relative XDG environment paths are ignored. Babu does not load a working-directory `.env` or legacy configuration automatically.

The `[harness]` section controls call limits, execution time, tool timeouts, and input/context/output sizes. It also controls history and approval expiry. State inspection and cancellation do not need model connectivity or integration credentials.

Sessions are isolated by authenticated principal and session ID. History includes recent completed turns, defaults to 20 messages within two hours, and is excluded once outside that window. This is context expiry, not deletion of stored run records. Bundled [response preferences](src/babu/harness/default_preferences.md) require Taiwanese Traditional Chinese; `harness.instructions_path` replaces them with an external UTF-8 file at startup. `harness.output_conversion` converts reply sentences that contain Simplified-only characters to Traditional Chinese and leaves other sentences unchanged.

## Plugins

Plugins are trusted Python code bundled with Babu and explicitly enabled in configuration. The model can invoke only registered tools with Pydantic-validated arguments. The host controls policy, approval, budgets, and execution receipts.
Tool definitions expose nested arguments and finite choices to simple model templates.
Omit optional empty arguments; Pydantic validation remains authoritative.

For Discord, enable `[plugins.discord]`. Set the guild/channel IDs and allowed `owner_user_ids`. Provide `DISCORD_TOKEN`.
Enable Message Content Intent for the bot. Grant channel permissions to read messages, send replies, and attach files. Start it with:

```bash
uv run babu run
```

Ordinary messages start runs. While the model runs, Babu displays an animated thinking message. Use `!babu show RUN_ID`, `!babu resume RUN_ID`, `!babu approve RUN_ID`, `!babu answer RUN_ID details`, or `!babu cancel RUN_ID` in the authorized channel to control your own runs.

For Notion, enable `[plugins.notion]`. Provide `NOTION_TOKEN`. Set both `[plugins.notion.default.tasks]` and `[plugins.notion.default.finance]` data source IDs. Give the integration access to those databases and their relations. Its tools create tasks with optional Markdown bodies and record income/expenses. They also list tasks, query transactions, search titles, and inspect schemas/options. Property detection supports explicit `properties` overrides.

Optional `[plugins.notion.users."discord:USER_ID"]` or `[plugins.notion.users."local:UNIX_UID"]` mappings provide both task and finance data sources for that principal. Other principals use the configured defaults. Notion routing does not authorize Discord users.

Store tokens in the process environment or the optional `agent.env`. Set `agent.env` file permissions to `600`. Keep credentials outside version control and the Nix store. Disabled plugins require no credentials. See [config.example.toml](config.example.toml) for the complete example and [harness execution](docs/harness.md) for recovery and plugin contracts.

## Synthetic model evaluation

Run isolated workflows against an explicit loopback model endpoint with `python -m babu.evaluation`.
The evaluator uses the real harness and Notion plugin with mock HTTP, temporary SQLite, and bundled preferences.
It never loads application configuration or integration credentials.
See [evaluation usage](docs/evaluation.md) for fixtures, reports, and review requirements.

## Nix

The flake packages Babu and its locked dependencies through uv2nix for `x86_64-linux`:

```bash
nix build --no-link --show-trace --print-build-logs .#babu
```

The host owns deployment and mutable files. A user service can use the XDG paths above; Home Manager can generate non-secret configuration. Load secrets at runtime. Keep SQLite in writable state storage. Babu 0.2 uses a new configuration and state schema; it does not migrate earlier deployments.
