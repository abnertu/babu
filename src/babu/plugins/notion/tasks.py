from typing import TYPE_CHECKING, Any

from babu.plugins.notion.schemas.query import ListTasksArgs
from babu.plugins.notion.schemas.task import CreateTaskArgs
from babu.schemas.common import ToolResult

from .client import NotionClient, NotionError
from .mapping import (
    date_range_property,
    extract_page_properties,
    merge,
    multi_select_property,
    number_property,
    paragraph_blocks,
    people_property,
    relation_property,
    rich_text_property,
    select_property,
    status_property,
    title_property,
)
from .relations import RelationResolver

if TYPE_CHECKING:
    from .schema import NotionSchemaCache

MAX_DESCRIPTION_LENGTH = 2000


def _description_text(note: str | None, body: str | None) -> str | None:
    value = (note or body or "").strip()
    if not value:
        return None
    if len(value) <= MAX_DESCRIPTION_LENGTH:
        return value
    return value[: MAX_DESCRIPTION_LENGTH - 1].rstrip() + "…"


class TaskService:
    def __init__(
        self,
        client: NotionClient,
        data_source_id: str,
        properties: dict[str, str] | None = None,
        resolver: RelationResolver | None = None,
        project_data_source_id: str | None = None,
        schema_cache: "NotionSchemaCache | None" = None,
    ) -> None:
        self.client, self.data_source_id = client, data_source_id
        self.properties = dict(properties or {})
        self.resolver = resolver
        self.project_data_source_id = project_data_source_id
        self.schema_cache = schema_cache

    async def _get_properties(self) -> dict[str, str]:
        if self.schema_cache:
            return await self.schema_cache.resolve_task_properties(self.data_source_id, self.properties)
        fallback = {"title": "Name"}
        fallback.update(self.properties)
        return fallback

    async def create(self, args: CreateTaskArgs, external_id: str | None = None) -> ToolResult:
        try:
            p = await self._get_properties()
            title_prop = p.get("title", "Name")
            properties = merge(title_property(title_prop, args.title))
            description = _description_text(args.description, None)
            if args.due_at:
                properties.update(date_range_property(p.get("due", "Due"), args.due_at, args.due_end_at))
            if args.status and p.get("status"):
                properties.update(status_property(p["status"], args.status))
            if args.priority:
                properties.update(status_property(p["priority"], args.priority.capitalize()))
            description_property = p.get("description") or p.get("note")
            if description and description_property:
                properties.update(rich_text_property(description_property, description))
            if external_id and p.get("external_id"):
                properties.update(rich_text_property(p["external_id"], external_id))

            if args.project and self.resolver and p.get("project"):
                proj_target_id = self.project_data_source_id
                if not proj_target_id and self.schema_cache:
                    proj_target_id = await self.schema_cache.get_relation_target_data_source_id(self.data_source_id, p["project"])
                if proj_target_id:
                    proj_title = await self.schema_cache.get_title_property_name(proj_target_id) if self.schema_cache else "Name"
                    project = await self.resolver.resolve(proj_target_id, proj_title, args.project)
                    properties.update(relation_property(p["project"], [project.id]))
            if args.parent_task and self.resolver and p.get("parent_task"):
                parent = await self.resolver.resolve(self.data_source_id, title_prop, args.parent_task)
                properties.update(relation_property(p["parent_task"], [parent.id]))
            if args.assignee_user_ids and p.get("assignee"):
                properties.update(people_property(p["assignee"], args.assignee_user_ids))
            if args.smart_list and p.get("smart_list"):
                properties.update(select_property(p["smart_list"], args.smart_list))
            if args.recur_interval is not None and p.get("recur_interval"):
                from decimal import Decimal

                properties.update(number_property(p["recur_interval"], Decimal(args.recur_interval)))
            if args.recur_unit and p.get("recur_unit"):
                properties.update(select_property(p["recur_unit"], args.recur_unit))
            if args.recur_days and p.get("recur_days"):
                properties.update(multi_select_property(p["recur_days"], args.recur_days))
            page = await self.client.create_page(
                self.data_source_id,
                properties,
                paragraph_blocks(args.body) if args.body else None,
            )
            return ToolResult.ok(
                {
                    "task_id": page.get("id", ""),
                    "title": args.title,
                    "due_at": args.due_at.isoformat() if args.due_at else None,
                    "body_written": bool(args.body),
                }
            )
        except NotionError as exc:
            return exc.result()

    async def list(self, args: ListTasksArgs) -> ToolResult:
        try:
            p = await self._get_properties()
            due_prop = p.get("due")
            if (args.due_from or args.due_to) and not due_prop:
                return ToolResult.fail("UNSUPPORTED_FILTER", "The task database has no due date property")
            filters: list[dict[str, Any]] = []
            states = {"unfinished": ("does_not_equal", "Done"), "doing": ("equals", "Doing"), "done": ("equals", "Done")}
            if args.state in states and p.get("status"):
                operator, name = states[args.state]
                filters.append({"property": p["status"], "status": {operator: name}})
            if args.due_from:
                filters.append({"property": due_prop, "date": {"on_or_after": args.due_from.isoformat()}})
            if args.due_to:
                filters.append({"property": due_prop, "date": {"on_or_before": args.due_to.isoformat()}})
            body: dict[str, Any] = {"page_size": args.limit}
            if due_prop:
                body["sorts"] = [{"property": due_prop, "direction": "ascending"}]
            if len(filters) > 1:
                body["filter"] = {"and": filters}
            elif filters:
                body["filter"] = filters[0]
            pages = await self.client.query_data_source(self.data_source_id, body)

            proj_prop_name = p.get("project")
            proj_target_id = self.project_data_source_id
            if not proj_target_id and self.schema_cache and proj_prop_name:
                proj_target_id = await self.schema_cache.get_relation_target_data_source_id(self.data_source_id, proj_prop_name)

            tasks = []
            for page in pages:
                item = extract_page_properties(page)
                if proj_target_id and self.schema_cache and proj_prop_name in item:
                    proj_val = item[proj_prop_name]
                    if isinstance(proj_val, list):
                        item[proj_prop_name] = await self.schema_cache.resolve_relation_names(proj_target_id, proj_val)
                tasks.append(item)

            return ToolResult.ok({"tasks": tasks, "count": len(tasks)})
        except NotionError as exc:
            return exc.result()
