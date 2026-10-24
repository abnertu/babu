# AGENTS Instructions

## Git

- Write commits in [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) format, with a body explaining why for non-trivial changes. Mark breaking changes with `!` and a `BREAKING CHANGE:` footer.
- Author commits as the user alone, with no `Co-Authored-By` or other co-author trailers.
- Commit as the plan progresses. Before pushing, squash related commits into one to keep history clean.
- Push only after the user approves that push.
- Version releases with [Semantic Versioning 2.0.0](https://semver.org/).

## Tests

- A test enters the repo only when it guards behaviour no existing test covers and fails when that behaviour breaks. Weigh this before adding each one.
- Run throwaway tests freely to check your work; keep them out of the repo unless they pass that bar.
- Prioritize schemas, authorization and status replies, LLM response parsing, tool-loop serialization, policy decisions, and Notion task mapping.
- Treat Notion writes and external model calls as side effects. Use fakes or mock transports in tests.

Run before handoff, including documentation changes:

```bash
uv sync --locked
uv run pytest -q
uv run ruff check src tests
uv run mypy src
nix flake check --show-trace --print-build-logs
nix build --no-link --show-trace --print-build-logs .#babu
git diff --check
```

## CI

- Keep a CI check only when it catches a concrete failure no existing check covers; identify that failure before adding or retaining it. Apply the test admission rule to CI too.
- Run repository entry points against real inputs or isolated fixtures. Reject duplicate checks, source-text assertions, copied inventories and checks that only prove a command or file exists.
- Name workflows, jobs and steps by their target and action. Propagate command failures; missing prerequisites and skipped required coverage cannot count as success.
- After changing these rules, audit the repository against them. Validate locally, then verify required CI on the pushed commit; report any uncovered contract or unavailable check.

## Docs

- Keep this file, `README.md` and every other doc concise and plain: each sentence carries information, with no filler.
- Update `README.md` when public behavior, configuration, deployment, or validation commands change. Keep operational instructions concise and implementation history out of repository documentation.
- Read `config.example.toml` before changing configuration or setup instructions.
- Read `src/babu/harness/default_preferences.md` before changing response preferences or instruction loading; it is runtime model input.
- Read `docs/harness.md` before changing run recovery, tool execution, or plugin contracts.

## Safety boundaries

- Keep Discord guild, channel, and user authorization enforced by `MessageAuthorizer`.
- Add capabilities through the explicit tool registry and validate arguments with Pydantic schemas.
- Do not add arbitrary shell, filesystem, HTTP, or Notion capabilities to the LLM.
- Keep environment files, tokens, and database contents out of output, commits, logs, and tests.
- Do not delete production configuration or runtime data, including `/etc/babu` and `/var/lib/babu` deployments, while changing code.

## Deployment

- Keep the systemd unit, service account, production configuration, and runtime directories in the host configuration, outside this application repository.
