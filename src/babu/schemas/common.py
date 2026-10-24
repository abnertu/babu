from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class ToolResult(StrictModel):
    success: bool
    data: dict[str, object] | None = None
    error_code: str | None = None
    error_message: str | None = None
    requires_input: bool = False
    outcome_unknown: bool = False

    @classmethod
    def ok(cls, data: dict[str, object] | None = None) -> "ToolResult":
        return cls(success=True, data=data or {})

    @classmethod
    def fail(cls, code: str, message: str, *, requires_input: bool = False, outcome_unknown: bool = False) -> "ToolResult":
        return cls(
            success=False, error_code=code, error_message=message, requires_input=requires_input, outcome_unknown=outcome_unknown
        )
