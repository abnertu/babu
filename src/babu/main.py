import argparse
import asyncio
import json
import logging
import os
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from babu import __version__
from babu.bootstrap import open_harness, open_store
from babu.config import ApplicationConfig, config_directory, default_config_path
from babu.harness.store import SessionBusyError
from babu.plugins.cli import principal_id, progress, request
from babu.plugins.loader import PluginConfiguration


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="babu")
    result.add_argument("--version", action="version", version=f"Babu {__version__}")
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("run", "chat", "check-config", "check-runtime", "runs", "show", "resume", "cancel"):
        command = commands.add_parser(name)
        command.add_argument("--config", type=Path, default=default_config_path())
        command.add_argument("--env-file", type=Path)
        if name == "chat":
            command.add_argument("message")
            command.add_argument("--session", default="default")
        if name in {"show", "resume", "cancel"}:
            command.add_argument("run_id")
        if name == "show":
            inspection = command.add_mutually_exclusive_group()
            inspection.add_argument("--events", action="store_true")
            inspection.add_argument("--receipts", action="store_true")
        if name == "resume":
            command.add_argument("--approve", action="store_true", default=None)
            command.add_argument("--input")
    return result


async def execute(args: argparse.Namespace, config: ApplicationConfig, env_file: Path | None) -> None:
    if args.command in {"runs", "show", "cancel"}:
        async with open_store(config) as store:
            if args.command == "runs":
                for state in await store.list_runs(principal_id()):
                    print(f"{state.id} {state.status} {state.request.session_id}")
                return
            if args.command == "show":
                state = await store.get(args.run_id, principal_id())
                if args.events:
                    for event in await store.events(state.id, principal_id()):
                        print(event.model_dump_json())
                    return
                if args.receipts:
                    print(json.dumps(await store.receipts(state.id, principal_id()), indent=2, ensure_ascii=False))
                    return
                print(state.model_dump_json(indent=2))
            else:
                state = await store.cancel(args.run_id, principal_id())
                if state.status == "cancelled":
                    await store.event(state.id, "RunCancelled")
                print(f"[{state.id}] {state.status}\n{state.content}")
        return
    async with open_harness(config, env_file) as harness:
        if args.command == "run":
            from babu.plugins.discord.bot import DiscordPlugin
            from babu.plugins.discord.config import DiscordSecrets

            settings = PluginConfiguration(config).discord
            if settings is None or not settings.enabled:
                raise ValueError("Enable the Discord plugin for 'run', or use 'babu chat <message>'")
            async with DiscordPlugin(settings, harness) as plugin:
                await plugin.start(DiscordSecrets.load(env_file).token.get_secret_value())
            return
        if args.command == "chat":
            state = await harness.start(request(args.message, args.session), progress)
        else:
            state = await harness.resume(args.run_id, principal_id(), approved=args.approve, content=args.input, sink=progress)
        print(f"[{state.id}] {state.status}\n{state.content}")


def main() -> None:
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    argument_parser = parser()
    args = argument_parser.parse_args()
    try:
        config = ApplicationConfig.from_toml(args.config)
        plugins = PluginConfiguration(config)
        env_file = args.env_file.expanduser() if args.env_file else None
        if env_file is not None and not env_file.is_file():
            raise ValueError("The specified environment file does not exist")
        if env_file is None and (config_directory() / "agent.env").is_file():
            env_file = config_directory() / "agent.env"
        if args.command == "check-config":
            print("Babu configuration is valid")
            return
        if args.command == "check-runtime":
            plugins.check_secrets(env_file)
            print("Babu runtime configuration is valid")
            return
        asyncio.run(execute(args, config, env_file))
    except SQLAlchemyError:
        argument_parser.error("Cannot access the Babu state database")
    except (ValueError, OSError, SessionBusyError) as exc:
        # Pydantic errors hide input values; environment-file contents and tokens are never logged.
        argument_parser.error(str(exc))


if __name__ == "__main__":
    main()
