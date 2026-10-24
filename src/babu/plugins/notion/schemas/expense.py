import datetime as dt
from decimal import Decimal

from pydantic import Field, field_validator

from babu.schemas.common import StrictModel

from .task import RelationRef


class AddExpenseArgs(StrictModel):
    title: str = Field(min_length=1, max_length=500, description="Transaction title or description")
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2, description="Transaction amount")
    date: dt.date = Field(description="Transaction date (YYYY-MM-DD)")
    category: RelationRef | None = Field(default=None, description="Category name (e.g. 餐飲, 娛樂) or RelationRef")
    account: RelationRef | None = Field(default=None, description="Account name (e.g. 現金, 信用卡) or RelationRef")

    @field_validator("title")
    @classmethod
    def title_must_have_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be empty")
        return value


class AddTransactionArgs(AddExpenseArgs):
    type: str = Field(default="Expense", pattern="^(Income|Expense)$")
