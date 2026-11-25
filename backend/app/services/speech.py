import hashlib
import json
import logging
import re
import sys
from array import array

import httpx
from fastapi import HTTPException

from app.api.dependencies import TenantContext
from app.core.config import Settings
from app.models import AgentConfiguration
from app.schemas.agent_config import AgentConfigurationUpdate
from app.services.prompt_compiler import compile_pronunciation_instruction
from app.services.realtime import build_safety_identifier

logger = logging.getLogger(__name__)
SPEECH_URL = "https://api.openai.com/v1/audio/speech"
TELEPHONE_SAMPLE_RATE = 8_000


async def render_speech(settings: Settings, context: TenantContext, request_payload: dict) -> bytes:
    if not settings.openai_api_key:
        raise HTTPException(503, detail={"code": "voice_preview_not_configured", "message": "Die Sprachausgabe ist serverseitig noch nicht konfiguriert."})
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=4.0)) as client:
            response = await client.post(
                SPEECH_URL,
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "OpenAI-Safety-Identifier": build_safety_identifier(context, settings),
                },
                json=request_payload,
            )
    except httpx.TimeoutException as exc:
        raise HTTPException(504, detail={"code": "voice_preview_timeout", "message": "Die Sprachausgabe antwortet nicht rechtzeitig."}) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(502, detail={"code": "voice_preview_unavailable", "message": "Die Sprachausgabe ist derzeit nicht erreichbar."}) from exc
    if not response.is_success or not response.content:
        logger.warning("OpenAI speech rejected", extra={"provider_status": response.status_code})
        raise HTTPException(502, detail={"code": "voice_preview_rejected", "message": "Die gewählte Stimme konnte nicht wiedergegeben werden."})
    return response.content


async def prepare_greeting(
    settings: Settings, context: TenantContext, config: AgentConfiguration | AgentConfigurationUpdate,
    stored: AgentConfiguration,
) -> tuple[bytes | None, str | None]:
    if config.operating_mode != "answering_machine" or not config.integrated_greeting:
        return None, None
    if not settings.openai_realtime_transcription_enabled:
        raise HTTPException(409, detail={"code": "transcription_disabled", "message": "Die schriftliche Nachrichtenaufnahme ist deaktiviert."})
    greeting = config.message_greeting.replace("{Name}", config.company_name)
    if not (re.search(r"\b(?:KI|künstliche Intelligenz)\b", config.message_greeting, re.IGNORECASE)
            and re.search(r"\b(?:schriftlich|als Text)\b", config.message_greeting, re.IGNORECASE)):
        raise HTTPException(422, detail={
            "code": "greeting_disclosure_missing",
            "message": "Erwähnen Sie in der Begrüßung die KI und die schriftliche Nachrichtenaufnahme.",
        })
    payload = {
        "model": "gpt-4o-mini-tts", "voice": config.voice, "speed": config.speech_speed,
        "input": greeting, "response_format": "pcm",
        "instructions": compile_pronunciation_instruction(
            config.pronunciation_style, config.regional_accent, config.pronunciation_instructions,
        ) + " Lies den vorgegebenen Text wortgetreu auf Deutsch, ohne etwas hinzuzufügen.",
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    if stored.greeting_audio_digest == digest and stored.greeting_audio:
        return stored.greeting_audio, digest
    pcm = await render_speech(settings, context, payload)
    if len(pcm) % 2 or not 6 <= len(pcm) <= 90 * 24_000 * 2:
        raise HTTPException(502, detail={"code": "invalid_greeting_audio", "message": "Die Begrüßung konnte nicht als Telefon-Audio erstellt werden."})
    return pcm24k_to_mulaw8k(pcm), digest


def pcm24k_to_mulaw8k(pcm: bytes) -> bytes:
    samples = array("h")
    samples.frombytes(pcm)
    if sys.byteorder == "big":
        samples.byteswap()
    # OpenAI returns 24 kHz signed PCM. Twilio requires raw 8 kHz G.711 μ-law.
    encoded = bytearray()
    for index in range(0, len(samples) - 2, 3):
        sample = (samples[index] + samples[index + 1] + samples[index + 2]) // 3
        magnitude = min(abs(sample), 32635) + 132
        exponent = magnitude.bit_length() - 8
        mantissa = (magnitude >> (exponent + 3)) & 15
        sign = 128 if sample < 0 else 0
        encoded.append(~(sign | (exponent << 4) | mantissa) & 255)
    return bytes(encoded)
