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


CAPABILITY_REGISTRY: dict[str, CapabilityDefinition] = {
    "calendar_booking": CapabilityDefinition(
        key="calendar_booking",
        display_name="Kalender und Terminbuchung",
        description="Aktive Leistungen laden, echte Verfügbarkeit prüfen und bestätigte Termine verbindlich buchen.",
        tool_name="create_appointment",
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
            "name": "list_bookable_services",
            "description": "Lädt nur aktive Leistungen und deren aktive Terminarten. Keine Leistungen erfinden.",
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        {
            "type": "function",
            "name": "check_appointment_availability",
            "description": "Prüft eine konkrete Startzeit serverseitig gegen lokale und externe Kalenderdaten.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service_id": {"type": "string"},
                    "appointment_type_id": {"type": "string"},
                    "requested_start": {"type": "string", "description": "ISO-8601-Zeitpunkt mit Zeitzone."},
                    "timezone": {"type": "string"},
                },
                "required": ["service_id", "appointment_type_id", "requested_start", "timezone"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "create_appointment",
            "description": (
                "Bucht erst nach ausdrücklicher Kundenbestätigung. Nur success=true, status=confirmed und "
                "external_event_id bedeuten, dass der Termin erfolgreich eingetragen wurde."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "service_id": {"type": "string"},
                    "appointment_type_id": {"type": "string"},
                    "customer_name": {"type": "string"},
                    "customer_phone": {"type": ["string", "null"]},
                    "customer_email": {"type": ["string", "null"]},
                    "start_at": {"type": "string"},
                    "timezone": {"type": "string"},
                    "idempotency_key": {"type": "string"},
                    "confirmed": {"type": "boolean", "const": True},
                },
                "required": [
                    "service_id", "appointment_type_id", "customer_name", "customer_phone",
                    "customer_email", "start_at", "timezone", "idempotency_key", "confirmed",
                ],
                "additionalProperties": False,
            },
        },
    ]
