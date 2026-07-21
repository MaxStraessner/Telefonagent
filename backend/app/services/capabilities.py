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


# Only capabilities with an implemented and server-authorized tool handler belong here.
# Calendar, callback and transfer capabilities intentionally remain absent.
CAPABILITY_REGISTRY: dict[str, CapabilityDefinition] = {}


def active_capabilities(bundle: AgentBundle) -> list[CapabilityDefinition]:
    enabled = {item.capability_key for item in bundle.capabilities if item.is_active}
    return [definition for key, definition in CAPABILITY_REGISTRY.items() if key in enabled]


def realtime_tools(bundle: AgentBundle) -> list[dict[str, object]]:
    # Tool schemas are added together with their server-side executor and authorization.
    return [] if not active_capabilities(bundle) else []
