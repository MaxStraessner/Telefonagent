from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

from fastapi import WebSocket

from app.api.dependencies import TenantContext
from app.core.config import Settings
from app.services.agent_runtime import AgentRuntimeConfig
from app.services.conversation_tools import ConversationToolDispatcher
from app.services.tool_projections import outbound_wire_tools

MAX_EVENT_BYTES = 1_000_000
MAX_AUDIO_BYTES = 160_000
OUTBOUND_QUEUE_SIZE = 64
OPENAI_SETUP_TIMEOUT_SECONDS = 10
STREAM_IDLE_TIMEOUT_SECONDS = 45


class TwilioMediaError(Exception):
    def __init__(
        self,
        code: str,
        *,
        provider_code: str | None = None,
        provider_param: str | None = None,
        provider_message: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.provider_code = provider_code
        self.provider_param = provider_param
        self.provider_message = provider_message

    def log_context(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "provider_error_code": self.provider_code,
                "provider_error_param": self.provider_param,
                "provider_error_message": self.provider_message,
            }.items()
            if value
        }


@asynccontextmanager
async def openai_connection(settings: Settings, runtime: AgentRuntimeConfig) -> AsyncIterator[Any]:
    from websockets.asyncio.client import connect

    if not settings.openai_api_key:
        raise TwilioMediaError("realtime_not_configured")
    url = f"wss://api.openai.com/v1/realtime?model={runtime.model}"
    safety_digest = hmac.new(
        settings.openai_safety_identifier_salt.encode("utf-8"),
        str(runtime.manifest.tenant_id).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    async with connect(
        url,
        additional_headers={
            "Authorization": f"Bearer {settings.openai_api_key}",
            "OpenAI-Safety-Identifier": f"tenant_{safety_digest[:32]}",
        },
        open_timeout=OPENAI_SETUP_TIMEOUT_SECONDS,
        close_timeout=5,
        max_size=MAX_EVENT_BYTES,
    ) as websocket:
        yield websocket


def session_update(runtime: AgentRuntimeConfig) -> dict[str, Any]:
    manifest = runtime.manifest
    transcription = (
        {"model": "gpt-4o-mini-transcribe", "language": manifest.language}
        if manifest.transcription_enabled
        else None
    )
    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "model": manifest.model,
            "output_modalities": ["audio"],
            "instructions": manifest.instructions,
            "max_output_tokens": manifest.max_output_tokens,
            "parallel_tool_calls": False,
            "audio": {
                "input": {
                    "format": {"type": "audio/pcmu"},
                    "noise_reduction": {"type": "far_field"},
                    "transcription": transcription,
                    "turn_detection": manifest.vad.model_dump(mode="json", exclude_none=True),
                },
                "output": {
                    "format": {"type": "audio/pcmu"},
                    "voice": manifest.voice,
                    "speed": manifest.speed,
                },
            },
            "tools": outbound_wire_tools(manifest.tools),
            "tool_choice": runtime.tool_choice,
        },
    }


def _sanitized_openai_error(event: dict[str, Any]) -> dict[str, str | None]:
    error = event.get("error")
    if not isinstance(error, dict):
        return {"provider_code": None, "provider_param": None, "provider_message": None}

    def field(name: str, maximum: int) -> str | None:
        value = error.get(name)
        if not isinstance(value, str):
            return None
        sanitized = " ".join(value.split())
        sanitized = re.sub(r"sk-[A-Za-z0-9_-]+", "[redacted]", sanitized)
        sanitized = re.sub(
            r"Bearer\s+\S+", "Bearer [redacted]", sanitized, flags=re.IGNORECASE
        )
        sanitized = re.sub(r"'[^']{49,}'", "'[redacted]'", sanitized)
        sanitized = re.sub(r'"[^"]{49,}"', '"[redacted]"', sanitized)
        return sanitized[:maximum] or None

    return {
        "provider_code": field("code", 80),
        "provider_param": field("param", 160),
        "provider_message": field("message", 300),
    }


def _openai_rejection(code: str, event: dict[str, Any]) -> TwilioMediaError:
    return TwilioMediaError(code, **_sanitized_openai_error(event))


def _decode_event(raw: str | bytes) -> dict[str, Any]:
    size = len(raw)
    if size > MAX_EVENT_BYTES:
        raise TwilioMediaError("event_too_large")
    try:
        value = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise TwilioMediaError("invalid_event") from exc
    if not isinstance(value, dict) or not isinstance(value.get("type") or value.get("event"), str):
        raise TwilioMediaError("invalid_event")
    return value


def _validate_audio(payload: object) -> str:
    if not isinstance(payload, str) or len(payload) > MAX_AUDIO_BYTES * 2:
        raise TwilioMediaError("invalid_audio_payload")
    try:
        decoded = base64.b64decode(payload, validate=True)
    except ValueError as exc:
        raise TwilioMediaError("invalid_audio_payload") from exc
    if not decoded or len(decoded) > MAX_AUDIO_BYTES:
        raise TwilioMediaError("invalid_audio_payload")
    return payload


@dataclass
class PlaybackState:
    item_id: str | None = None
    total_audio_bytes: int = 0
    total_ms: int = 0
    acknowledged_ms: int = 0
    mark_counter: int = 0
    marks: dict[str, int] = field(default_factory=dict)

    def add_audio(self, item_id: str | None, encoded_audio: str) -> str:
        decoded_bytes = len(base64.b64decode(encoded_audio))
        if item_id and item_id != self.item_id:
            self.item_id = item_id
            self.total_audio_bytes = 0
            self.total_ms = 0
            self.acknowledged_ms = 0
            self.marks.clear()
        self.total_audio_bytes += decoded_bytes
        self.total_ms = self.total_audio_bytes // 8
        self.mark_counter += 1
        name = f"ai-{self.mark_counter}"
        self.marks[name] = self.total_ms
        return name

    def acknowledge(self, name: str) -> None:
        duration = self.marks.pop(name, None)
        if duration is not None:
            self.acknowledged_ms = max(self.acknowledged_ms, duration)

    def clear(self) -> tuple[str | None, int]:
        result = self.item_id, min(self.acknowledged_ms, self.total_ms)
        self.item_id = None
        self.total_audio_bytes = 0
        self.total_ms = 0
        self.acknowledged_ms = 0
        self.marks.clear()
        return result


class TwilioMediaBridge:
    def __init__(
        self,
        twilio: WebSocket,
        settings: Settings,
        context: TenantContext,
        runtime: AgentRuntimeConfig,
        stream_sid: str,
        dispatcher: ConversationToolDispatcher,
        initial_sequence: int,
        *,
        connector: Callable[[Settings, AgentRuntimeConfig], Any] = openai_connection,
    ) -> None:
        self.twilio = twilio
        self.settings = settings
        self.context = context
        self.runtime = runtime
        self.stream_sid = stream_sid
        self.dispatcher = dispatcher
        self.initial_sequence = initial_sequence
        self.connector = connector
        self.playback = PlaybackState()
        self.latest_user_utterance: str | None = None
        self.outbound: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(OUTBOUND_QUEUE_SIZE)

    async def run(self) -> None:
        try:
            async with asyncio.timeout(
                self.runtime.manifest.maximum_session_minutes * 60
            ):
                async with self.connector(self.settings, self.runtime) as openai:
                    await self._await_session_created(openai)
                    await openai.send(json.dumps(session_update(self.runtime)))
                    await self._await_session_updated(openai)
                    await openai.send(json.dumps({
                        "type": "response.create",
                        "response": {"instructions": self.runtime.manifest.initial_response_instructions},
                    }))
                    tasks = {
                        asyncio.create_task(self._send_twilio()),
                        asyncio.create_task(self._receive_twilio(openai)),
                        asyncio.create_task(self._receive_openai(openai)),
                    }
                    done, pending = await asyncio.wait(
                        tasks, return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    for task in done:
                        task.result()
        except TimeoutError as exc:
            raise TwilioMediaError("maximum_session_duration_reached") from exc

    async def _await_session_created(self, openai: Any) -> None:
        try:
            event = _decode_event(
                await asyncio.wait_for(
                    openai.recv(), timeout=OPENAI_SETUP_TIMEOUT_SECONDS
                )
            )
        except TimeoutError as exc:
            raise TwilioMediaError("openai_session_creation_timeout") from exc
        if event["type"] == "session.created":
            return
        if event["type"] == "error":
            raise _openai_rejection("openai_connection_rejected", event)
        raise TwilioMediaError("invalid_openai_initial_event")

    async def _await_session_updated(self, openai: Any) -> None:
        async with asyncio.timeout(OPENAI_SETUP_TIMEOUT_SECONDS):
            while True:
                event = _decode_event(await openai.recv())
                if event["type"] == "session.updated":
                    return
                if event["type"] == "error":
                    raise _openai_rejection("openai_session_rejected", event)

    async def _send_twilio(self) -> None:
        while True:
            event = await self.outbound.get()
            if event is None:
                return
            await self.twilio.send_json(event)

    async def _receive_twilio(self, openai: Any) -> None:
        last_sequence = self.initial_sequence
        try:
            while True:
                raw = await asyncio.wait_for(
                    self.twilio.receive_text(), timeout=STREAM_IDLE_TIMEOUT_SECONDS
                )
                event = _decode_event(raw)
                try:
                    sequence = int(event["sequenceNumber"])
                except (KeyError, TypeError, ValueError) as exc:
                    raise TwilioMediaError("invalid_sequence") from exc
                if sequence != last_sequence + 1:
                    raise TwilioMediaError("invalid_sequence")
                last_sequence = sequence
                kind = event.get("event")
                if kind == "media":
                    if str((event.get("media") or {}).get("track") or "") != "inbound":
                        raise TwilioMediaError("invalid_media_track")
                    if str(event.get("streamSid") or "") != self.stream_sid:
                        raise TwilioMediaError("stream_context_mismatch")
                    payload = _validate_audio((event.get("media") or {}).get("payload"))
                    await openai.send(json.dumps({"type": "input_audio_buffer.append", "audio": payload}))
                elif kind == "mark":
                    if str(event.get("streamSid") or "") != self.stream_sid:
                        raise TwilioMediaError("stream_context_mismatch")
                    self.playback.acknowledge(str((event.get("mark") or {}).get("name") or ""))
                elif kind == "dtmf":
                    dtmf = event.get("dtmf") or {}
                    if (
                        str(event.get("streamSid") or "") != self.stream_sid
                        or str(dtmf.get("track") or "") != "inbound_track"
                        or str(dtmf.get("digit") or "") not in "0123456789*#ABCD"
                    ):
                        raise TwilioMediaError("invalid_dtmf_event")
                elif kind == "stop":
                    if str(event.get("streamSid") or "") != self.stream_sid:
                        raise TwilioMediaError("stream_context_mismatch")
                    return
                else:
                    raise TwilioMediaError("unsupported_twilio_event")
        finally:
            await self.outbound.put(None)

    async def _receive_openai(self, openai: Any) -> None:
        while True:
            event = _decode_event(await openai.recv())
            kind = event["type"]
            if kind == "response.output_audio.delta":
                audio = _validate_audio(event.get("delta"))
                mark_name = self.playback.add_audio(event.get("item_id"), audio)
                await self.outbound.put({"event": "media", "streamSid": self.stream_sid, "media": {"payload": audio}})
                await self.outbound.put({"event": "mark", "streamSid": self.stream_sid, "mark": {"name": mark_name}})
            elif kind == "input_audio_buffer.speech_started":
                item_id, audio_end_ms = self.playback.clear()
                await self.outbound.put({"event": "clear", "streamSid": self.stream_sid})
                if item_id:
                    await openai.send(json.dumps({
                        "type": "conversation.item.truncate",
                        "item_id": item_id,
                        "content_index": 0,
                        "audio_end_ms": audio_end_ms,
                    }))
            elif kind == "conversation.item.input_audio_transcription.completed":
                transcript = str(event.get("transcript") or "").strip()
                self.latest_user_utterance = transcript[:300] or None
            elif kind == "response.function_call_arguments.done":
                await self._execute_tool(openai, event)
            elif kind == "error":
                raise _openai_rejection("openai_stream_error", event)

    async def _execute_tool(self, openai: Any, event: dict[str, Any]) -> None:
        call_id = str(event.get("call_id") or "")
        name = str(event.get("name") or "")
        if not call_id or not name:
            raise TwilioMediaError("invalid_tool_call")
        try:
            arguments = json.loads(event.get("arguments") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            arguments = {}
        if not isinstance(arguments, dict):
            arguments = {}
        result = await self.dispatcher.execute(
            name,
            arguments,
            call_id=call_id,
            latest_confirmed_user_utterance=self.latest_user_utterance,
        )
        await openai.send(json.dumps({
            "type": "conversation.item.create",
            "item": {"type": "function_call_output", "call_id": call_id, "output": json.dumps(result, ensure_ascii=False)},
        }))
        await openai.send(json.dumps({"type": "response.create"}))
        self.dispatcher.mark_result_sent(call_id)
