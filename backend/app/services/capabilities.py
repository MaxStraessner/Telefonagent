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
        tool_name="finalize_appointment_booking",
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
            "name": "resolve_service",
            "description": "Löst eine genannte Leistung eindeutig gegen den aktiven Katalog auf.",
            "parameters": {
                "type": "object",
                "properties": {"service_name": {"type": "string"}},
                "required": ["service_name"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "resolve_booking_datetime",
            "description": (
                "Löst eine natürliche deutsche Datums- und Zeitangabe in der "
                "Unternehmenszeitzone auf. Keine Zeitzone und keinen ISO-Zeitpunkt erfinden."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "Natürliche deutsche Angabe, zum Beispiel morgen um 14 Uhr.",
                    }
                },
                "required": ["expression"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "check_appointment_availability",
            "description": "Prüft eine konkrete Startzeit serverseitig gegen lokale und externe Kalenderdaten.",
            "parameters": {
                "type": "object",
                "properties": {
                    "appointment_type_id": {"type": "string"},
                },
                "required": ["appointment_type_id"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "find_alternative_slots",
            "description": "Findet freie Alternativtermine serverseitig aus dem Sitzungssnapshot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "preferred_time_of_day": {"type": "string", "enum": ["morning", "afternoon", "evening"]},
                    "maximum_results": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["maximum_results"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "select_booking_slot",
            "description": "Wählt ausschließlich eine zuvor angebotene signierte Slot-ID aus.",
            "parameters": {
                "type": "object",
                "properties": {"slot_id": {"type": "string"}},
                "required": ["slot_id"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "prepare_appointment_confirmation",
            "description": (
                "Prüft den ausgewählten Slot erneut, speichert Kundendaten serverseitig "
                "und erzeugt die verbindliche Zusammenfassung mit Version und Digest."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "customer_name": {"type": "string"},
                    "customer_phone": {"type": ["string", "null"]},
                    "customer_email": {"type": ["string", "null"]},
                },
                "required": ["customer_name", "customer_phone", "customer_email"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "finalize_appointment_booking",
            "description": (
                "Bucht erst nach ausdrücklicher Kundenbestätigung. Nur success=true, status=confirmed und "
                "external_event_id bedeuten, dass der Termin erfolgreich eingetragen wurde."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "confirmation_version": {"type": "integer", "minimum": 1},
                },
                "required": ["confirmation_version"],
                "additionalProperties": False,
            },
        },
    ]
