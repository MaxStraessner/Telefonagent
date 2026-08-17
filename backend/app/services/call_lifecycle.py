from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CallChannel, CallSession
from app.services.agent_runtime import AgentRuntimeConfig

TERMINAL_CALL_STATUSES = frozenset({"ended", "cancelled", "failed", "abandoned"})


class CallLifecycleError(Exception):
    pass


class CallLifecycleService:
    def __init__(self, db: Session, tenant_id: UUID) -> None:
        self.db = db
        self.tenant_id = tenant_id

    def find_provider_call(
        self, channel: CallChannel, provider_session_id: str, *, for_update: bool = False
    ) -> CallSession | None:
        statement = select(CallSession).where(
            CallSession.tenant_id == self.tenant_id,
            CallSession.channel == channel,
            CallSession.provider_session_id == provider_session_id,
        )
        return self.db.scalar(statement.with_for_update() if for_update else statement)

    def provision_provider_call(
        self,
        channel: CallChannel,
        provider_session_id: str,
        runtime: AgentRuntimeConfig,
    ) -> tuple[CallSession, bool]:
        existing = self.find_provider_call(channel, provider_session_id)
        if existing is not None:
            return existing, False
        now = datetime.now(timezone.utc)
        call = CallSession(
            tenant_id=self.tenant_id,
            channel=channel,
            provider_session_id=provider_session_id,
            status="provisioned",
            started_at=now,
            bootstrap_status="completed",
            runtime_state="connecting",
        )
        self.record_runtime(call, runtime, commit=False)
        self.db.add(call)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            concurrent = self.find_provider_call(channel, provider_session_id)
            if concurrent is None:
                raise
            return concurrent, False
        self.db.refresh(call)
        return call, True

    def record_runtime(
        self, call: CallSession, runtime: AgentRuntimeConfig, *, commit: bool = True
    ) -> None:
        manifest = runtime.manifest
        call.configuration_version = runtime.version
        call.runtime_manifest_digest = manifest.digest
        call.runtime_manifest_snapshot = {
            "model": manifest.model,
            "voice": manifest.voice,
            "speed": manifest.speed,
            "language": manifest.language,
            "prompt_digest": manifest.prompt_digest,
            "tool_names": manifest.tool_names,
            "tools_digest": manifest.tools_digest,
            "vad": manifest.vad.model_dump(mode="json", exclude_none=True),
        }
        call.configuration_status = "server_managed"
        if commit:
            self.db.commit()

    def connect(self, call_id: UUID) -> CallSession:
        call = self.db.scalar(select(CallSession).where(
            CallSession.id == call_id,
            CallSession.tenant_id == self.tenant_id,
        ).with_for_update())
        if call is None or call.status != "provisioned":
            self.db.rollback()
            raise CallLifecycleError("call_not_provisioned")
        call.status = "connected"
        call.connected_at = datetime.now(timezone.utc)
        call.runtime_state = "idle"
        self.db.commit()
        self.db.refresh(call)
        return call

    def finish(
        self,
        call_id: UUID,
        *,
        status: str = "ended",
        phase: str = "stream_cleanup",
        error_code: str | None = None,
    ) -> None:
        call = self.db.scalar(select(CallSession).where(
            CallSession.id == call_id,
            CallSession.tenant_id == self.tenant_id,
        ).with_for_update())
        if call is None or call.status in TERMINAL_CALL_STATUSES:
            self.db.rollback()
            return
        call.status = status
        call.ended_at = datetime.now(timezone.utc)
        call.failure_phase = phase
        call.error_code = error_code
        call.runtime_state = status
        self.db.commit()
