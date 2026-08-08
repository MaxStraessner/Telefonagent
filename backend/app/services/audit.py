import hashlib
import hmac
from collections.abc import Mapping
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import AuditLog, PlatformRole

ALLOWED_AUDIT_FIELDS = frozenset(
    {
        "username",
        "display_name",
        "email",
        "role",
        "platform_role",
        "status",
        "is_active",
        "is_primary_admin",
        "is_demo",
        "name",
        "legal_name",
        "slug",
        "delivery_status",
    }
)


def redacted_metadata(values: Mapping[str, object] | None) -> dict | None:
    if not values:
        return None
    return {key: values[key] for key in ALLOWED_AUDIT_FIELDS if key in values}


class AuditService:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings

    def record(
        self,
        *,
        actor_user_id: UUID | None,
        platform_role: PlatformRole | None,
        tenant_id: UUID | None,
        action: str,
        target_type: str,
        target_id: object | None,
        before: Mapping[str, object] | None = None,
        after: Mapping[str, object] | None = None,
        outcome: str = "success",
        request_id: str | None = None,
        client_ip: str | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            actor_user_id=actor_user_id,
            platform_role=platform_role.value if platform_role else None,
            tenant_id=tenant_id,
            action=action,
            target_type=target_type,
            target_id=str(target_id) if target_id is not None else None,
            outcome=outcome,
            metadata_before=redacted_metadata(before),
            metadata_after=redacted_metadata(after),
            request_id=request_id[:100] if request_id else None,
            ip_hash=self._ip_hash(client_ip),
        )
        self.db.add(entry)
        return entry

    def _ip_hash(self, client_ip: str | None) -> str | None:
        if not client_ip:
            return None
        return hmac.new(
            self.settings.auth_hmac_secret.encode("utf-8"),
            client_ip.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
