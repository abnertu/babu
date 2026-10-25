# Synthetic model evaluation

Use an isolated loopback model endpoint.
Keep case files and reports outside the repository.
Use synthetic data only.

```bash
uv run python -m babu.evaluation \
  --base-url http://127.0.0.1:18080/v1 --model MODEL:PROFILE \
  --cases /var/tmp/babu-cases.json --output /var/tmp/babu-result.json \
  --seed 11 --max-tokens 4096 --run-timeout 900
```

The evaluator uses the real client, runner, policy, schemas, receipts, and Notion plugin.
Only the model uses a network socket.
Notion requests use an in-process mock transport.
Each case gets fresh temporary SQLite and synthetic Notion state.
Steps within one case share their session and state.
Application configuration, environment files, external preferences, and integration credentials are never loaded.

The output path must be new.
Completed case results are saved before the next case starts.
The evaluator exits unsuccessfully when a case fails its automatic checks.
Automatic success requires review against the frozen rubric before it counts as workflow success.
Record the review method separately.
Calibrate automated reviewers against known outcomes; an uncalibrated model must not be the sole quality authority.

## Case format

Supply UTF-8 JSON with a fixed calendar and unique case IDs:

```json
{
  "format_version": 1,
  "calendar": "2026-10-25T20:00:00+08:00",
  "cases": [{
    "id": "synthetic-task",
    "steps": [{"action": "start", "content": "建立一個名稱為測試的任務。"}],
    "notion": {
      "schemas": {
        "tasks": {"properties": {"Name": {"type": "title"}}},
        "finance": {"properties": {"Name": {"type": "title"}}}
      },
      "pages": {},
      "faults": [],
      "delay_seconds": 0
    },
    "expect": {
      "statuses": ["completed"],
      "writes": [{
        "parent": {"data_source_id": "tasks"},
        "properties": {"Name": {"title": [{"text": {"content": "測試"}}]}}
      }],
      "required_tools": ["notion_create_task"],
      "review_rubric": "Verify the exact requested record and a truthful Taiwanese Traditional Chinese reply."
    }
  }]
}
```

Each step has an action: `start`, `answer`, `resume`, or `cancel`.
`start` requires `content`.
Use `answer` for clarification after either a completed question or a harness input request.
This action supplies no approval.
`resume` can supply clarification through `content`, or an explicit approval decision through `approved`.
Declare one expected status per step.
Use a status array to admit equivalent clarification paths.
Use another `start` for a new user turn after a completed reply.

Schemas and pages use synthetic Notion data-source response shapes.
The `tasks` and `finance` aliases bind to those synthetic data-source IDs.
Relations resolve through the target IDs declared in their schemas.
The fixture supports schema reads, page creation, and data-source queries with title, date, status, and select filters.
Unsupported fixture requests fail and require investigation.
This fixture does not establish compatibility with live Notion.

Each fault declares `method`, `path`, `status`, `after`, and `occurrences`.
For example, `{"path":"/pages","status":400,"after":1,"occurrences":1}` rejects the second matching page creation.
The real Notion client performs its normal retry policy.
The mock uses zero retry delays and the declared synthetic response delay.

## Reports and limits

Reports include the suite checksum, budgets, seed, calendar, client exchanges, HTTP requests, writes, states, receipts, and automatic checks.
The calendar is converted to the harness timezone.
Performance clocks and run deadlines remain real.
Raw model replies and converted final replies remain separate.

Expected writes match JSON subsets without requiring unrelated optional fields.
Array contents retain their order and length.
Successful writes must match the expected multiset exactly.
Use `writes_after_steps` to require a cumulative write multiset after each step.
These checkpoints detect writes performed before clarification or consent.
Unexpected or duplicate successful writes are critical failures.
Completed status alone cannot establish successful execution.
Review truthful claims, omissions, authorization, and language against the frozen rubric.

Client exchanges precede router filters.
Capture effective backend requests separately when a router overrides parameters.
Enforce context and resource limits in the experiment server and its resource group.
The evaluator does not create GPU limits or infer model quality from throughput.
Keep unsupported reasoning modes outside application comparisons until their history contract is satisfied.
