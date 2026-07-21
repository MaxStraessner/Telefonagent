from dataclasses import dataclass

from app.services.agent_configuration import AgentBundle


@dataclass(frozen=True)
class CapabilityDefinition:
    key: str
    display_name: str
    description: str
    tool_name: str
    risk_level: str
    requires_confirmation: bool


# Only capabilities with implemented, tenant-authorized backend handlers belong here.
CAPABILITY_REGISTRY: dict[str, CapabilityDefinition] = {
    "calendar_booking": CapabilityDefinition(
        key="calendar_booking",
        display_name="Kalender und Terminbuchung",
        description="Terminarten laden, freie Zeiten suchen und bestätigte Termine verbindlich buchen.",
        tool_name="create_calendar_booking",
        risk_level="write",
        requires_confirmation=True,
    )
}


def active_capabilities(bundle: AgentBundle) -> list[CapabilityDefinition]:
    enabled = {item.capability_key for item in bundle.capabilities if item.is_active}
    return [definition for key, definition in CAPABILITY_REGISTRY.items() if key in enabled]


def realtime_tools(bundle: AgentBundle) -> list[dict[str, object]]:
    if not any(item.key == "calendar_booking" for item in active_capabilities(bundle)):
        return []
    return [
        {
            "type": "function",
            "name": "list_appointment_types",
            "description": "Lädt ausschließlich die aktiven Terminarten dieses Unternehmensaccounts.",
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "find_available_appointments",
            "description": "Sucht serverseitig freie Termine. Niemals selbst Verfügbarkeiten berechnen.",
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_type_id": {"type": "string"},
                    "preferred_date": {"type": ["string", "null"], "description": "Datum im Format YYYY-MM-DD oder null."},
                    "preferred_time_of_day": {"type": ["string", "null"], "enum": ["morning", "afternoon", "evening", None]},
                    "search_days": {"type": "integer", "minimum": 1, "maximum": 30},
                },
                "required": ["appointment_type_id", "preferred_date", "preferred_time_of_day", "search_days"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "create_calendar_booking",
            "description": "Bucht einen zuvor angebotenen Termin erst nach ausdrücklicher Kundenbestätigung.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slot_id": {"type": "string"},
                    "appointment_type_id": {"type": "string"},
                    "customer_name": {"type": "string"},
                    "customer_phone": {"type": "string"},
                    "customer_email": {"type": ["string", "null"]},
                    "customer_notes": {"type": ["string", "null"]},
                    "idempotency_key": {"type": "string"},
                },
                "required": [
                    "slot_id", "appointment_type_id", "customer_name", "customer_phone",
                    "customer_email", "customer_notes", "idempotency_key",
                ],
                "additionalProperties": False,
            },
        },
    ]
