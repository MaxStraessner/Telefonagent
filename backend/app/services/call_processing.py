"""Durable call work queue on existing call rows, with tenant-bound sessions."""
import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, or_, select

from app.db.session import SessionLocal, bind_tenant_context
from app.models import AgentConfiguration, CallChannel, CallSession, Tenant
from app.services.call_analysis import analyze_call_safely
from app.services.call_lifecycle import CallLifecycleService

logger = logging.getLogger(__name__)


def erase_call_content(call: CallSession) -> None:
    call.transcript = []
    call.caller_name = None
    call.caller_phone = None
    call.summary = None
    call.result = None
    call.category = None
    call.action_items = []
    call.action_required = False
    call.analysis_details = None
    call.analysis_status = "not_requested"
    call.analysis_lease_token = None
    call.analysis_next_attempt_at = None
    call.content_deleted_at = datetime.now(timezone.utc)
    call.documentation_status = "deleted"
    call.transcript_revision = (call.transcript_revision or 0) + 1


async def process_tenant_calls(db, settings, tenant_id) -> None:
    now = datetime.now(timezone.utc)
    config = db.scalar(select(AgentConfiguration).where(AgentConfiguration.tenant_id == tenant_id))
    retention = config.call_retention_days if config else 30
    calls = list(db.scalars(select(CallSession).where(
        CallSession.tenant_id == tenant_id, CallSession.channel == CallChannel.telephone,
        CallSession.content_deleted_at.is_(None),
        or_(CallSession.ended_at.is_(None), CallSession.analysis_status.in_(["pending", "processing"]),
            and_(CallSession.analysis_status == "failed", CallSession.analysis_next_attempt_at.is_not(None), CallSession.analysis_attempts < 3),
            and_(CallSession.documentation_status != "legacy", CallSession.ended_at < now - timedelta(days=retention))),
    ).order_by(CallSession.created_at).limit(100)))
    for call in calls:
        if call.documentation_status != "legacy" and call.ended_at and _utc(call.ended_at) < now - timedelta(days=retention):
            erase_call_content(call)
            db.commit()
            continue
        if call.ended_at is None:
            start = _utc(call.started_at or call.created_at)
            if start < now - timedelta(minutes=settings.openai_realtime_max_session_minutes + 2):
                CallLifecycleService(db, tenant_id).finish(call.id, status="failed", error_code="stream_lost", phase="recovery")
            continue
        if call.analysis_status == "processing":
            if _utc(call.updated_at) < now - timedelta(minutes=2):
                call.analysis_status = "pending"
                call.analysis_lease_token = None
                db.commit()
            else:
                continue
        if call.analysis_status == "failed":
            if not call.analysis_next_attempt_at or call.analysis_attempts >= 3:
                continue
            if _utc(call.analysis_next_attempt_at) > now:
                continue
            call.analysis_status = "pending"
            db.commit()
        if call.analysis_status == "pending" and (not call.analysis_next_attempt_at or _utc(call.analysis_next_attempt_at) <= now):
            await analyze_call_safely(db, settings, tenant_id, call.id)


def _utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


async def call_processing_loop(settings) -> None:
    while True:
        try:
            with SessionLocal() as db:
                tenant_ids = list(db.scalars(select(Tenant.id)))
            for tenant_id in tenant_ids:
                with SessionLocal() as db:
                    bind_tenant_context(db, tenant_id)
                    await process_tenant_calls(db, settings, tenant_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Provider exceptions and transcript content must never reach ordinary logs.
            logger.error("call_processing_cycle_failed")
        await asyncio.sleep(5)
