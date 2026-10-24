from datetime import date, datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from babu.schemas.common import StrictModel


class RelationRef(StrictModel):
    id: str | None = Field(default=None, min_length=1)
    name: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="before")
    @classmethod
    def coerce_reference(cls, value: object) -> object:
        if isinstance(value, str):
            cleaned = value.strip()
            if not cleaned:
                raise ValueError("relation reference must not be empty")
            return {"name": cleaned}
        if isinstance(value, dict):
            cleaned_dict = dict(value)
            if "name" in cleaned_dict and isinstance(cleaned_dict["name"], str):
                cleaned_name = cleaned_dict["name"].strip()
                cleaned_dict["name"] = cleaned_name or None
            if "id" in cleaned_dict and isinstance(cleaned_dict["id"], str):
                cleaned_id = cleaned_dict["id"].strip()
                cleaned_dict["id"] = cleaned_id or None
            return cleaned_dict
        return value

    @classmethod
    def from_name(cls, name: str) -> "RelationRef":
        return cls(name=name)

    @model_validator(mode="after")
    def exactly_one_reference(self) -> "RelationRef":
        if (self.id is None) == (self.name is None):
            raise ValueError("exactly one of id or name is required")
        return self


class CreateTaskArgs(StrictModel):
    title: str = Field(min_length=1, max_length=500)
    due_at: date | datetime | None = None
    due_end_at: date | datetime | None = None
    status: Literal["To Do", "Doing", "Done"] | None = None
    priority: Literal["Low", "Medium", "High", "low", "medium", "high"] | None = None
    project: RelationRef | None = None
    parent_task: RelationRef | None = None
    assignee_user_ids: list[str] = Field(default_factory=list, max_length=20)
    smart_list: Literal["Someday"] | None = None
    recur_interval: int | None = Field(default=None, ge=1)
    recur_unit: (
        Literal[
            "Day(s)",
            "Week(s)",
            "Month(s)",
            "Month(s) on the First Weekday",
            "Month(s) on the Last Weekday",
            "Month(s) on the Last Day",
            "Year(s)",
        ]
        | None
    ) = None
    recur_days: list[Literal["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]] = Field(
        default_factory=list
    )
    description: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional short summary shown in the Notion Description property. Omit it when the title is sufficient.",
    )
    body: str | None = Field(
        default=None,
        max_length=20000,
        description="Full task details in Markdown. This becomes the Notion page body.",
    )

    @field_validator("title")
    @classmethod
    def title_must_have_content(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be empty")
        return value

    @field_validator("description")
    @classmethod
    def description_must_have_content(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("description must not be empty")
        return value

    @model_validator(mode="after")
    def validate_dates_and_recurrence(self) -> "CreateTaskArgs":
        if self.due_end_at is not None and self.due_at is None:
            raise ValueError("due_at is required when due_end_at is set")
        if self.recur_interval is not None and self.due_at is None:
            raise ValueError("due_at is required for recurring tasks")
        if self.recur_days and (self.recur_interval != 1 or self.recur_unit not in {None, "Day(s)"}):
            raise ValueError("recur_days requires a one-day recurrence")
        return self
