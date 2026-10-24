from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from babu.plugins.notion.schemas.task import RelationRef

from .client import NotionClient, NotionError

if TYPE_CHECKING:
    from .schema import NotionSchemaCache


class RelationResolutionError(NotionError):
    pass


@dataclass(frozen=True)
class ResolvedRelation:
    id: str
    name: str


def _title(page: dict[str, Any], property_name: str) -> str:
    value = page.get("properties", {}).get(property_name, {}).get("title", [])
    return "".join(item.get("plain_text", "") for item in value).strip()


class RelationResolver:
    def __init__(self, client: NotionClient, schema_cache: "NotionSchemaCache | None" = None) -> None:
        self.client = client
        self.schema_cache = schema_cache

    async def resolve(self, data_source_id: str, title_property: str | None, reference: RelationRef) -> ResolvedRelation:
        if reference.id:
            return ResolvedRelation(reference.id, reference.id)
        assert reference.name is not None
        if not title_property and self.schema_cache:
            title_property = await self.schema_cache.get_title_property_name(data_source_id)
        prop_name = title_property or "title"
        pages = await self.client.query_data_source(
            data_source_id,
            {"filter": {"property": prop_name, "title": {"equals": reference.name}}, "page_size": 6},
        )
        matches = [(str(page.get("id", "")), _title(page, prop_name)) for page in pages]
        matches = [(page_id, name) for page_id, name in matches if name.casefold() == reference.name.casefold()]
        if not matches:
            raise RelationResolutionError("RELATION_NOT_FOUND", f"找不到 relation：{reference.name}")
        if len(matches) > 1:
            names = "、".join(name for _, name in matches[:5])
            raise RelationResolutionError("RELATION_AMBIGUOUS", f"Relation 不唯一：{names}")
        return ResolvedRelation(*matches[0])
