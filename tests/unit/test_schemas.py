from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from babu.plugins.notion.schemas.expense import AddExpenseArgs
from babu.plugins.notion.schemas.task import CreateTaskArgs


def test_task_schema_strips_title():
    args = CreateTaskArgs(title="  整理房間 ", description="  清理房間 ")
    assert args.title == "整理房間"
    assert args.description == "清理房間"


def test_expense_requires_positive_amount():
    with pytest.raises(ValidationError):
        AddExpenseArgs(title="午餐", amount=0, date=date.today())


def test_expense_accepts_decimal():
    args = AddExpenseArgs(title="午餐", amount="120", date=date.today())
    assert args.amount == Decimal("120")


def test_relation_ref_coerces_from_string():
    args = AddExpenseArgs(
        title="晚餐",
        amount=150,
        date=date.today(),
        category="餐飲",
        account=" 現金 ",
    )
    assert args.category is not None
    assert args.category.name == "餐飲"
    assert args.category.id is None
    assert args.account is not None
    assert args.account.name == "現金"
    assert args.account.id is None


def test_relation_ref_rejects_empty_string():
    with pytest.raises(ValidationError):
        AddExpenseArgs(title="晚餐", amount=150, date=date.today(), category="   ")
