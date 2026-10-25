import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import delete, select

from app.core.config import Settings
from app.models import CallChannel, CallSession, Tenant
from app.schemas.calls import TranscriptTurn
from app.services.call_analysis import (
    CallAnalysisError,
    CallAnalysisService,
    analyze_call_safely,
    analyze_transcript,
)
from app.services.provisioning import ProvisioningService


def _tenant(db) -> Tenant:
    return db.scalar(select(Tenant).where(Tenant.slug == "salon-haarkunst-test"))


def _call(db, tenant_id, **values) -> CallSession:
    started_at = values.pop("started_at", datetime.now(timezone.utc) - timedelta(minutes=3))
    analysis_status = values.pop("analysis_status", "pending")
    call = CallSession(
        tenant_id=tenant_id,
        channel=CallChannel.telephone,
        status="ended",
        provider_session_id="CA" + uuid4().hex,
        started_at=started_at,
        ended_at=values.pop("ended_at", started_at + timedelta(minutes=2)),
        duration_seconds=120,
        inbox_status="open",
        transcript=[
            {"speaker": "agent", "text": "Guten Tag, wie kann ich helfen?"},
            {"speaker": "customer", "text": "Ich möchte einen Termin."},
        ],
        analysis_status=analysis_status,
        priority="normal",
        action_required=False,
        action_items=[],
        **values,
    )
    db.add(call)
    db.commit()
    db.refresh(call)
    return call


def _cleanup(db, *call_ids) -> None:
    db.execute(delete(CallSession).where(CallSession.id.in_(call_ids)))
    db.commit()


def test_call_list_detail_status_and_tenant_isolation(client, db):
    tenant = _tenant(db)
    foreign_tenant, _ = ProvisioningService(db).provision_tenant(
        slug=f"call-history-{uuid4().hex[:8]}",
        name="Fremdes Unternehmen",
        industry="services",
        timezone_name="Europe/Berlin",
        username=f"foreign-{uuid4().hex[:8]}",
        password="a sufficiently strong foreign password",
        display_name="Foreign Admin",
        email=None,
    )
    own = _call(
        db,
        tenant.id,
        caller_phone="+491701234567",
        caller_name="Max Mustermann",
        category="appointment",
        summary="Der Kunde möchte einen Termin.",
        result="Termin vereinbart.",
        analysis_status="completed",
    )
    foreign = _call(db, foreign_tenant.id, caller_name="Nicht sichtbar")

    response = client.get("/api/v1/calls")
    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [str(own.id)]
    assert response.json()[0]["caller_phone"] == "+491701234567"

    detail = client.get(f"/api/v1/calls/{own.id}")
    assert detail.status_code == 200
    assert detail.json()["summary"] == "Der Kunde möchte einen Termin."
    assert detail.json()["transcript"][1] == {
        "speaker": "customer",
        "text": "Ich möchte einen Termin.",
    }

    changed = client.patch(f"/api/v1/calls/{own.id}", json={"status": "done"})
    assert changed.status_code == 200
    assert changed.json()["status"] == "done"
    assert client.get(f"/api/v1/calls/{foreign.id}").status_code == 404
    assert client.patch(
        f"/api/v1/calls/{foreign.id}", json={"status": "done"}
    ).status_code == 404
    db.expire_all()
    assert db.get(CallSession, foreign.id).inbox_status == "open"
    _cleanup(db, own.id, foreign.id)


class FakeResponsesClient:
    response: httpx.Response
    last_json: dict | None = None
    last_headers: dict | None = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, *, json, headers):
        type(self).last_json = json
        type(self).last_headers = headers
        return type(self).response


def _openai_response(value: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(value)}],
                }
            ]
        },
        request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
    )


@pytest.mark.parametrize(
    "analysis",
    [
        {
            "caller_name": "Max Mustermann",
            "category": "appointment",
            "summary": "Ein Termin wird gewünscht.",
            "result": "Dienstag vereinbart.",
            "action_required": False,
            "action_items": [],
            "priority": "normal",
        },
        {
            "caller_name": "Eva Beispiel",
            "category": "callback",
            "summary": "Die Kundin bittet um Rückruf.",
            "result": "Rückruf offen.",
            "action_required": True,
            "action_items": ["Kundin zurückrufen"],
            "priority": "important",
        },
        {
            "caller_name": None,
            "category": "general_question",
            "summary": "Öffnungszeiten wurden erfragt.",
            "result": "Frage beantwortet.",
            "action_required": False,
            "action_items": [],
            "priority": "normal",
        },
        {
            "caller_name": "Mehrere Aufgaben",
            "category": "complaint",
            "summary": "Eine Beschwerde wurde aufgenommen.",
            "result": "Prüfung ausstehend.",
            "action_required": True,
            "action_items": ["Vorgang prüfen", "Kunden zurückrufen"],
            "priority": "urgent",
        },
    ],
)
def test_call_analysis_parses_supported_business_cases(monkeypatch, analysis):
    FakeResponsesClient.response = _openai_response(analysis)
    monkeypatch.setattr(
        "app.services.call_analysis.httpx.AsyncClient", FakeResponsesClient
    )
    settings = Settings(openai_api_key="server-only-test-key")
    result = asyncio.run(
        analyze_transcript(
            [TranscriptTurn(speaker="customer", text="Testgespräch")],
            settings,
            uuid4(),
        )
    )
    assert result.model_dump(include=set(analysis)) == analysis
    assert FakeResponsesClient.last_json["model"] == "gpt-5.4-nano"
    assert FakeResponsesClient.last_json["store"] is False
    assert FakeResponsesClient.last_json["text"]["format"]["strict"] is True
    assert FakeResponsesClient.last_headers["OpenAI-Safety-Identifier"].startswith(
        "tenant_"
    )


def test_invalid_analysis_response_is_rejected(monkeypatch):
    FakeResponsesClient.response = httpx.Response(
        200,
        json={"output_text": "not-json"},
        request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
    )
    monkeypatch.setattr(
        "app.services.call_analysis.httpx.AsyncClient", FakeResponsesClient
    )
    with pytest.raises(CallAnalysisError) as rejected:
        asyncio.run(
            analyze_transcript(
                [TranscriptTurn(speaker="customer", text="Test")],
                Settings(openai_api_key="server-only-test-key"),
                uuid4(),
            )
        )
    assert rejected.value.code == "analysis_invalid_response"


def test_analysis_result_is_saved_and_sets_default_inbox_status(monkeypatch, db):
    tenant = _tenant(db)
    call = _call(db, tenant.id)
    FakeResponsesClient.response = _openai_response(
        {
            "caller_name": "Rita Rückruf",
            "category": "callback",
            "summary": "Rita bittet um einen Rückruf.",
            "result": "Rückruf noch offen.",
            "action_required": True,
            "action_items": ["Rita zurückrufen"],
            "priority": "important",
        }
    )
    monkeypatch.setattr(
        "app.services.call_analysis.httpx.AsyncClient", FakeResponsesClient
    )
    result = asyncio.run(
        CallAnalysisService(
            db, Settings(openai_api_key="server-only-test-key"), tenant.id
        ).analyze(call.id)
    )
    assert result.analysis_status == "completed"
    assert result.inbox_status == "open"
    assert result.caller_name == "Rita Rückruf"
    assert result.action_items == ["Rita zurückrufen"]
    _cleanup(db, call.id)


def test_failed_analysis_preserves_call_and_transcript(monkeypatch, db):
    tenant = _tenant(db)
    call = _call(db, tenant.id)
    original_transcript = list(call.transcript)
    FakeResponsesClient.response = httpx.Response(
        200,
        json={"output_text": "invalid"},
        request=httpx.Request("POST", "https://api.openai.com/v1/responses"),
    )
    monkeypatch.setattr(
        "app.services.call_analysis.httpx.AsyncClient", FakeResponsesClient
    )
    result = asyncio.run(
        CallAnalysisService(
            db, Settings(openai_api_key="server-only-test-key"), tenant.id
        ).analyze(call.id)
    )
    assert result.analysis_status == "failed"
    assert result.analysis_error_code == "analysis_invalid_response"
    assert result.status == "ended"
    assert result.transcript == original_transcript
    assert db.get(CallSession, call.id) is not None
    _cleanup(db, call.id)


def test_unexpected_analysis_failure_is_retryable_and_preserves_transcript(
    monkeypatch, db
):
    tenant = _tenant(db)
    call = _call(db, tenant.id)
    original_transcript = list(call.transcript)

    async def fail_unexpectedly(*args, **kwargs):
        raise RuntimeError("unexpected test failure")

    monkeypatch.setattr(CallAnalysisService, "analyze", fail_unexpectedly)
    asyncio.run(
        analyze_call_safely(
            db, Settings(openai_api_key="server-only-test-key"), tenant.id, call.id
        )
    )

    db.expire_all()
    saved = db.get(CallSession, call.id)
    assert saved is not None
    assert saved.analysis_status == "failed"
    assert saved.analysis_error_code == "analysis_unexpected_failure"
    assert saved.transcript == original_transcript
    _cleanup(db, call.id)
