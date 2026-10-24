import json
from typing import Any

from babu.harness.models import ExecutionContext
from babu.schemas.common import ToolResult
from babu.tools.base import RegisteredTool, ToolRisk
from babu.tools.registry import ToolRegistry

from .client import NotionClient, NotionError
from .config import NotionSettings, NotionUserDatabases
from .finance import FinanceService
from .relations import RelationResolver
from .schema import NotionSchemaCache
from .schemas.expense import AddTransactionArgs
from .schemas.query import GetDatabaseInfoArgs, ListTasksArgs, QueryExpensesArgs, SearchNotionArgs
from .schemas.task import CreateTaskArgs
from .search import NotionSearchService
from .tasks import TaskService


def _create_user_services(
    notion: NotionClient,
    resolver: RelationResolver,
    databases: NotionUserDatabases,
    schema_cache: NotionSchemaCache | None = None,
) -> tuple[TaskService, FinanceService, NotionSearchService]:
    task_properties = dict(databases.tasks.properties)
    tasks = TaskService(
        notion,
        databases.tasks.data_source_id,
        task_properties,
        resolver,
        databases.projects.data_source_id if databases.projects else None,
        schema_cache=schema_cache,
    )
    finance_properties = dict(databases.finance.properties)
    finance = FinanceService(
        notion,
        databases.finance.data_source_id,
        finance_properties,
        resolver,
        databases.categories.data_source_id if databases.categories else None,
        databases.accounts.data_source_id if databases.accounts else None,
        schema_cache=schema_cache,
    )
    sources: dict[str, tuple[str, str | None]] = {
        "tasks": (databases.tasks.data_source_id, task_properties.get("title")),
        "transactions": (databases.finance.data_source_id, finance_properties.get("title")),
    }
    for alias, source in (
        ("projects", databases.projects),
        ("categories", databases.categories),
        ("accounts", databases.accounts),
    ):
        if source:
            sources[alias] = (source.data_source_id, source.properties.get("title"))
    search = NotionSearchService(notion, sources, schema_cache=schema_cache)
    return tasks, finance, search


class NotionPlugin:
    def __init__(self, settings: NotionSettings, client: NotionClient) -> None:
        self.settings, self.client = settings, client
        self.schema_cache = NotionSchemaCache(client)
        self.resolver = RelationResolver(client, schema_cache=self.schema_cache)
        self._services: dict[str, tuple[TaskService, FinanceService, NotionSearchService]] = {}

    async def close(self) -> None:
        await self.client.close()

    def services(self, context: ExecutionContext) -> tuple[TaskService, FinanceService, NotionSearchService]:
        principal = context.principal_id
        if principal not in self._services:
            self._services[principal] = _create_user_services(
                self.client,
                self.resolver,
                self.settings.get_for_user(principal),
                self.schema_cache,
            )
        return self._services[principal]

    def register(self, registry: ToolRegistry) -> None:
        for name, description, schema, method, risk in (
            (
                "notion_create_task",
                "Create a Notion task with optional Markdown body.",
                CreateTaskArgs,
                self.create_task,
                ToolRisk.WRITE_SAFE,
            ),
            (
                "notion_add_transaction",
                "Record income or an expense.",
                AddTransactionArgs,
                self.add_transaction,
                ToolRisk.WRITE_SAFE,
            ),
            (
                "notion_list_tasks",
                "List the principal's Notion tasks, unfinished ones by default. Call it for every question about the user's "
                "tasks, to-dos or schedule; the conversation does not contain them. State each task's status in the reply.",
                ListTasksArgs,
                self.list_tasks,
                ToolRisk.READ,
            ),
            (
                "notion_query_expenses",
                "Query the principal's transactions. Call it for every question about the user's income, spending or "
                "balances; the conversation does not contain them.",
                QueryExpensesArgs,
                self.query_expenses,
                ToolRisk.READ,
            ),
            ("notion_search", "Search configured Notion data sources by title.", SearchNotionArgs, self.search, ToolRisk.READ),
            (
                "notion_get_database_info",
                "Inspect schema and available property options.",
                GetDatabaseInfoArgs,
                self.database_info,
                ToolRisk.READ,
            ),
        ):
            registry.register(
                RegisteredTool(
                    name,
                    description,
                    schema,
                    method,
                    risk,
                    binding=json.dumps(self.settings.model_dump(mode="json"), sort_keys=True),
                )
            )

    async def create_task(self, args: Any, context: ExecutionContext) -> ToolResult:
        return await self.services(context)[0].create(args, external_id=context.operation_id)

    async def add_transaction(self, args: Any, context: ExecutionContext) -> ToolResult:
        return await self.services(context)[1].add(args, external_id=context.operation_id)

    async def list_tasks(self, args: Any, context: ExecutionContext) -> ToolResult:
        return await self.services(context)[0].list(args)

    async def query_expenses(self, args: Any, context: ExecutionContext) -> ToolResult:
        return await self.services(context)[1].query(args)

    async def search(self, args: Any, context: ExecutionContext) -> ToolResult:
        return await self.services(context)[2].search(args)

    async def database_info(self, args: Any, context: ExecutionContext) -> ToolResult:
        try:
            databases = self.settings.get_for_user(context.principal_id)
            aliases = {
                "task": "tasks",
                "finance": "finance",
                "transactions": "finance",
                "expenses": "finance",
                "expense": "finance",
                "category": "categories",
                "account": "accounts",
                "project": "projects",
            }
            alias = aliases.get(args.database.lower().strip(), args.database.lower().strip())
            source = (
                getattr(databases, alias, None) if alias in {"tasks", "finance", "categories", "accounts", "projects"} else None
            )
            data_source_id = source.data_source_id if source else None
            if not data_source_id and alias in {"categories", "accounts", "projects"}:
                parent_id = databases.tasks.data_source_id if alias == "projects" else databases.finance.data_source_id
                properties = (
                    await self.schema_cache.resolve_task_properties(parent_id)
                    if alias == "projects"
                    else await self.schema_cache.resolve_finance_properties(parent_id)
                )
                key = {"categories": "category", "accounts": "account", "projects": "project"}[alias]
                if properties.get(key):
                    data_source_id = await self.schema_cache.get_relation_target_data_source_id(parent_id, properties[key])
            if not data_source_id:
                return ToolResult.fail("DATABASE_NOT_FOUND", "No configured data source for this alias", requires_input=True)
            if args.property:
                return ToolResult.ok(await self.schema_cache.get_property_options(data_source_id, args.property))
            if alias in {"categories", "accounts", "projects"}:
                options = await self.schema_cache.get_relation_options(data_source_id)
                return ToolResult.ok({"database": alias, "options": [item["name"] for item in options]})
            return ToolResult.ok(await self.schema_cache.get_database_summary(data_source_id))
        except NotionError as exc:
            return ToolResult.fail(exc.code, str(exc))
