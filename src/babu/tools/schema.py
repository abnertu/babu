"""Build canonical argument shapes that simple tool templates can display."""

import json
from typing import Any, cast


def tool_parameters(schema: dict[str, Any]) -> dict[str, Any]:
    retained_reference = False

    def merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
        result = base | extra
        first, second = base.get("description"), extra.get("description")
        if first and second and first != second:
            result["description"] = f"{second} {first}"
        return result

    def visit(value: Any, active: frozenset[str] = frozenset()) -> Any:
        nonlocal retained_reference
        if isinstance(value, list):
            return [visit(item, active) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            reference = value["$ref"]
            if not isinstance(reference, str) or not reference.startswith("#/") or reference in active:
                retained_reference = True
                return dict(value)
            target: Any = schema
            for component in reference[2:].split("/"):
                target = target[component.replace("~1", "/").replace("~0", "~")]
            expanded = cast(dict[str, Any], visit(target, active | {reference}))
            return merge(expanded, visit({key: item for key, item in value.items() if key != "$ref"}, active))
        result = {key: visit(item, active) for key, item in value.items() if key != "$defs"}
        alternatives = result.get("anyOf", [])
        if alternatives and result.get("type"):
            matching = [branch for branch in alternatives if branch.get("type") == result["type"]]
            if len(matching) == 1:
                result = merge(matching[0], {key: item for key, item in result.items() if key != "anyOf"})
        if "const" in result:
            result["enum"] = [result["const"]]
        constraints = {
            key: result[key]
            for key in (
                "minimum",
                "maximum",
                "exclusiveMinimum",
                "exclusiveMaximum",
                "minLength",
                "maxLength",
                "minItems",
                "maxItems",
                "multipleOf",
                "pattern",
                "format",
            )
            if key in result
        }
        if constraints:
            note = "Constraints: " + json.dumps(constraints, ensure_ascii=False)
            description = str(result.get("description", ""))
            if note not in description:
                result["description"] = (description + " " + note).strip()
        properties = result.get("properties", {})
        required = result.get("required", [])
        for name, field in properties.items():
            alternatives = field.get("anyOf")
            if alternatives and name not in required and "default" in field and field["default"] is None:
                nonnull = [branch for branch in alternatives if branch.get("type") != "null"]
                if nonnull and len(nonnull) != len(alternatives):
                    outer = {key: item for key, item in field.items() if key not in {"anyOf", "default"}}
                    properties[name] = field = merge(nonnull[0], outer) if len(nonnull) == 1 else outer | {"anyOf": nonnull}
            alternatives = field.get("anyOf", [])
            if alternatives and all(branch.get("type") == alternatives[0].get("type") for branch in alternatives):
                if alternatives[0].get("type") is not None:
                    field["type"] = alternatives[0]["type"]
        return result

    result = cast(dict[str, Any], visit(schema))
    if retained_reference and "$defs" in schema:
        result["$defs"] = schema["$defs"]
    return result
