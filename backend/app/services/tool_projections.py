"""Deterministic Realtime tool projections for the installed Agents SDK.

The canonical contract is project-owned and includes ``strict``.  The Realtime
wire projection intentionally excludes SDK-only fields and applies only the
strict JSON-schema transformation implemented by Agents 0.13.5.
"""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from typing import Any

WIRE_TOOL_KEYS = ("type", "name", "description", "parameters")
CANONICAL_TOOL_KEYS = (
    *WIRE_TOOL_KEYS,
    "strict",
    "deferLoading",
    "providerData",
    "needsApproval",
    "timeoutMs",
    "timeoutBehavior",
    "inputGuardrails",
    "outputGuardrails",
)
REQUIRED_CANONICAL_TOOL_KEYS = (*WIRE_TOOL_KEYS, "strict")


class ToolProjectionError(ValueError):
    """Raised when a tool cannot be projected without losing semantics."""


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _is_mapping(value: object) -> bool:
    return isinstance(value, Mapping)


def _schema_allows_null(schema: object) -> bool:
    if not _is_mapping(schema):
        return False
    schema_map = schema
    if schema_map.get("type") == "null":
        return True
    schema_type = schema_map.get("type")
    if isinstance(schema_type, list) and "null" in schema_type:
        return True
    for key in ("anyOf", "oneOf", "allOf"):
        entries = schema_map.get(key)
        if isinstance(entries, list) and any(_schema_allows_null(item) for item in entries):
            return True
    return False


def _wrap_nullable(schema: object) -> object:
    if not _is_mapping(schema) or _schema_allows_null(schema):
        return schema
    wrapped: dict[str, Any] = {}
    if isinstance(schema.get("description"), str):
        wrapped["description"] = schema["description"]
    wrapped["anyOf"] = [schema, {"type": "null"}]
    return wrapped


def strict_schema_projection(schema: object) -> object:
    """Mirror Agents 0.13.5 ``toOpenAIStrictToolSchema`` exactly."""

    if not _is_mapping(schema):
        if isinstance(schema, list):
            return [strict_schema_projection(item) for item in schema]
        return schema

    record: dict[str, Any] = copy.deepcopy(dict(schema))
    if record.get("type") == "object" and isinstance(record.get("properties"), Mapping):
        properties = dict(record["properties"])
        original_required = {
            str(item) for item in record.get("required", []) if isinstance(item, (str, int, float))
        }
        normalized_properties: dict[str, Any] = {}
        for key, value in properties.items():
            normalized = strict_schema_projection(value)
            normalized_properties[str(key)] = normalized if str(key) in original_required else _wrap_nullable(normalized)
        record["properties"] = normalized_properties
        record["required"] = list(normalized_properties)
        record["additionalProperties"] = False

    for key in ("$defs", "definitions"):
        nested = record.get(key)
        if isinstance(nested, Mapping):
            record[key] = {str(name): strict_schema_projection(value) for name, value in nested.items()}

    for key in ("anyOf", "allOf", "oneOf"):
        nested = record.get(key)
        if isinstance(nested, list):
            record[key] = [strict_schema_projection(value) for value in nested]

    items = record.get("items")
    if isinstance(items, list):
        record["items"] = [strict_schema_projection(value) for value in items]
    elif _is_mapping(items):
        record["items"] = strict_schema_projection(items)

    if record.get("default") is None:
        record.pop("default", None)
    return record


def validate_canonical_tools(tools: object) -> list[dict[str, Any]]:
    if not isinstance(tools, list):
        raise ToolProjectionError("Der kanonische Toolvertrag ist keine Liste.")
    validated: list[dict[str, Any]] = []
    for index, tool in enumerate(tools):
        if not isinstance(tool, Mapping):
            raise ToolProjectionError(f"Tool {index} ist kein Objekt.")
        unknown = set(tool) - set(CANONICAL_TOOL_KEYS)
        if unknown:
            raise ToolProjectionError(
                f"Unbekannte kanonische Toolfelder: {', '.join(sorted(str(item) for item in unknown))}."
            )
        missing = [key for key in REQUIRED_CANONICAL_TOOL_KEYS if key not in tool]
        if missing:
            raise ToolProjectionError(f"Tool {index} fehlen kanonische Felder: {', '.join(missing)}.")
        if tool["type"] != "function" or not isinstance(tool["name"], str) or not tool["name"]:
            raise ToolProjectionError(f"Tool {index} ist kein gültiges Funktionstool.")
        if tool["strict"] is not True:
            raise ToolProjectionError(f"Tool {tool['name']} muss strict=true verwenden.")
        if not isinstance(tool["description"], str) or not isinstance(tool["parameters"], Mapping):
            raise ToolProjectionError(f"Tool {tool['name']} enthält kein gültiges Schema.")
        validated.append({str(key): copy.deepcopy(value) for key, value in tool.items()})
    return validated


def canonical_tools_digest(tools: object) -> str:
    return _digest(validate_canonical_tools(tools))


def outbound_wire_tools(tools: object) -> list[dict[str, Any]]:
    canonical = validate_canonical_tools(tools)
    return [
        {
            "type": tool["type"],
            "name": tool["name"],
            "description": tool["description"],
            "parameters": strict_schema_projection(tool["parameters"]),
        }
        for tool in canonical
    ]


def outbound_wire_tools_digest(tools: object) -> str:
    return _digest(outbound_wire_tools(tools))


def acknowledged_wire_tools(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ToolProjectionError("session.updated enthält keine Toolliste.")
    result: list[dict[str, Any]] = []
    for index, tool in enumerate(value):
        if not isinstance(tool, Mapping):
            raise ToolProjectionError(f"Bestätigtes Tool {index} ist kein Objekt.")
        allowed = set(WIRE_TOOL_KEYS) | {"strict"}
        unknown = set(tool) - allowed
        if unknown:
            raise ToolProjectionError(
                f"Unbekannte Provider-Toolfelder: {', '.join(sorted(str(item) for item in unknown))}."
            )
        if tool.get("type") != "function" or not isinstance(tool.get("name"), str):
            raise ToolProjectionError(f"Bestätigtes Tool {index} ist kein gültiges Funktionstool.")
        if not isinstance(tool.get("description"), str) or not isinstance(tool.get("parameters"), Mapping):
            raise ToolProjectionError(f"Bestätigtes Tool {tool['name']} enthält kein gültiges Wire-Schema.")
        result.append({key: copy.deepcopy(tool[key]) for key in WIRE_TOOL_KEYS})
    return result


def acknowledged_tools_digest(value: object) -> str:
    return _digest(acknowledged_wire_tools(value))
