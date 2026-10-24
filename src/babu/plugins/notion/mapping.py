import re
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Any


def title_property(name: str, value: str) -> dict[str, object]:
    return {name: {"title": [{"text": {"content": value}}]}}


def rich_text_property(name: str, value: str) -> dict[str, object]:
    return {name: {"rich_text": [{"text": {"content": value}}]}}


def select_property(name: str, value: str) -> dict[str, object]:
    return {name: {"select": {"name": value}}}


def status_property(name: str, value: str) -> dict[str, object]:
    return {name: {"status": {"name": value}}}


def date_property(name: str, value: date | datetime) -> dict[str, object]:
    return {name: {"date": {"start": value.isoformat()}}}


def date_range_property(name: str, start: date | datetime, end: date | datetime | None = None) -> dict[str, object]:
    value = {"start": start.isoformat()}
    if end is not None:
        value["end"] = end.isoformat()
    return {name: {"date": value}}


def number_property(name: str, value: Decimal) -> dict[str, object]:
    return {name: {"number": float(value)}}


def relation_property(name: str, page_ids: list[str]) -> dict[str, object]:
    return {name: {"relation": [{"id": page_id} for page_id in page_ids]}}


def people_property(name: str, user_ids: list[str]) -> dict[str, object]:
    return {name: {"people": [{"object": "user", "id": user_id} for user_id in user_ids]}}


def multi_select_property(name: str, values: Sequence[str]) -> dict[str, object]:
    return {name: {"multi_select": [{"name": value} for value in values]}}


def merge(*properties: dict[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for property_group in properties:
        result.update(property_group)
    return result


def extract_property_value(prop: dict[str, Any]) -> Any:
    prop_type = prop.get("type")
    if prop_type in {"title", "rich_text"}:
        items = prop.get(prop_type, [])
        return "".join(item.get("plain_text", "") for item in items).strip()
    if prop_type == "number":
        return prop.get("number")
    if prop_type == "select":
        sel = prop.get("select")
        return sel.get("name") if sel else None
    if prop_type == "status":
        st = prop.get("status")
        return st.get("name") if st else None
    if prop_type == "date":
        d = prop.get("date")
        if not d:
            return None
        start = d.get("start")
        end = d.get("end")
        if end:
            return f"{start} to {end}"
        return start
    if prop_type == "multi_select":
        return [item.get("name") for item in prop.get("multi_select", []) if item.get("name")]
    if prop_type == "relation":
        return [item.get("id") for item in prop.get("relation", []) if item.get("id")]
    if prop_type == "formula":
        formula = prop.get("formula", {})
        f_type = formula.get("type")
        return formula.get(f_type) if f_type else None
    if prop_type == "checkbox":
        return prop.get("checkbox")
    if prop_type == "people":
        return [p.get("name") or p.get("id") for p in prop.get("people", [])]
    if prop_type in {"url", "email", "phone_number"}:
        return prop.get(prop_type)
    if prop_type == "rollup":
        rollup = prop.get("rollup", {})
        r_type = rollup.get("type")
        if r_type in {"number", "date"}:
            return rollup.get(r_type)
        if r_type == "array":
            return [extract_property_value(item) for item in rollup.get("array", [])]
        return None
    return None


def extract_page_properties(page: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": page.get("id", ""),
        "url": page.get("url", ""),
    }
    raw_props = page.get("properties", {})
    for name, prop in raw_props.items():
        val = extract_property_value(prop)
        if val is not None:
            result[name] = val
    return result


def paragraph_blocks(content: str) -> list[dict[str, object]]:
    """Convert the supported Markdown subset into native Notion blocks."""
    blocks: list[dict[str, object]] = []
    lines = content.splitlines()
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        index += 1
        if not line:
            continue

        if line.startswith("```"):
            language = line[3:].strip() or "plain text"
            code_lines: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                code_lines.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            blocks.append(
                {
                    "object": "block",
                    "type": "code",
                    "code": {"rich_text": _rich_text("\n".join(code_lines)), "language": language},
                }
            )
            continue

        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            level = len(heading.group(1))
            block_type = f"heading_{level}"
            blocks.append({"object": "block", "type": block_type, block_type: {"rich_text": _rich_text(heading.group(2))}})
            continue

        checkbox = re.match(r"^-\s+\[([ xX])\]\s+(.+)$", line)
        if checkbox:
            blocks.append(
                {
                    "object": "block",
                    "type": "to_do",
                    "to_do": {"rich_text": _rich_text(checkbox.group(2)), "checked": checkbox.group(1).lower() == "x"},
                }
            )
            continue

        bullet = re.match(r"^[-*+]\s+(.+)$", line)
        if bullet:
            blocks.append(
                {
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": _rich_text(bullet.group(1))},
                }
            )
            continue

        numbered = re.match(r"^\d+[.)]\s+(.+)$", line)
        if numbered:
            blocks.append(
                {
                    "object": "block",
                    "type": "numbered_list_item",
                    "numbered_list_item": {"rich_text": _rich_text(numbered.group(1))},
                }
            )
            continue

        if line.startswith("> "):
            blocks.append({"object": "block", "type": "quote", "quote": {"rich_text": _rich_text(line[2:])}})
            continue

        blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": _rich_text(line)}})
    return blocks


_INLINE_MARKDOWN = re.compile(r"(\[([^]]+)\]\(([^)]+)\)|\*\*([^*]+)\*\*|__([^_]+)__|`([^`]+)`|\*([^*]+)\*|_([^_]+)_)")


def _rich_text(value: str) -> list[dict[str, object]]:
    """Convert inline Markdown into Notion rich-text annotations."""
    result: list[dict[str, object]] = []
    position = 0
    for match in _INLINE_MARKDOWN.finditer(value):
        if match.start() > position:
            result.append({"type": "text", "text": {"content": value[position : match.start()]}})
        link_text, link_url, bold, bold_alt, code, italic = match.group(2, 3, 4, 5, 6, 7)
        text = link_text or bold or bold_alt or code or italic or ""
        item: dict[str, object] = {"type": "text", "text": {"content": text}}
        annotations: dict[str, object] = {}
        if bold or bold_alt:
            annotations["bold"] = True
        if italic:
            annotations["italic"] = True
        if code:
            annotations["code"] = True
        if link_url:
            item["text"] = {"content": text, "link": {"url": link_url}}
        if annotations:
            item["annotations"] = annotations
        result.append(item)
        position = match.end()
    if position < len(value):
        result.append({"type": "text", "text": {"content": value[position:]}})
    return result or [{"type": "text", "text": {"content": ""}}]
