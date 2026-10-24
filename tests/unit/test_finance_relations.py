from datetime import date

import pytest

from babu.plugins.notion.finance import FinanceService
from babu.plugins.notion.relations import ResolvedRelation
from babu.plugins.notion.schemas.expense import AddExpenseArgs


class FakeNotion:
    properties = None

    async def create_page(self, data_source_id, properties, children=None):
        self.properties = properties
        return {"id": "transaction-id"}


class FakeResolver:
    async def resolve(self, data_source_id, title_property, reference):
        return ResolvedRelation(f"{title_property}-id", reference.name or "")


@pytest.mark.asyncio
async def test_expense_writes_category_and_account_relations():
    notion = FakeNotion()
    service = FinanceService(
        notion,
        "transactions",
        {"title": "Item Name", "amount": "Amount", "date": "Date", "type": "Type", "category": "Category", "account": "Account"},
        FakeResolver(),
        "categories",
        "accounts",
    )
    result = await service.add(
        AddExpenseArgs(title="早餐", amount="120", date=date(2026, 9, 15), category={"name": "餐飲"}, account={"name": "現金"})
    )
    assert result.success is True
    assert notion.properties["Category"] == {"relation": [{"id": "Category Name-id"}]}
    assert notion.properties["Account"] == {"relation": [{"id": "Account Name-id"}]}


@pytest.mark.asyncio
async def test_expense_auto_detects_schema_and_accepts_string_relations():
    from babu.plugins.notion.schema import NotionSchemaCache

    class SchemaNotion(FakeNotion):
        async def retrieve_data_source(self, data_source_id):
            if data_source_id == "transactions":
                return {
                    "properties": {
                        "項目": {"type": "title"},
                        "花費": {"type": "number"},
                        "日期": {"type": "date"},
                        "類別": {
                            "type": "relation",
                            "relation": {"data_source_id": "categories-auto"},
                        },
                    }
                }
            if data_source_id == "categories-auto":
                return {"properties": {"分類名稱": {"type": "title"}}}
            return {"properties": {}}

    notion = SchemaNotion()
    cache = NotionSchemaCache(notion)
    service = FinanceService(
        notion,
        "transactions",
        properties={},
        resolver=FakeResolver(),
        schema_cache=cache,
    )
    result = await service.add(AddExpenseArgs(title="午餐", amount="85", date=date(2026, 9, 20), category="餐飲"))
    assert result.success is True
    assert notion.properties["項目"] == {"title": [{"text": {"content": "午餐"}}]}
    assert notion.properties["花費"] == {"number": 85.0}
    assert notion.properties["類別"] == {"relation": [{"id": "分類名稱-id"}]}
