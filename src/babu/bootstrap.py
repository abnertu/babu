from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from babu.config import ApplicationConfig
from babu.harness.runner import RunRunner
from babu.harness.store import RunStore
from babu.llm.client import LLMClient
from babu.plugins.loader import PluginConfiguration
from babu.plugins.notion.client import NotionClient
from babu.plugins.notion.config import NotionSecrets
from babu.plugins.notion.plugin import NotionPlugin
from babu.storage.db import Database
from babu.tools.executor import ToolExecutor
from babu.tools.policy import Policy
from babu.tools.registry import ToolRegistry


@asynccontextmanager
async def open_store(config: ApplicationConfig) -> AsyncIterator[RunStore]:
    directory = config.harness.state_dir
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    database = Database(f"sqlite+aiosqlite:///{directory / 'babu.db'}")
    try:
        await database.create_schema()
        yield RunStore(database)
    finally:
        await database.close()


@asynccontextmanager
async def open_harness(config: ApplicationConfig, env_file: Path | None = None) -> AsyncIterator[RunRunner]:
    plugins = PluginConfiguration(config)
    settings = config.harness
    async with AsyncExitStack() as stack:
        store = await stack.enter_async_context(open_store(config))
        registry = ToolRegistry()
        if plugins.notion and plugins.notion.enabled:
            token = NotionSecrets.load(env_file).token
            plugin = NotionPlugin(plugins.notion, NotionClient(token.get_secret_value()))
            stack.push_async_callback(plugin.close)
            plugin.register(registry)
        llm = LLMClient(
            str(config.llm.base_url), config.llm.model, config.llm.temperature, config.llm.max_tokens, config.llm.timeout_seconds
        )
        stack.push_async_callback(llm.close)
        path = settings.instructions_path or Path(__file__).parent / "harness/default_preferences.md"
        raw = path.read_bytes()
        if len(raw) > settings.instructions_max_bytes:
            raise ValueError("Instructions exceed configured size limit")
        yield RunRunner(llm, registry, ToolExecutor(registry, Policy()), store, settings, raw.decode("utf-8"))
