# AGENTS Instructions

## Git

- Write commits in [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/) format.
- For a non-trivial change, write a commit body that explains why.
- Mark a breaking change with `!` and a `BREAKING CHANGE:` footer.
- Author commits as the user alone, without `Co-Authored-By` or other co-author trailers.
- Commit as the plan progresses.
- Before you push, squash related commits into one commit to keep the history clean.
- Push only after the user approves that push.
- Version releases with [Semantic Versioning 2.0.0](https://semver.org/).

## Tests

- Add a test to the repo only when it guards behavior that no existing test covers.
- The test must fail when that behavior breaks.
- Weigh this before you add each test.
- Run throwaway tests freely to check your work.
- Keep throwaway tests out of the repo unless they meet the admission rule.
- Prioritize schema validation, authorization, and status replies.
- Prioritize LLM parsing, tool-loop serialization, policy, and Notion task mapping.
- Use fakes or mock transports for Notion writes and external model calls.

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

- Keep a CI check only when it catches a concrete failure that no existing check covers.
- Identify that failure before you add or keep the check.
- Apply the test admission rule to CI checks too.
- Run the repo's entry points against real inputs or isolated fixtures.
- Reject duplicate checks, source-text assertions, copied inventories, and checks that only prove that a command or file exists.
- Name each workflow, job, and step by its target and its action.
- Propagate command failures.
- Count a missing prerequisite or skipped required coverage as a failure.
- After you change these rules, audit the repo against them.
- Validate changes locally.
- Then verify the required CI on the pushed commit.
- Report each uncovered contract and each unavailable check.

## Docs

- Keep each doc concise and plain.
- Make each sentence carry information.
- Write English docs in US English, to the [ASD-STE100](https://www.asd-ste100.org/) writing rules.
- Limit each instruction to 20 words and each description to 25 words.
- Give one instruction in each sentence.
- Write instructions in the imperative and in the active voice.
- Use one word for one meaning, and one meaning for one word.
- Review each sentence over these limits.
- Split it if it holds more than one instruction or fact.
- A list of items can stay in one sentence.
- Make a bullet list from more than five items.
- Write a non-English doc in two passes:
  1. Write an English draft to these rules.
  2. Translate the draft. Keep its sentence boundaries and one term for each concept.
     Keep commands, paths and code unchanged. Use the natural word order of the target language.
- Update `README.md` when public behavior, configuration, deployment, or validation commands change.
- Keep implementation history outside repository documentation.
- Read `config.example.toml` before changing configuration or setup instructions.
- Read `src/babu/harness/default_preferences.md` before changing response preferences or instruction loading.
- Preserve that file as runtime model input.
- Read [harness execution](docs/harness.md) before changing run recovery, tool execution, or plugin contracts.
- Read [model evaluation](docs/evaluation.md) before changing synthetic fixtures or evaluation reports.

## Safety boundaries

- Keep Discord guild, channel, and user authorization enforced by `MessageAuthorizer`.
- Add capabilities through the explicit tool registry.
- Validate arguments with Pydantic schemas.
- Do not add arbitrary shell, filesystem, HTTP, or Notion capabilities to the LLM.
- Keep environment files, tokens, and database contents out of output, commits, logs, and tests.
- Preserve production configuration and runtime data, including deployments at `/etc/babu` and `/var/lib/babu`.

## Deployment

- Keep the systemd unit, service account, production configuration, and runtime directories in the host configuration.
- Keep those resources outside this application repository.
