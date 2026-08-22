from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import httpx
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import CallChannel, CallSession
from app.schemas.calls import CallAnalysisResult, TranscriptTurn
from app.services.realtime import build_safety_identifier_for_tenant

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
logger = logging.getLogger(__name__)

ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "caller_name": {"type": ["string", "null"], "maxLength": 150},
        "category": {
            "type": "string",
            "enum": [
                "appointment",
                "callback",
                "price_request",
                "general_question",
                "complaint",
                "new_customer",
                "existing_customer",
                "other",
            ],
        },
        "summary": {"type": "string", "maxLength": 4000},
        "result": {"type": "string", "maxLength": 4000},
        "action_required": {"type": "boolean"},
        "action_items": {
            "type": "array",
            "items": {"type": "string", "maxLength": 500},
            "maxItems": 20,
        },
        "priority": {
            "type": "string",
            "enum": ["normal", "important", "urgent"],
        },
    },
    "required": [
        "caller_name",
        "category",
        "summary",
        "result",
        "action_required",
        "action_items",
        "priority",
    ],
}

ANALYSIS_INSTRUCTIONS = """Du wertest das Transkript eines geschäftlichen Telefonats aus.
Verwende ausschließlich Informationen, die ausdrücklich aus dem Gespräch hervorgehen.
Erfinde nichts. Unbekannte Namen sind null, unbekannte Ergebnisse oder Zusammenfassungen leer.
Setze urgent nur bei klar erkennbarem dringendem Handlungsbedarf.
Kategorien: appointment, callback, price_request, general_question, complaint,
new_customer, existing_customer oder other."""


class CallAnalysisError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def render_transcript(turns: list[TranscriptTurn]) -> str:
    labels = {"agent": "Agent", "customer": "Kunde"}
    return "\n\n".join(f"{labels[turn.speaker]}: {turn.text}" for turn in turns)


def _output_text(payload: dict[str, Any]) -> str:
    direct = payload.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    for item in payload.get("output") or []:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content") or []:
            if isinstance(content, dict) and content.get("type") == "output_text":
                value = content.get("text")
                if isinstance(value, str) and value.strip():
                    return value
    raise CallAnalysisError("analysis_response_missing")


async def analyze_transcript(
    turns: list[TranscriptTurn], settings: Settings, tenant_id: UUID
) -> CallAnalysisResult:
    if not settings.openai_api_key:
        raise CallAnalysisError("analysis_not_configured")
    payload = {
        "model": settings.openai_call_analysis_model,
        "store": False,
        "reasoning": {"effort": "none"},
        "max_output_tokens": 1000,
        "instructions": ANALYSIS_INSTRUCTIONS,
        "input": render_transcript(turns),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "call_analysis",
                "description": "Strukturierte Auswertung eines abgeschlossenen Telefonats.",
                "strict": True,
                "schema": ANALYSIS_SCHEMA,
            }
        },
    }
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(
                settings.openai_call_analysis_timeout_seconds, connect=4.0
            )
        ) as client:
            response = await client.post(
                OPENAI_RESPONSES_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {settings.openai_api_key}",
                    "OpenAI-Safety-Identifier": build_safety_identifier_for_tenant(
                        tenant_id, settings
                    ),
                },
            )
    except httpx.TimeoutException as exc:
        raise CallAnalysisError("analysis_timeout") from exc
    except httpx.HTTPError as exc:
        raise CallAnalysisError("analysis_unavailable") from exc
    if response.status_code >= 400:
        raise CallAnalysisError("analysis_provider_rejected")
    try:
        payload_json = response.json()
        if not isinstance(payload_json, dict):
            raise TypeError
        return CallAnalysisResult.model_validate_json(_output_text(payload_json))
    except (TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        raise CallAnalysisError("analysis_invalid_response") from exc


class CallAnalysisService:
    def __init__(self, db: Session, settings: Settings, tenant_id: UUID) -> None:
        self.db = db
        self.settings = settings
        self.tenant_id = tenant_id

    def _call(self, call_id: UUID, *, for_update: bool = False) -> CallSession | None:
        statement = select(CallSession).where(
            CallSession.id == call_id,
            CallSession.tenant_id == self.tenant_id,
            CallSession.channel == CallChannel.telephone,
        )
        return self.db.scalar(statement.with_for_update() if for_update else statement)

    async def analyze(self, call_id: UUID, *, retry: bool = False) -> CallSession:
        call = self._call(call_id, for_update=True)
        if call is None:
            self.db.rollback()
            raise CallAnalysisError("call_not_found")
        allowed = {"failed"} if retry else {"pending"}
        if call.analysis_status == "completed" or call.analysis_status == "processing":
            self.db.rollback()
            return call
        if call.analysis_status not in allowed:
            self.db.rollback()
            return call
        try:
            turns = [TranscriptTurn.model_validate(item) for item in (call.transcript or [])]
        except ValidationError:
            turns = []
        if not turns:
            call.analysis_status = "failed"
            call.analysis_error_code = "transcript_empty"
            call.updated_at = datetime.now(timezone.utc)
            self.db.commit()
            self.db.refresh(call)
            return call

        call.analysis_status = "processing"
        call.analysis_error_code = None
        call.analysis_attempts = (call.analysis_attempts or 0) + 1
        call.updated_at = datetime.now(timezone.utc)
        self.db.commit()
        try:
            analysis = await analyze_transcript(turns, self.settings, self.tenant_id)
        except CallAnalysisError as exc:
            call = self._call(call_id, for_update=True)
            if call is None:
                self.db.rollback()
                raise
            call.analysis_status = "failed"
            call.analysis_error_code = exc.code
            call.updated_at = datetime.now(timezone.utc)
            self.db.commit()
            self.db.refresh(call)
            logger.warning(
                "call_analysis_failed",
                extra={"event_name": "call_analysis_failed", "error_code": exc.code},
            )
            return call

        call = self._call(call_id, for_update=True)
        if call is None:
            self.db.rollback()
            raise CallAnalysisError("call_not_found")
        call.caller_name = analysis.caller_name
        call.category = analysis.category
        call.summary = analysis.summary
        call.result = analysis.result
        call.action_required = analysis.action_required
        call.action_items = analysis.action_items
        call.priority = analysis.priority
        call.inbox_status = "open" if analysis.action_required else "done"
        call.analysis_status = "completed"
        call.analysis_error_code = None
        call.analyzed_at = datetime.now(timezone.utc)
        call.updated_at = call.analyzed_at
        self.db.commit()
        self.db.refresh(call)
        return call


async def analyze_call_safely(
    db: Session, settings: Settings, tenant_id: UUID, call_id: UUID
) -> None:
    try:
        await CallAnalysisService(db, settings, tenant_id).analyze(call_id)
    except Exception:
        db.rollback()
        try:
            call = db.scalar(
                select(CallSession)
                .where(
                    CallSession.id == call_id,
                    CallSession.tenant_id == tenant_id,
                    CallSession.channel == CallChannel.telephone,
                )
                .with_for_update()
            )
            if call is not None and call.analysis_status != "completed":
                call.analysis_status = "failed"
                call.analysis_error_code = "analysis_unexpected_failure"
                call.updated_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            db.rollback()
        logger.exception(
            "call_analysis_unexpected_failure",
            extra={"event_name": "call_analysis_unexpected_failure"},
        )
