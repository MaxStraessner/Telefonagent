import pytest

from app.services.tool_projections import (
    ToolProjectionError,
    acknowledged_wire_tools,
    outbound_wire_tools,
    validate_canonical_tools,
)


def tool(parameters, **overrides):
    value = {
        "type": "function",
        "name": "read_only_test",
        "description": "Read-only test tool.",
        "parameters": parameters,
        "strict": True,
    }
    value.update(overrides)
    return value


def test_strict_is_required_in_the_canonical_contract():
    with pytest.raises(ToolProjectionError, match="strict=true"):
        validate_canonical_tools([tool({"type": "object", "properties": {}, "required": [], "additionalProperties": False}, strict=False)])
    missing = tool({"type": "object", "properties": {}, "required": [], "additionalProperties": False})
    missing.pop("strict")
    with pytest.raises(ToolProjectionError, match="strict"):
        validate_canonical_tools([missing])


def test_wire_projection_removes_strict_and_applies_known_sdk_rules():
    parameters = {
        "type": "object",
        "properties": {
            "required_nullable": {"type": ["string", "null"]},
            "optional_value": {"type": "string", "default": None},
            "nested": {
                "type": "object",
                "properties": {"value": {"type": "number", "minimum": 1}},
                "required": ["value"],
                "additionalProperties": False,
            },
            "values": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["required_nullable", "nested", "values"],
        "additionalProperties": False,
    }
    projected = outbound_wire_tools([tool(parameters, deferLoading=True)])
    assert set(projected[0]) == {"type", "name", "description", "parameters"}
    schema = projected[0]["parameters"]
    assert schema["required"] == ["required_nullable", "optional_value", "nested", "values"]
    assert schema["properties"]["required_nullable"] == {"type": ["string", "null"]}
    assert schema["properties"]["optional_value"] == {
        "anyOf": [{"type": "string"}, {"type": "null"}],
    }
    assert schema["properties"]["nested"]["additionalProperties"] is False
    assert schema["properties"]["values"]["items"] == {"type": "string"}


def test_required_nullable_is_not_treated_as_optional():
    parameters = {
        "type": "object",
        "properties": {"value": {"type": ["string", "null"]}},
        "required": ["value"],
        "additionalProperties": False,
    }
    projected = outbound_wire_tools([tool(parameters)])[0]["parameters"]
    assert projected["properties"]["value"] == {"type": ["string", "null"]}


def test_acknowledged_projection_accepts_only_wire_fields_and_drops_strict():
    acknowledged = acknowledged_wire_tools([
        {
            "type": "function",
            "name": "read_only_test",
            "description": "Read-only test tool.",
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
            "strict": True,
        },
    ])
    assert set(acknowledged[0]) == {"type", "name", "description", "parameters"}
    with pytest.raises(ToolProjectionError, match="Unbekannte Provider-Toolfelder"):
        acknowledged_wire_tools([{**acknowledged[0], "unexpected": True}])

