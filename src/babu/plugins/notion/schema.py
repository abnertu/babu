from collections.abc import Sequence
from typing import Any

from .client import NotionClient


class NotionSchemaCache:
    def __init__(self, client: NotionClient) -> None:
        self.client = client
        self._schemas: dict[str, dict[str, Any]] = {}
        self._relation_page_cache: dict[tuple[str, str], str] = {}

    async def get_schema(self, data_source_id: str) -> dict[str, Any]:
        if data_source_id not in self._schemas:
            self._schemas[data_source_id] = await self.client.retrieve_data_source(data_source_id)
        return self._schemas[data_source_id]

    async def get_title_property_name(self, data_source_id: str) -> str:
        schema = await self.get_schema(data_source_id)
        properties = schema.get("properties", {})
        for name, prop in properties.items():
            if prop.get("type") == "title":
                return str(name)
        return "title"

    async def get_relation_target_data_source_id(self, data_source_id: str, property_name: str) -> str | None:
        schema = await self.get_schema(data_source_id)
        properties = schema.get("properties", {})
        # Exact or case-insensitive match
        matched_prop = properties.get(property_name)
        if not matched_prop:
            for name, prop in properties.items():
                if name.casefold() == property_name.casefold():
                    matched_prop = prop
                    break
        if not matched_prop or matched_prop.get("type") != "relation":
            return None
        rel = matched_prop.get("relation", {})
        target = rel.get("data_source_id") or rel.get("database_id")
        return str(target) if target else None

    async def get_relation_options(self, target_data_source_id: str) -> list[dict[str, str]]:
        target_title_prop = await self.get_title_property_name(target_data_source_id)
        pages = await self.client.query_data_source(target_data_source_id, {"page_size": 100})
        options: list[dict[str, str]] = []
        for page in pages:
            page_id = str(page.get("id", ""))
            title_parts = page.get("properties", {}).get(target_title_prop, {}).get("title", [])
            name = "".join(item.get("plain_text", "") for item in title_parts).strip()
            self._relation_page_cache[(target_data_source_id, page_id)] = name or page_id
            options.append({"id": page_id, "name": name or page_id})
        return options

    async def resolve_relation_names(self, target_data_source_id: str, page_ids: Sequence[str]) -> list[str]:
        missing = [pid for pid in page_ids if (target_data_source_id, pid) not in self._relation_page_cache]
        if missing:
            await self.get_relation_options(target_data_source_id)
        return [self._relation_page_cache.get((target_data_source_id, pid), pid) for pid in page_ids]

    async def get_property_options(self, data_source_id: str, property_name: str) -> dict[str, Any]:
        schema = await self.get_schema(data_source_id)
        properties = schema.get("properties", {})
        matched_name = None
        for name in properties:
            if name.casefold() == property_name.casefold():
                matched_name = name
                break
        if not matched_name:
            return {"error": f"Property '{property_name}' not found in database"}

        prop = properties[matched_name]
        prop_type = prop.get("type")
        if prop_type in {"select", "status"}:
            opts = [opt.get("name") for opt in prop.get(prop_type, {}).get("options", []) if opt.get("name")]
            return {"property": matched_name, "type": prop_type, "options": opts}
        if prop_type == "multi_select":
            opts = [opt.get("name") for opt in prop.get("multi_select", {}).get("options", []) if opt.get("name")]
            return {"property": matched_name, "type": prop_type, "options": opts}
        if prop_type == "relation":
            target_ds = await self.get_relation_target_data_source_id(data_source_id, matched_name)
            if not target_ds:
                return {"property": matched_name, "type": "relation", "options": []}
            opts_data = await self.get_relation_options(target_ds)
            names = [item["name"] for item in opts_data]
            return {
                "property": matched_name,
                "type": "relation",
                "target_data_source_id": target_ds,
                "options": names,
            }
        return {"property": matched_name, "type": prop_type, "options": []}

    async def get_database_summary(self, data_source_id: str) -> dict[str, Any]:
        schema = await self.get_schema(data_source_id)
        properties = schema.get("properties", {})
        summary: dict[str, Any] = {}
        for name, prop in properties.items():
            p_type = prop.get("type")
            info: dict[str, Any] = {"type": p_type}
            if p_type in {"select", "status"}:
                info["options"] = [opt.get("name") for opt in prop.get(p_type, {}).get("options", []) if opt.get("name")]
            elif p_type == "multi_select":
                info["options"] = [opt.get("name") for opt in prop.get("multi_select", {}).get("options", []) if opt.get("name")]
            elif p_type == "relation":
                rel = prop.get("relation", {})
                target = rel.get("data_source_id") or rel.get("database_id")
                info["target_data_source_id"] = str(target) if target else None
            summary[name] = info
        return {"data_source_id": data_source_id, "properties": summary}

    async def resolve_finance_properties(self, data_source_id: str, overrides: dict[str, str] | None = None) -> dict[str, str]:
        schema = await self.get_schema(data_source_id)
        properties = schema.get("properties", {})
        detected: dict[str, str] = {}

        # 1. Title property: exactly one property has type 'title'
        for name, prop in properties.items():
            if prop.get("type") == "title":
                detected["title"] = name
                break

        def find_by_type_and_names(prop_types: Sequence[str], candidate_names: Sequence[str]) -> str | None:
            norm_candidates = [c.casefold() for c in candidate_names]
            for name, prop in properties.items():
                if prop.get("type") in prop_types and name.casefold() in norm_candidates:
                    return str(name)
            for name, prop in properties.items():
                if prop.get("type") in prop_types:
                    n_lower = name.casefold()
                    if any(c in n_lower or n_lower in c for c in norm_candidates):
                        return str(name)
            return None

        def find_single_of_type(prop_type: str) -> str | None:
            matches = [name for name, prop in properties.items() if prop.get("type") == prop_type]
            return str(matches[0]) if len(matches) == 1 else None

        # 2. Amount property
        amount_candidates = ["amount", "金額", "費用", "價格", "price", "cost", "total", "花費"]
        amount_prop = find_by_type_and_names(["number"], amount_candidates) or find_single_of_type("number")
        if amount_prop:
            detected["amount"] = amount_prop

        # 3. Date property
        date_candidates = ["date", "日期", "時間", "交易日期", "時間戳記"]
        date_prop = find_by_type_and_names(["date"], date_candidates) or find_single_of_type("date")
        if date_prop:
            detected["date"] = date_prop

        # 4. Type property (select)
        type_candidates = ["type", "類型", "種類", "收支"]
        type_prop = find_by_type_and_names(["select"], type_candidates)
        if type_prop:
            detected["type"] = type_prop

        # 5. Category property (relation or select)
        cat_candidates = ["category", "類別", "分類", "項目分類"]
        cat_prop = find_by_type_and_names(["relation"], cat_candidates) or find_by_type_and_names(["select"], cat_candidates)
        if cat_prop:
            detected["category"] = cat_prop

        # 6. Account property (relation or select)
        acc_candidates = ["account", "帳戶", "帳號", "資產", "支付方式", "付款帳戶"]
        acc_prop = find_by_type_and_names(["relation"], acc_candidates) or find_by_type_and_names(["select"], acc_candidates)
        if acc_prop:
            detected["account"] = acc_prop

        if overrides:
            detected.update(overrides)

        return detected

    async def resolve_task_properties(self, data_source_id: str, overrides: dict[str, str] | None = None) -> dict[str, str]:
        schema = await self.get_schema(data_source_id)
        properties = schema.get("properties", {})
        detected: dict[str, str] = {}

        # 1. Title property
        for name, prop in properties.items():
            if prop.get("type") == "title":
                detected["title"] = name
                break

        def find_by_type_and_names(prop_types: Sequence[str], candidate_names: Sequence[str]) -> str | None:
            norm_candidates = [c.casefold() for c in candidate_names]
            for name, prop in properties.items():
                if prop.get("type") in prop_types and name.casefold() in norm_candidates:
                    return str(name)
            for name, prop in properties.items():
                if prop.get("type") in prop_types:
                    n_lower = name.casefold()
                    if any(c in n_lower or n_lower in c for c in norm_candidates):
                        return str(name)
            return None

        # Status
        status_prop = find_by_type_and_names(["status", "select"], ["status", "狀態", "進度"])
        if status_prop:
            detected["status"] = status_prop

        # Due date
        due_prop = find_by_type_and_names(["date"], ["due", "due date", "截止", "到期", "日期", "執行日期"])
        if not due_prop:
            matches = [name for name, prop in properties.items() if prop.get("type") == "date"]
            if len(matches) == 1:
                due_prop = str(matches[0])
        if due_prop:
            detected["due"] = due_prop

        # Priority
        priority_prop = find_by_type_and_names(["select", "status"], ["priority", "優先級", "優先度", "重要度"])
        if priority_prop:
            detected["priority"] = priority_prop

        # Description
        desc_prop = find_by_type_and_names(["rich_text"], ["description", "說明", "備註", "描述", "note", "notes"])
        if desc_prop:
            detected["description"] = desc_prop

        # Project
        proj_prop = find_by_type_and_names(["relation"], ["project", "專案", "項目"])
        if proj_prop:
            detected["project"] = proj_prop

        # Parent task
        parent_prop = find_by_type_and_names(["relation"], ["parent task", "parent", "母任務", "父任務", "上層任務"])
        if parent_prop:
            detected["parent_task"] = parent_prop

        # Assignee
        assignee_prop = find_by_type_and_names(["people"], ["assignee", "指派", "負責人", "人員"])
        if assignee_prop:
            detected["assignee"] = assignee_prop

        # Smart list
        smart_prop = find_by_type_and_names(["select"], ["smart list", "清單"])
        if smart_prop:
            detected["smart_list"] = smart_prop

        # Recur interval
        recur_prop = find_by_type_and_names(["number"], ["recur interval", "週期", "間隔"])
        if recur_prop:
            detected["recur_interval"] = recur_prop

        # Recur unit
        unit_prop = find_by_type_and_names(["select"], ["recur unit", "週期單位", "單位"])
        if unit_prop:
            detected["recur_unit"] = unit_prop

        # Recur days
        days_prop = find_by_type_and_names(["multi_select"], ["days (only if set to 1 day(s))", "recur days", "days", "星期"])
        if days_prop:
            detected["recur_days"] = days_prop

        if overrides:
            detected.update(overrides)

        return detected
