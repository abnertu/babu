# Harness execution

The harness owns the lifecycle of a run. CLI and Discord are transport adapters; Notion supplies registered capabilities. Core modules under `src/babu/harness/` depend on neither transport nor Notion. `bootstrap.py` and `plugins/loader.py` compose the explicitly supported plugins.

## Requests and identities

A transport authenticates its caller before submitting `RunRequest(principal_id, session_id, source, event_id, content)`. Discord uses `MessageAuthorizer` for guild/channel/user checks and emits `discord:<user-id>` principals. The CLI uses the current OS user's `local:<uid>` principal. Neither exposes an option to impersonate another principal.

A source event identifies one immutable request. Reuse with different input or ownership is rejected. History and execution locks use both principal and session ID. Showing, resuming, approving, or cancelling a run verifies its owner.

## Checkpoints and receipts

The runner persists each model tool-call batch before starting its tools. Every operation gets a stable `<run-id>:<tool-index>` ID, and its write classification is saved independently of the current plugin set. Its intent is committed before invocation, and its complete result is committed before the run advances its cursor. Full receipts remain in SQLite when model-visible output is shortened; inspect them with `babu show RUN_ID --receipts`.

After an interruption, completed receipts are reused without repeating the corresponding action. An interrupted read can be retried. An interrupted write without a completed receipt stops as `outcome_unknown`; SQLite cannot atomically commit a remote Notion write. The optional Notion `properties.external_id` mapping records the operation ID for manual investigation. Babu does not automatically reconcile uncertain Notion writes or promise exactly-once remote execution.

Same-process requests for one session are serialized. Cross-process session leases reject competing execution. A crashed process's lease expires after the configured run timeout plus 60 seconds; resume after expiry. Each active execution has a deadline shorter than its lease. No background worker automatically resumes runs at startup: use `resume`, or redeliver the original source event.

## Waiting and stopping

`awaiting_approval` saves the original tool call and approval deadline without invoking the handler. The original arguments are displayed for review. Approval is consumed for that operation only. Tool definitions, risks, application version, and plugin routing bindings are fingerprinted; changes invalidate continuation rather than applying an old approval to a new destination. Rejection or expiry cancels the run. Existing Notion create tools retain their configured safe-write policy; sensitive or destructive registered tools require approval.

`awaiting_input` saves a tool's clarification result and closes the remaining tool-call batch without executing it. Resume with input to let the model produce corrected arguments. Waiting does not consume execution time; model/tool call counters and elapsed execution time survive resumes.

`completed`, `partial`, `failed`, `exhausted`, and `cancelled` are terminal states. `partial` records a final model answer after a tool error. Budget exhaustion preserves successful receipts and reports the limit explicitly. Cancellation prevents subsequent tool dispatch and cannot undo an action already in flight. It does not roll back completed external writes.

## Plugin contracts

Capability plugins register a Pydantic argument schema, description, risk classification, and async handler with `ToolRegistry`. Handlers receive validated arguments and an immutable `ExecutionContext` containing principal, session, run, and operation IDs. Return `ToolResult`; mark missing input with `requires_input` and uncertain effects with `outcome_unknown`.

The host constructs plugin clients, loads credentials only for enabled plugins, and closes resources on shutdown. Plugins are trusted code in the application's process; registration controls model access and does not sandbox Python code. New plugins require explicit loader registration and dependencies fixed at build time.

Transport adapters submit requests or authenticated run-control commands and render `RunEvent` notifications. Events carry sequence numbers, timestamps, run IDs, and operation metadata. Presentation failures do not invalidate a committed tool receipt; replayed source events can retrieve the saved result. Run events and receipts are inspectable by their owner; application logs contain identifiers and error categories rather than prompts, tool arguments, or credentials.

## Validation

Exercise the harness through its runner with scripted models and fake tools, and use mock HTTP transports for Notion. Preserve coverage for interruption around writes, duplicate events, approval ownership, session isolation, cancellation, deadlines, and bounded output. CLI tests start a local fake model endpoint and run the actual command against temporary XDG directories and SQLite files. Production APIs are excluded from the test suite.
