import pytest

from babu.plugins.notion.schemas.task import CreateTaskArgs
from babu.plugins.notion.tasks import TaskService


class FakeNotion:
    def __init__(self):
        self.properties = None
        self.children = None

    async def create_page(self, data_source_id, properties, children=None):
        self.properties = properties
        self.children = children
        return {"id": "page-id"}


class FakeResolver:
    async def resolve(self, data_source_id, title_property, reference):
        from babu.plugins.notion.relations import ResolvedRelation

        return ResolvedRelation("related-id", reference.name or "related")


@pytest.mark.asyncio
async def test_long_task_content_is_written_to_page_body():
    notion = FakeNotion()
    service = TaskService(notion, "source-id", {"title": "Name", "due_date": "Due"})
    result = await service.create(CreateTaskArgs(title="整理伺服器", description="更新套件", body="1. 更新套件\n2. 檢查服務"))
    assert result.success is True
    assert result.data["body_written"] is True
    assert [block["numbered_list_item"]["rich_text"][0]["text"]["content"] for block in notion.children] == [
        "更新套件",
        "檢查服務",
    ]


@pytest.mark.asyncio
async def test_description_is_written_without_creating_a_body():
    notion = FakeNotion()
    service = TaskService(notion, "source-id", {"title": "Name", "description": "Description"})

    result = await service.create(CreateTaskArgs(title="整理伺服器", description="更新套件並檢查服務"))

    assert result.success is True
    assert notion.properties["Description"] == {"rich_text": [{"text": {"content": "更新套件並檢查服務"}}]}
    assert notion.children is None


@pytest.mark.asyncio
async def test_description_is_omitted_when_not_provided():
    notion = FakeNotion()
    service = TaskService(notion, "source-id", {"title": "Name", "description": "Description"})

    result = await service.create(CreateTaskArgs(title="整理伺服器"))

    assert result.success is True
    assert "Description" not in notion.properties


@pytest.mark.asyncio
async def test_description_and_body_are_written_to_separate_destinations():
    notion = FakeNotion()
    service = TaskService(notion, "source-id", {"title": "Name", "description": "Description"})

    result = await service.create(
        CreateTaskArgs(title="整理伺服器", description="更新伺服器套件", body="**更新套件**\n- 檢查服務")
    )

    assert result.success is True
    assert notion.properties["Description"]["rich_text"][0]["text"]["content"] == "更新伺服器套件"
    assert notion.children[0]["type"] == "paragraph"
    assert notion.children[0]["paragraph"]["rich_text"][0]["annotations"]["bold"] is True
    assert notion.children[1]["type"] == "bulleted_list_item"


@pytest.mark.asyncio
async def test_description_and_body_remain_within_their_own_limits():
    notion = FakeNotion()
    service = TaskService(notion, "source-id", {"title": "Name", "description": "Description"})
    body = "x" * 20000

    result = await service.create(CreateTaskArgs(title="長任務", description="長任務摘要", body=body))

    assert result.success is True
    description = notion.properties["Description"]["rich_text"][0]["text"]["content"]
    assert description == "長任務摘要"
    assert notion.children[0]["paragraph"]["rich_text"][0]["text"]["content"] == body


@pytest.mark.asyncio
async def test_task_project_is_written_as_relation():
    notion = FakeNotion()
    service = TaskService(notion, "tasks", {"title": "Name", "project": "Project"}, FakeResolver(), "projects")
    result = await service.create(CreateTaskArgs(title="部署", project={"name": "Babu"}))
    assert result.success is True
    assert notion.properties["Project"] == {"relation": [{"id": "related-id"}]}


@pytest.mark.asyncio
async def test_task_auto_detects_schema_and_accepts_string_project():
    from babu.plugins.notion.schema import NotionSchemaCache

    class SchemaNotion(FakeNotion):
        async def retrieve_data_source(self, data_source_id):
            if data_source_id == "tasks":
                return {
                    "properties": {
                        "任務": {"type": "title"},
                        "狀態": {"type": "status"},
                        "專案": {
                            "type": "relation",
                            "relation": {"data_source_id": "projects-auto"},
                        },
                    }
                }
            if data_source_id == "projects-auto":
                return {"properties": {"專案名稱": {"type": "title"}}}
            return {"properties": {}}

    notion = SchemaNotion()
    cache = NotionSchemaCache(notion)
    service = TaskService(
        notion,
        "tasks",
        properties={},
        resolver=FakeResolver(),
        schema_cache=cache,
    )
    result = await service.create(CreateTaskArgs(title="測試任務", status="To Do", project="AI Agent"))
    assert result.success is True
    assert notion.properties["任務"] == {"title": [{"text": {"content": "測試任務"}}]}
    assert notion.properties["狀態"] == {"status": {"name": "To Do"}}
    assert notion.properties["專案"] == {"relation": [{"id": "related-id"}]}


@pytest.mark.asyncio
async def test_task_list_filters_and_sorts_by_due_date():
    from datetime import date

    from babu.plugins.notion.schemas.query import ListTasksArgs

    class QueryNotion(FakeNotion):
        async def query_data_source(self, data_source_id, body):
            self.body = body
            return []

    notion = QueryNotion()
    service = TaskService(notion, "tasks", {"title": "Name", "due": "Due", "status": "Status"})
    result = await service.list(ListTasksArgs(state="doing", due_from=date(2026, 10, 26), due_to=date(2026, 11, 1)))
    assert result.success is True
    assert notion.body["sorts"] == [{"property": "Due", "direction": "ascending"}]
    assert notion.body["filter"] == {
        "and": [
            {"property": "Status", "status": {"equals": "Doing"}},
            {"property": "Due", "date": {"on_or_after": "2026-10-26"}},
            {"property": "Due", "date": {"on_or_before": "2026-11-01"}},
        ]
    }


@pytest.mark.asyncio
async def test_task_list_excludes_done_tasks_by_default():
    from babu.plugins.notion.schemas.query import ListTasksArgs

    class QueryNotion(FakeNotion):
        async def query_data_source(self, data_source_id, body):
            self.body = body
            return []

    notion = QueryNotion()
    service = TaskService(notion, "tasks", {"title": "Name", "status": "Status"})
    await service.list(ListTasksArgs())
    assert notion.body["filter"] == {"property": "Status", "status": {"does_not_equal": "Done"}}
    await service.list(ListTasksArgs(state="done"))
    assert notion.body["filter"] == {"property": "Status", "status": {"equals": "Done"}}
