from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.api.dependencies import TenantContext
from app.core.config import Settings
from app.services.agent_configuration import AgentBundle, is_open_now, load_agent_bundle
from app.services.capabilities import active_capabilities, realtime_tools
from app.services.prompt_compiler import SECTION_NAMES, compile_agent_prompt

SERVER_VAD_SILENCE_BY_EAGERNESS = {"low": 900, "medium": 600, "high": 350}


@dataclass(frozen=True)
class AgentRuntimeConfig:
    bundle: AgentBundle
    prompt: str
    greeting: str
    model: str
    voice: str
    speed: float
    turn_detection: dict[str, object]
    tools: list[dict[str, object]]
    tool_choice: str
    capability_keys: list[str]
    prompt_sections: list[str]
    business_hours_status: str

    @property
    def version(self) -> int:
        return self.bundle.configuration.version


def build_runtime_config(
    db: Session, context: TenantContext, settings: Settings, *, test_mode: bool,
) -> AgentRuntimeConfig:
    bundle = load_agent_bundle(db, context.id)
    config = bundle.configuration
    business_hours_status = "open" if is_open_now(bundle, context.tenant.timezone) else "closed"
    if test_mode:
        greeting = config.test_greeting
    else:
        greeting = config.standard_greeting if is_open_now(bundle, context.tenant.timezone) else config.outside_hours_greeting
    if config.turn_detection_type.value == "semantic_vad":
        turn_detection: dict[str, object] = {
            "type": "semantic_vad", "eagerness": config.turn_eagerness.value,
            "create_response": True, "interrupt_response": False,
        }
    else:
        turn_detection = {
            "type": "server_vad", "threshold": config.vad_threshold,
            "prefix_padding_ms": config.prefix_padding_ms,
            "silence_duration_ms": SERVER_VAD_SILENCE_BY_EAGERNESS[config.turn_eagerness.value],
            "create_response": True, "interrupt_response": False,
        }
        if config.idle_prompt_enabled:
            turn_detection["idle_timeout_ms"] = config.idle_timeout_ms
    capabilities = active_capabilities(bundle)
    tools = realtime_tools(bundle)
    return AgentRuntimeConfig(
        bundle=bundle, prompt=compile_agent_prompt(bundle, greeting), greeting=greeting,
        model=settings.openai_realtime_model, voice=config.voice, speed=config.speech_speed,
        turn_detection=turn_detection, tools=tools,
        tool_choice="auto" if tools else "none", capability_keys=[item.key for item in capabilities],
        prompt_sections=SECTION_NAMES, business_hours_status=business_hours_status,
    )
