"""Adversarial regressions found during branch acceptance, using synthetic data."""
import asyncio
import json
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.api.dependencies import TenantContext, UserContext
from app.api.v1.calls import delete_call_content
from app.calendar.errors import CalendarError
from app.core.config import Settings
from app.models import AppUser, CallChannel, CallSession, Tenant
from app.schemas.calendar import CalendarBookingCreate
from app.schemas.calls import CallAnalysisResult
from app.services.agent_runtime import build_runtime_config
from app.services.calendar_booking import CalendarBookingService
from app.services.call_analysis import CallAnalysisService
from app.services.call_lifecycle import CallLifecycleError, CallLifecycleService
from app.services.call_processing import erase_call_content, process_tenant_calls
from app.services.twilio_media import TwilioMediaBridge


@pytest.fixture
def quality_call(db):
    tenant_id = db.scalar(select(Tenant.id).where(Tenant.slug == "salon-haarkunst-test"))
    item = CallSession(tenant_id=tenant_id, channel=CallChannel.telephone,
        status="provisioned", operating_mode="answering_machine", analysis_status="pending",
        documentation_status="recording", inbox_status="open", started_at=datetime.now(timezone.utc),
        transcript=[{"speaker": "customer", "text": "Rückruf bitte"}])
    db.add(item)
    db.commit()
    yield item
    db.rollback()
    db.execute(delete(CallSession).where(CallSession.id == item.id))
    db.commit()


def test_stale_finish_cannot_restore_deleted_transcript(db, quality_call):
    item = quality_call
    assert item.content_deleted_at is None
    with Session(db.get_bind()) as other:
        saved = other.get(CallSession, item.id)
        saved.status = "ended"
        saved.ended_at = datetime.now(timezone.utc)
        erase_call_content(saved)
        other.commit()
    CallLifecycleService(db, item.tenant_id).finish(item.id, transcript=[{"speaker": "customer", "text": "MUST NOT RETURN"}])
    db.refresh(item)
    assert item.transcript == []
    assert item.documentation_status == "deleted"


def test_stale_connect_cannot_reopen_ended_call(db, quality_call):
    item = quality_call
    with Session(db.get_bind()) as other:
        CallLifecycleService(other, item.tenant_id).finish(item.id)
    with pytest.raises(CallLifecycleError):
        CallLifecycleService(db, item.tenant_id).connect(item.id)
    db.refresh(item)
    assert item.status == "ended"


def test_content_deletion_removes_analysis_committed_after_request_session_loaded(db, quality_call):
    item = quality_call
    item.ended_at = datetime.now(timezone.utc)
    item.status = "ended"
    db.commit()
    assert item.summary is None
    tenant = db.get(Tenant, item.tenant_id)
    user_id = db.scalar(select(AppUser.id).where(AppUser.username == "owner@telefonagent.local"))
    with Session(db.get_bind()) as other:
        analyzed = other.get(CallSession, item.id)
        analyzed.summary = "Newly completed private summary"
        analyzed.caller_name = "Private Name"
        analyzed.analysis_details = {"contact_phone": "01700000001"}
        other.commit()
    delete_call_content(item.id, context=TenantContext(id=tenant.id, tenant=tenant),
        user=UserContext(id=user_id, role=None), db=db, settings=Settings())
    with Session(db.get_bind()) as verify:
        deleted = verify.get(CallSession, item.id)
        assert deleted.content_deleted_at and deleted.transcript == []
        assert deleted.summary is None and deleted.caller_name is None and deleted.analysis_details is None


def test_late_confirmation_repairs_documentation_status(db, quality_call):
    item = quality_call
    item.transcript = []
    db.commit()
    lifecycle = CallLifecycleService(db, item.tenant_id)
    lifecycle.save_segment(item.id, {"segment_id": "message", "speaker": "customer", "text": "Rückruf", "state": "provisional"})
    lifecycle.finish(item.id)
    assert item.documentation_status == "incomplete"
    lifecycle.save_segment(item.id, {"segment_id": "message", "speaker": "customer", "text": "Rückruf bitte", "state": "confirmed"})
    assert item.documentation_status == "complete"


def test_analysis_preserves_manual_inbox_resolution(db, quality_call, monkeypatch):
    item = quality_call
    item.ended_at = datetime.now(timezone.utc)
    db.commit()
    async def analyze(*args):
        with Session(db.get_bind()) as other:
            manual = other.get(CallSession, item.id)
            manual.inbox_status = "done"
            other.commit()
        return CallAnalysisResult(caller_name=None, category="callback", summary="Rückruf", result="Nachricht", action_required=True, action_items=[], priority="normal")
    monkeypatch.setattr("app.services.call_analysis.analyze_transcript", analyze)
    asyncio.run(CallAnalysisService(db, Settings(), item.tenant_id).analyze(item.id))
    assert item.inbox_status == "done"


def test_future_retries_do_not_starve_ready_calls(db, quality_call, monkeypatch):
    item = quality_call
    now = datetime.now(timezone.utc)
    blockers = [CallSession(id=uuid4(), tenant_id=item.tenant_id, channel=CallChannel.telephone,
        status="ended", ended_at=now, analysis_status="failed", analysis_attempts=1,
        analysis_next_attempt_at=now + timedelta(hours=1), created_at=now - timedelta(days=1)) for _ in range(100)]
    db.add_all(blockers)
    item.ended_at = now
    db.commit()
    processed = []
    async def fake(db, settings, tenant_id, call_id):
        processed.append(call_id)
    monkeypatch.setattr("app.services.call_processing.analyze_call_safely", fake)
    try:
        asyncio.run(process_tenant_calls(db, Settings(), item.tenant_id))
        assert item.id in processed
    finally:
        db.execute(delete(CallSession).where(CallSession.id.in_([x.id for x in blockers])))
        db.commit()


def bridge_for_test(db, **kwargs):
    tenant = db.scalar(select(Tenant).where(Tenant.slug == "salon-haarkunst-test"))
    context = TenantContext(id=tenant.id, tenant=tenant)
    runtime = build_runtime_config(db, context, Settings(), test_mode=False)
    return TwilioMediaBridge(None, Settings(), context, runtime, "MZ-test", None, 1, **kwargs)


def test_late_metadata_is_persisted_without_downgrading_final_text(db):
    segments = []
    bridge = bridge_for_test(db, transcript_sink=segments.append)
    asyncio.run(bridge._handle_openai_event(None, {"type": "conversation.item.input_audio_transcription.completed", "item_id": "b", "transcript": "Freitag bitte"}))
    asyncio.run(bridge._handle_openai_event(None, {"type": "input_audio_buffer.committed", "item_id": "b", "previous_item_id": "a"}))
    assert segments[-1]["previous_item_id"] == "a" and segments[-1]["state"] == "confirmed"
    asyncio.run(bridge._handle_openai_event(None, {"type": "conversation.item.input_audio_transcription.delta", "item_id": "b", "delta": "stale"}))
    assert bridge.segment_text["b"] == "Freitag bitte"


def test_late_old_transcription_does_not_replace_latest_confirmation(db):
    bridge = bridge_for_test(db)
    asyncio.run(bridge._handle_openai_event(None, {"type": "input_audio_buffer.speech_started", "item_id": "new", "audio_start_ms": 3000}))
    asyncio.run(bridge._handle_openai_event(None, {"type": "conversation.item.input_audio_transcription.completed", "item_id": "new", "transcript": "Nein, bitte nicht buchen"}))
    asyncio.run(bridge._handle_openai_event(None, {"type": "conversation.item.input_audio_transcription.completed", "item_id": "old", "transcript": "Ja bitte buchen"}))
    assert bridge.latest_user_utterance == "Nein, bitte nicht buchen"


def test_disabled_interruptions_do_not_clear_playback(db):
    bridge = bridge_for_test(db)
    bridge.runtime = replace(bridge.runtime, turn_detection={**bridge.runtime.turn_detection, "interrupt_response": False})
    asyncio.run(bridge._handle_openai_event(None, {"type": "input_audio_buffer.speech_started", "item_id": "caller"}))
    assert bridge.outbound.empty()


def test_new_speech_invalidates_previous_confirmation_until_final_transcript(db):
    bridge = bridge_for_test(db)
    asyncio.run(bridge._handle_openai_event(None, {"type": "conversation.item.input_audio_transcription.completed", "item_id": "old", "transcript": "Ja bitte buchen"}))
    assert bridge.latest_user_utterance == "Ja bitte buchen"
    asyncio.run(bridge._handle_openai_event(None, {"type": "input_audio_buffer.speech_started", "item_id": "correction"}))
    asyncio.run(bridge._handle_openai_event(None, {"type": "conversation.item.input_audio_transcription.completed", "item_id": "old", "transcript": "Ja bitte buchen"}))
    assert bridge.latest_user_utterance is None


def test_cancelling_bridge_cleans_up_all_stream_tasks(db):
    cancelled = set()
    async def run():
        entered = asyncio.Event()
        class Twilio:
            async def receive_text(self):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.add("twilio")
            async def send_json(self, value):
                pass
        class OpenAI:
            def __init__(self):
                self.queue = asyncio.Queue()
                self.queue.put_nowait({"type": "session.created"})
            async def send(self, raw):
                if json.loads(raw)["type"] == "session.update":
                    self.queue.put_nowait({"type": "session.updated"})
            async def recv(self):
                try:
                    return json.dumps(await self.queue.get())
                except asyncio.CancelledError:
                    cancelled.add("openai")
                    raise
        @asynccontextmanager
        async def connector(*args):
            yield OpenAI()
        bridge = bridge_for_test(db, connector=connector)
        bridge.twilio = Twilio()
        task = asyncio.create_task(bridge.run())
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert cancelled == {"twilio", "openai"}


def test_answering_machine_is_blocked_at_calendar_service_boundary(db, quality_call):
    service = CalendarBookingService(db, Settings(), quality_call.tenant_id, "Europe/Berlin")
    payload = CalendarBookingCreate(slot_id="not-used-because-actions-are-disabled", appointment_type_id=uuid4(), customer_name="Synthetic", idempotency_key="qa-direct-denial")
    with pytest.raises(CalendarError) as blocked:
        asyncio.run(service.create(payload, conversation_session_id=quality_call.id))
    assert blocked.value.code == "actions_disabled"


def test_processing_lease_recovery_ignores_unrelated_inbox_updates(db, quality_call, monkeypatch):
    item = quality_call
    item.ended_at = datetime.now(timezone.utc)
    item.analysis_status = "processing"
    item.analysis_lease_token = "expired"
    item.analysis_next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    item.updated_at = datetime.now(timezone.utc)
    db.commit()
    processed = []
    async def analyze(db, settings, tenant_id, call_id):
        processed.append(call_id)
    monkeypatch.setattr("app.services.call_processing.analyze_call_safely", analyze)
    asyncio.run(process_tenant_calls(db, Settings(), item.tenant_id))
    assert processed == [item.id]


def test_repeated_process_crashes_are_bounded(db, quality_call, monkeypatch):
    item = quality_call
    item.ended_at = datetime.now(timezone.utc)
    item.analysis_status = "processing"
    item.analysis_attempts = 3
    item.analysis_next_attempt_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    async def must_not_run(*args):
        pytest.fail("Crash recovery must stop after three claimed attempts")
    monkeypatch.setattr("app.services.call_processing.analyze_call_safely", must_not_run)
    asyncio.run(process_tenant_calls(db, Settings(), item.tenant_id))
    assert item.analysis_status == "failed" and item.analysis_error_code == "analysis_recovery_exhausted"


def test_transcript_limit_is_visible_instead_of_silent_data_loss(db):
    segments = []
    bridge = bridge_for_test(db, transcript_sink=segments.append)
    asyncio.run(bridge._handle_openai_event(None, {"type": "conversation.item.input_audio_transcription.completed", "item_id": "long", "transcript": "a" * 20_001}))
    assert bridge.documentation_error == "transcript_segment_truncated"
    assert len(segments[0]["text"]) == 20_000
