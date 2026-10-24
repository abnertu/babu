from datetime import date
from typing import Literal

from pydantic import Field

from babu.schemas.common import StrictModel


class ListTasksArgs(StrictModel):
    state: Literal["unfinished", "doing", "done"] = Field(
        default="unfinished",
        description="unfinished: 待辦/未完成, To Do and Doing (default); doing: 進行中; done: 已完成",
    )
    due_from: date | None = Field(default=None, description="Earliest due date, inclusive (YYYY-MM-DD)")
    due_to: date | None = Field(default=None, description="Latest due date, inclusive (YYYY-MM-DD)")
    limit: int = Field(default=20, ge=1, le=100)
    start_cursor: str | None = None


class QueryExpensesArgs(StrictModel):
    start_date: date | None = None
    end_date: date | None = None
    type: str | None = Field(default=None, pattern="^(Income|Expense)$")
    limit: int = Field(default=20, ge=1, le=100)
    start_cursor: str | None = None


class SearchNotionArgs(StrictModel):
    query: str = Field(min_length=1, max_length=500)
    database: str | None = Field(default=None, pattern="^(tasks|projects|transactions|categories|accounts)$")
    limit: int = Field(default=10, ge=1, le=25)


class GetDatabaseInfoArgs(StrictModel):
    database: str = Field(
        default="finance",
        description="Database name or alias to inspect ('finance', 'tasks', 'categories', 'projects', 'accounts')",
    )
    property: str | None = Field(
        default=None,
        description="Optional property name to get options for (e.g. 'Category', 'Status', 'Type', 'Account', 'Project')",
    )
