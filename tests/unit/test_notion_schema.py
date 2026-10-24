import pytest

from babu.plugins.notion.schema import NotionSchemaCache


class FakeClient:
    def __init__(self, schemas):
        self.schemas = schemas
        self.call_count = 0

    async def retrieve_data_source(self, data_source_id: str):
        self.call_count += 1
        if data_source_id in self.schemas:
            return self.schemas[data_source_id]
        raise ValueError(f"Unknown data source: {data_source_id}")


@pytest.mark.asyncio
async def test_schema_cache_caches_results():
    schemas = {
        "ds-1": {
            "properties": {
                "名稱": {"type": "title"},
                "金額": {"type": "number"},
            }
        }
    }
    client = FakeClient(schemas)
    cache = NotionSchemaCache(client)

    s1 = await cache.get_schema("ds-1")
    s2 = await cache.get_schema("ds-1")
    assert s1 == s2
    assert client.call_count == 1
    assert await cache.get_title_property_name("ds-1") == "名稱"


@pytest.mark.asyncio
async def test_resolve_finance_properties_chinese_and_english():
    schemas = {
        "chinese-finance": {
            "properties": {
                "項目名稱": {"type": "title"},
                "花費金額": {"type": "number"},
                "消費日期": {"type": "date"},
                "收支類型": {"type": "select"},
                "類別": {
                    "type": "relation",
                    "relation": {"data_source_id": "cat-ds", "database_id": "cat-db"},
                },
                "支付方式": {
                    "type": "relation",
                    "relation": {"data_source_id": "acc-ds", "database_id": "acc-db"},
                },
            }
        },
        "english-finance": {
            "properties": {
                "Item Name": {"type": "title"},
                "Amount": {"type": "number"},
                "Date": {"type": "date"},
                "Type": {"type": "select"},
                "Category": {
                    "type": "relation",
                    "relation": {"data_source_id": "cat-ds-2"},
                },
                "Account": {
                    "type": "relation",
                    "relation": {"database_id": "acc-db-2"},
                },
            }
        },
    }
    client = FakeClient(schemas)
    cache = NotionSchemaCache(client)

    # Test Chinese resolution
    cn_props = await cache.resolve_finance_properties("chinese-finance")
    assert cn_props["title"] == "項目名稱"
    assert cn_props["amount"] == "花費金額"
    assert cn_props["date"] == "消費日期"
    assert cn_props["type"] == "收支類型"
    assert cn_props["category"] == "類別"
    assert cn_props["account"] == "支付方式"

    # Test relation target extraction
    assert await cache.get_relation_target_data_source_id("chinese-finance", "類別") == "cat-ds"
    assert await cache.get_relation_target_data_source_id("chinese-finance", "支付方式") == "acc-ds"

    # Test English resolution
    en_props = await cache.resolve_finance_properties("english-finance")
    assert en_props["title"] == "Item Name"
    assert en_props["amount"] == "Amount"
    assert en_props["date"] == "Date"
    assert en_props["type"] == "Type"
    assert en_props["category"] == "Category"
    assert en_props["account"] == "Account"

    assert await cache.get_relation_target_data_source_id("english-finance", "Category") == "cat-ds-2"
    assert await cache.get_relation_target_data_source_id("english-finance", "Account") == "acc-db-2"


@pytest.mark.asyncio
async def test_resolve_finance_properties_with_overrides():
    schemas = {
        "finance-ds": {
            "properties": {
                "Name": {"type": "title"},
                "Amount": {"type": "number"},
            }
        }
    }
    client = FakeClient(schemas)
    cache = NotionSchemaCache(client)
    props = await cache.resolve_finance_properties("finance-ds", overrides={"amount": "CustomAmount", "extra": "Val"})
    assert props["title"] == "Name"
    assert props["amount"] == "CustomAmount"
    assert props["extra"] == "Val"


@pytest.mark.asyncio
async def test_resolve_task_properties_detection():
    schemas = {
        "tasks-ds": {
            "properties": {
                "任務名稱": {"type": "title"},
                "狀態": {"type": "status"},
                "截止日期": {"type": "date"},
                "重要度": {"type": "select"},
                "備註說明": {"type": "rich_text"},
                "專案": {
                    "type": "relation",
                    "relation": {"data_source_id": "proj-ds"},
                },
                "母任務": {"type": "relation"},
                "負責人": {"type": "people"},
            }
        }
    }
    client = FakeClient(schemas)
    cache = NotionSchemaCache(client)
    props = await cache.resolve_task_properties("tasks-ds")
    assert props["title"] == "任務名稱"
    assert props["status"] == "狀態"
    assert props["due"] == "截止日期"
    assert props["priority"] == "重要度"
    assert props["description"] == "備註說明"
    assert props["project"] == "專案"
    assert props["parent_task"] == "母任務"
    assert props["assignee"] == "負責人"
    assert await cache.get_relation_target_data_source_id("tasks-ds", "專案") == "proj-ds"


@pytest.mark.asyncio
async def test_get_property_options_and_summary():
    from babu.plugins.notion.mapping import extract_page_properties

    class FullFakeClient(FakeClient):
        async def query_data_source(self, data_source_id, body=None):
            if data_source_id == "cat-ds":
                return [
                    {"id": "c1", "properties": {"名稱": {"type": "title", "title": [{"plain_text": "餐飲"}]}}},
                    {"id": "c2", "properties": {"名稱": {"type": "title", "title": [{"plain_text": "娛樂"}]}}},
                ]
            return []

    schemas = {
        "finance-ds": {
            "properties": {
                "名稱": {"type": "title"},
                "金額": {"type": "number"},
                "收支": {"type": "select", "select": {"options": [{"name": "Income"}, {"name": "Expense"}]}},
                "類別": {"type": "relation", "relation": {"data_source_id": "cat-ds"}},
            }
        },
        "cat-ds": {
            "properties": {
                "名稱": {"type": "title"},
            }
        },
    }
    client = FullFakeClient(schemas)
    cache = NotionSchemaCache(client)

    # Test select options
    type_opts = await cache.get_property_options("finance-ds", "收支")
    assert type_opts["type"] == "select"
    assert type_opts["options"] == ["Income", "Expense"]

    # Test relation options
    rel_opts = await cache.get_property_options("finance-ds", "類別")
    assert rel_opts["type"] == "relation"
    assert rel_opts["options"] == ["餐飲", "娛樂"]

    # Test summary
    summary = await cache.get_database_summary("finance-ds")
    assert "properties" in summary
    assert summary["properties"]["收支"]["options"] == ["Income", "Expense"]

    # Test extract_page_properties
    sample_page = {
        "id": "page-1",
        "url": "https://notion.so/page-1",
        "properties": {
            "名稱": {"type": "title", "title": [{"plain_text": "午餐"}]},
            "金額": {"type": "number", "number": 120},
            "收支": {"type": "select", "select": {"name": "Expense"}},
            "類別": {"type": "relation", "relation": [{"id": "c1"}]},
        },
    }
    extracted = extract_page_properties(sample_page)
    assert extracted["id"] == "page-1"
    assert extracted["名稱"] == "午餐"
    assert extracted["金額"] == 120
    assert extracted["收支"] == "Expense"
    assert extracted["類別"] == ["c1"]
