import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, select

from app.api.dependencies import TenantContext, UserContext, get_user_context
from app.core.config import Settings
from app.main import app
from app.models import CallChannel, CallSession, Tenant, TenantRole, ToolExecution
from app.schemas.calls import CallAnalysisResult
from app.services.agent_runtime import build_runtime_config, restore_call_runtime
from app.services.call_analysis import CallAnalysisError, CallAnalysisService
from app.services.call_lifecycle import CallLifecycleService
from app.services.call_processing import erase_call_content, process_tenant_calls
from app.services.conversation_tools import ConversationToolDispatcher
from app.services.tool_audit import ToolAudit
from app.services.voice_providers import LiveProtocol, RealtimeProtocol, select_voice_protocol
from app.voice_evaluation import evaluate_audio


@pytest.fixture(autouse=True)
def cleanup_calls(db):
    existing = set(db.scalars(select(CallSession.id)))
    yield
    db.rollback()
    created = set(db.scalars(select(CallSession.id))) - existing
    if created:
        db.execute(delete(CallSession).where(CallSession.id.in_(created)))
        db.commit()


def tenant(db):
    return db.scalar(select(Tenant).where(Tenant.slug == "salon-haarkunst-test"))


def call(db, **values):
    item = CallSession(tenant_id=tenant(db).id, channel=CallChannel.telephone,
                       provider_session_id="CA" + uuid4().hex, status="connected",
                       operating_mode="answering_machine", documentation_status=values.pop("documentation_status", "recording"), **values)
    db.add(item)
    db.commit()
    return item


def test_mode_changes_preserve_call_snapshot_and_block_tools(db, client):
    t = tenant(db)
    context = TenantContext(id=t.id, tenant=t)
    response = client.get("/api/v1/agent/config").json()
    original = {k: v for k, v in response.items() if k not in {"tenant_id", "version", "updated_at", "can_edit", "role"}}
    update = {**original, "expected_version": response["version"], "operating_mode": "answering_machine", "message_greeting": "Guten Tag bei Beispiel. Was möchten Sie uns mitteilen?"}
    assert client.put("/api/v1/agent/config", json=update).status_code == 200
    db.expire_all()
    runtime = build_runtime_config(db, context, Settings(), test_mode=False)
    assert runtime.tools == [] and runtime.tool_choice == "none"
    assert runtime.greeting == update["message_greeting"]
    assert "Anrufbeantworter" in runtime.prompt
    item, _ = CallLifecycleService(db, t.id).provision_provider_call(CallChannel.telephone, "CA" + uuid4().hex, runtime)
    dispatcher = ConversationToolDispatcher(db, Settings(), context, item.id)
    result = asyncio.run(dispatcher.execute("list_bookable_services", {}, call_id="blocked", latest_confirmed_user_utterance=None))
    assert result["error_code"] == "actions_disabled"
    with pytest.raises(Exception) as blocked:
        ToolAudit(db, t.id, item.id, "direct", "list_bookable_services")
    assert blocked.value.status_code == 403
    assert db.scalar(select(ToolExecution).where(ToolExecution.call_session_id == item.id)) is None
    current = client.get("/api/v1/agent/config").json()
    assert client.put("/api/v1/agent/config", json={**original, "expected_version": current["version"]}).status_code == 200
    restored = restore_call_runtime(item.runtime_manifest_snapshot)
    assert restored.greeting == update["message_greeting"]
    assert restored.tools == []
    db.expire_all()
    assert build_runtime_config(db, context, Settings(), test_mode=False).greeting != restored.greeting


def test_segments_survive_abort_duplicates_and_out_of_order_delivery(db):
    item = call(db)
    service = CallLifecycleService(db, item.tenant_id)
    service.save_segment(item.id, {"segment_id": "b", "previous_item_id": "a", "speaker": "customer", "text": "Bitte rufen", "state": "provisional"})
    service.save_segment(item.id, {"segment_id": "a", "speaker": "agent", "text": "Guten Tag", "state": "confirmed"})
    service.save_segment(item.id, {"segment_id": "b", "speaker": "customer", "text": "Bitte rufen Sie mich zurück.", "state": "confirmed"})
    service.finish(item.id, status="failed", error_code="stream_lost", transcript=[])
    db.expire_all()
    saved = db.get(CallSession, item.id)
    revision = saved.transcript_revision
    service.save_segment(item.id, {"segment_id": "b", "speaker": "customer", "text": "Bitte", "state": "provisional"})
    assert db.get(CallSession, item.id).transcript_revision == revision
    assert [x["segment_id"] for x in saved.transcript] == ["a", "b"]
    assert saved.transcript[-1]["text"] == "Bitte rufen Sie mich zurück."
    assert saved.error_code == "stream_lost"
    service.save_segment(item.id, {"segment_id": "b", "event_id": "duplicate-final", "speaker": "customer", "text": "Bitte rufen Sie mich zurück.", "state": "confirmed"})
    assert db.get(CallSession, item.id).transcript_revision == revision
    CallLifecycleService(db, uuid4()).save_segment(item.id, {"speaker": "customer", "text": "Fremd"})
    assert len(db.get(CallSession, item.id).transcript) == 2


def analysis():
    return CallAnalysisResult(caller_name=None, category="callback", summary="Rückruf gewünscht.", result="Nachricht aufgenommen.", action_required=True, action_items=["Zurückrufen"], priority="normal", callback_required=True, main_concern="Rückruf")


def test_analysis_ignores_provisional_and_rejects_results_after_deletion(db, monkeypatch):
    item = call(db, ended_at=datetime.now(timezone.utc), analysis_status="pending", transcript=[
        {"speaker": "customer", "text": "Bitte zurückrufen", "state": "confirmed"},
        {"speaker": "customer", "text": "vorläufig", "state": "provisional"},
    ])
    async def fake(turns, settings, tenant_id):
        assert [x.text for x in turns] == ["Bitte zurückrufen"]
        erase_call_content(db.get(CallSession, item.id))
        db.commit()
        return analysis()
    monkeypatch.setattr("app.services.call_analysis.analyze_transcript", fake)
    result = asyncio.run(CallAnalysisService(db, Settings(), item.tenant_id).analyze(item.id))
    assert result.summary is None and result.transcript == []
    assert result.analysis_status == "not_requested"


def test_worker_retries_transient_failure_and_recovers_expired_lease(db, monkeypatch):
    now = datetime.now(timezone.utc)
    item = call(db, ended_at=now, analysis_status="pending", transcript=[{"speaker": "customer", "text": "Rückruf bitte"}])
    async def fail(*args):
        raise CallAnalysisError("analysis_timeout")
    monkeypatch.setattr("app.services.call_analysis.analyze_transcript", fail)
    asyncio.run(process_tenant_calls(db, Settings(), item.tenant_id))
    assert item.analysis_status == "failed" and item.analysis_attempts == 1
    assert item.analysis_next_attempt_at
    item.analysis_next_attempt_at = now - timedelta(seconds=1)
    db.commit()
    async def succeed(*args):
        return analysis()
    monkeypatch.setattr("app.services.call_analysis.analyze_transcript", succeed)
    asyncio.run(process_tenant_calls(db, Settings(), item.tenant_id))
    assert item.analysis_status == "completed"
    assert item.analysis_details["callback_required"] is True
    item.analysis_status = "processing"
    item.analysis_lease_token = "expired"
    item.updated_at = now - timedelta(minutes=3)
    db.commit()
    asyncio.run(process_tenant_calls(db, Settings(), item.tenant_id))
    assert item.analysis_status == "completed"


def test_call_list_includes_active_and_retention_erases_content(db, client):
    now = datetime.now(timezone.utc)
    active = call(db, started_at=now)
    old = call(db, ended_at=now - timedelta(days=40), caller_name="Synthetisch", transcript=[{"speaker": "customer", "text": "Alte Nachricht"}])
    assert str(active.id) in {x["id"] for x in client.get("/api/v1/calls").json()}
    asyncio.run(process_tenant_calls(db, Settings(), active.tenant_id))
    db.refresh(old)
    assert old.content_deleted_at and old.transcript == [] and old.caller_name is None
    assert client.delete(f"/api/v1/calls/{active.id}/content").status_code == 409
    assert client.delete(f"/api/v1/calls/{old.id}/content").status_code == 204
    assert client.get(f"/api/v1/calls/{old.id}").headers["cache-control"] == "no-store"


def test_legacy_content_is_not_automatically_deleted_on_upgrade(db):
    old = call(db, ended_at=datetime.now(timezone.utc) - timedelta(days=60), documentation_status="legacy", transcript=[{"speaker": "customer", "text": "Synthetischer Bestand"}])
    asyncio.run(process_tenant_calls(db, Settings(), old.tenant_id))
    db.refresh(old)
    assert old.transcript and not old.content_deleted_at


def test_company_user_cannot_delete_call_content(db, client):
    item = call(db, ended_at=datetime.now(timezone.utc), transcript=[{"speaker": "customer", "text": "Nachricht"}])
    app.dependency_overrides[get_user_context] = lambda: UserContext(id=uuid4(), role=TenantRole.company_user)
    assert client.delete(f"/api/v1/calls/{item.id}/content").status_code == 403
    db.refresh(item)
    assert item.transcript and not item.content_deleted_at


def test_late_transcript_invalidates_in_flight_analysis(db, monkeypatch):
    item = call(db, ended_at=datetime.now(timezone.utc), analysis_status="pending", transcript=[{"speaker": "customer", "text": "Bitte zurückrufen"}])
    async def fake(*args):
        CallLifecycleService(db, item.tenant_id).save_segment(item.id, {"segment_id": "late", "speaker": "customer", "text": "Erst morgen bitte.", "state": "confirmed"})
        return analysis()
    monkeypatch.setattr("app.services.call_analysis.analyze_transcript", fake)
    result = asyncio.run(CallAnalysisService(db, Settings(), item.tenant_id).analyze(item.id))
    assert result.analysis_status == "pending" and result.summary is None
    assert result.transcript[-1]["text"] == "Erst morgen bitte."


@pytest.mark.parametrize("voice", ["nuri", "juni", "flitz"])
def test_live_uses_own_protocol_and_preserves_german_fragments(voice):
    provider = LiveProtocol(voice=voice)
    assert provider.url() == "wss://api.openai.com/v1/live/sessions"
    startup = provider.start(greeting="Guten Tag", company_name="Beispiel")
    assert startup["type"] == "session.start"
    assert startup["session"]["audio"]["format"] == {"type": "audio/pcmu", "rate": 8000}
    assert startup["session"]["store"] is False
    assert provider.audio_append("audio")["type"] == "session.input_audio.append"
    fragment = provider.handle_event({"type": "session.input_transcript.delta", "delta": "Donnerstag, äh Freitag", "start_ms": 100, "end_ms": 900})
    assert fragment["speaker"] == "customer" and fragment["text"] == "Donnerstag, äh Freitag"
    assert fragment["start_ms"] == 100
    assert provider.handle_event({"type": "response.output_audio.delta", "delta": "wrong"}) is None
    assert provider.close()["type"] == "session.close"
    with pytest.raises(ValueError):
        provider.start(greeting="Hallo", company_name="Test", mode="agent")
    with pytest.raises(ValueError):
        select_voice_protocol("live")
    with pytest.raises(ValueError):
        RealtimeProtocol().url("gpt-live-1")
    assert isinstance(select_voice_protocol("realtime"), RealtimeProtocol)


def test_live_evaluation_streams_raw_audio_and_finalizes_without_phone_or_tools(monkeypatch):
    class Connection:
        def __init__(self):
            self.events = asyncio.Queue()
            self.sent = []
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def send(self, raw):
            event = json.loads(raw)
            self.sent.append(event)
            if event["type"] == "session.start":
                self.events.put_nowait({"type": "session.started"})
            if event["type"] == "session.input_audio.append" and len(self.sent) == 3:
                self.events.put_nowait({"type": "session.output_audio.delta", "delta": base64.b64encode(b"\xff" * 160).decode()})
                self.events.put_nowait({"type": "session.input_transcript.delta", "delta": "Rückruf bitte", "start_ms": 0, "end_ms": 20})
            if event["type"] == "session.close":
                self.events.put_nowait({"type": "session.closed", "reason": "close_requested", "usage": {"seconds": 3.02}})
        async def recv(self):
            return json.dumps(await self.events.get())
    connection = Connection()
    async def no_delay(*args):
        pass
    monkeypatch.setattr("app.voice_evaluation.asyncio.sleep", no_delay)
    result = asyncio.run(evaluate_audio(LiveProtocol(), b"\xff" * 160, Settings(openai_api_key="synthetic-test-key"), connector=lambda *args, **kwargs: connection))
    assert result["finalized"] is True
    assert result["output_audio_bytes"] == 160
    assert result["transcript_fragments"] == 1
    assert result["usage"] == {"seconds": 3.02}
    assert connection.sent[0]["type"] == "session.start"
    assert connection.sent[-1]["type"] == "session.close"
    assert all(x["type"] != "response.create" for x in connection.sent)
