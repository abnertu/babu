from typing import TYPE_CHECKING

from babu.plugins.notion.schemas.query import SearchNotionArgs
from babu.schemas.common import ToolResult

from .client import NotionClient, NotionError

if TYPE_CHECKING:
    from .schema import NotionSchemaCache


class NotionSearchService:
    def __init__(
        self,
        client: NotionClient,
        sources: dict[str, tuple[str, str | None]],
        schema_cache: "NotionSchemaCache | None" = None,
    ) -> None:
        self.client = client
        self.sources = sources
        self.schema_cache = schema_cache

    async def _resolve_source(self, alias: str) -> tuple[str, str | None] | None:
        if alias in self.sources:
            return self.sources[alias]
        if not self.schema_cache:
            return None
        tasks_ds = self.sources.get("tasks", (None,))[0]
        finance_ds = self.sources.get("transactions", (None,))[0]
        if alias == "categories" and finance_ds:
            target_ds = await self.schema_cache.get_relation_target_data_source_id(finance_ds, "Category")
            if target_ds:
                self.sources["categories"] = (target_ds, None)
                return self.sources["categories"]
        if alias == "accounts" and finance_ds:
            target_ds = await self.schema_cache.get_relation_target_data_source_id(finance_ds, "Account")
            if target_ds:
                self.sources["accounts"] = (target_ds, None)
                return self.sources["accounts"]
        if alias == "projects" and tasks_ds:
            target_ds = await self.schema_cache.get_relation_target_data_source_id(tasks_ds, "Project")
            if target_ds:
                self.sources["projects"] = (target_ds, None)
                return self.sources["projects"]
        return None

    async def search(self, args: SearchNotionArgs) -> ToolResult:
        if args.database:
            aliases = [args.database]
        else:
            for rel_alias in ("categories", "accounts", "projects"):
                await self._resolve_source(rel_alias)
            aliases = list(self.sources)
        results: list[dict[str, object]] = []
        try:
            for alias in aliases:
                source_info = await self._resolve_source(alias)
                if not source_info:
                    continue
                data_source_id, title_property = source_info
                if not title_property and self.schema_cache:
                    title_property = await self.schema_cache.get_title_property_name(data_source_id)
                prop_name = title_property or "title"
                pages = await self.client.query_data_source(
                    data_source_id,
                    {
                        "filter": {"property": prop_name, "title": {"contains": args.query}},
                        "page_size": min(args.limit, 100),
                    },
                )
                for page in pages:
                    title_items = page.get("properties", {}).get(prop_name, {}).get("title", [])
                    title = "".join(item.get("plain_text", "") for item in title_items)
                    results.append({"database": alias, "id": page.get("id", ""), "title": title, "url": page.get("url", "")})
                    if len(results) >= args.limit:
                        return ToolResult.ok({"results": results, "count": len(results)})
            return ToolResult.ok({"results": results, "count": len(results)})
        except NotionError as exc:
            return exc.result()
