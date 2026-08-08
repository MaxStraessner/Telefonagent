import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.api.v1.auth import get_mail_adapter
from app.core.security import hash_password, sha256_token
from app.main import app
from app.models import (
    AppUser,
    Invitation,
    PasswordResetToken,
    PlatformRole,
    Tenant,
    TenantMembership,
    TenantRole,
)
from app.services.mail import MailDeliveryError, OutboundMail
from app.services.provisioning import ProvisioningService

ORIGIN_HEADERS = {
    "Origin": "http://testserver",
    "X-Requested-With": "Telefonagent",
}


class FakeMailer:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.messages: list[OutboundMail] = []

    def send(self, message: OutboundMail) -> None:
        if self.fail:
            raise MailDeliveryError("simulated")
        self.messages.append(message)


def _raw_token(message: OutboundMail) -> str:
    match = re.search(r"token=([^\s]+)", message.text)
    assert match is not None
    return match.group(1)


def _company_account(db, suffix: str):
    return ProvisioningService(db).provision_tenant(
        slug=f"lifecycle-{suffix}",
        name=f"Lifecycle {suffix}",
        industry="services",
        timezone_name="Europe/Berlin",
        username=f"lifecycle-{suffix}",
        display_name=f"Lifecycle {suffix}",
        email=f"lifecycle-{suffix}@example.test",
        password=f"initial lifecycle password {suffix} long enough",
    )


def test_password_recovery_is_neutral_hashed_one_time_and_revokes_sessions(
    anonymous_client, db
):
    _tenant, user = _company_account(db, "recovery")
    mailer = FakeMailer()
    app.dependency_overrides[get_mail_adapter] = lambda: mailer
    unknown = anonymous_client.post(
        "/api/v1/auth/forgot-password",
        json={"identifier": "unknown@example.test"},
        headers=ORIGIN_HEADERS,
    )
    known = anonymous_client.post(
        "/api/v1/auth/forgot-password",
        json={"identifier": "  LIFECYCLE-RECOVERY@EXAMPLE.TEST "},
        headers=ORIGIN_HEADERS,
    )
    assert unknown.status_code == known.status_code == 202
    assert unknown.content == known.content == b""
    assert len(mailer.messages) == 1
    raw_token = _raw_token(mailer.messages[0])
    stored = db.scalar(
        select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
    )
    assert stored is not None
    assert stored.token_hash == sha256_token(raw_token)
    assert raw_token not in stored.token_hash

    assert anonymous_client.post(
        "/api/v1/auth/login",
        json={
            "username": "lifecycle-recovery",
            "password": "initial lifecycle password recovery long enough",
        },
        headers=ORIGIN_HEADERS,
    ).status_code == 200
    recovered = anonymous_client.post(
        "/api/v1/auth/reset-password",
        json={
            "token": raw_token,
            "new_password": "replacement lifecycle recovery password",
        },
        headers=ORIGIN_HEADERS,
    )
    assert recovered.status_code == 204
    assert anonymous_client.get("/api/v1/auth/session").status_code == 401
    assert anonymous_client.post(
        "/api/v1/auth/reset-password",
        json={
            "token": raw_token,
            "new_password": "second replacement lifecycle password",
        },
        headers=ORIGIN_HEADERS,
    ).status_code == 400
    assert anonymous_client.post(
        "/api/v1/auth/login",
        json={
            "username": "lifecycle-recovery@example.test",
            "password": "replacement lifecycle recovery password",
        },
        headers=ORIGIN_HEADERS,
    ).status_code == 200


def test_password_recovery_expiry_and_delivery_failure_revoke_tokens(
    anonymous_client, db
):
    _tenant, user = _company_account(db, "delivery")
    failing = FakeMailer(fail=True)
    app.dependency_overrides[get_mail_adapter] = lambda: failing
    assert anonymous_client.post(
        "/api/v1/auth/forgot-password",
        json={"identifier": user.email},
        headers=ORIGIN_HEADERS,
    ).status_code == 202
    failed_token = db.scalar(
        select(PasswordResetToken)
        .where(PasswordResetToken.user_id == user.id)
        .order_by(PasswordResetToken.created_at.desc())
    )
    assert failed_token is not None and failed_token.revoked_at is not None

    mailer = FakeMailer()
    app.dependency_overrides[get_mail_adapter] = lambda: mailer
    assert anonymous_client.post(
        "/api/v1/auth/forgot-password",
        json={"identifier": user.email},
        headers=ORIGIN_HEADERS,
    ).status_code == 202
    raw_token = _raw_token(mailer.messages[0])
    token = db.scalar(
        select(PasswordResetToken).where(
            PasswordResetToken.token_hash == sha256_token(raw_token)
        )
    )
    assert token is not None
    token.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert anonymous_client.post(
        "/api/v1/auth/reset-password",
        json={"token": raw_token, "new_password": "expired token password is long"},
        headers=ORIGIN_HEADERS,
    ).status_code == 400


def test_company_invitation_is_hashed_expires_and_can_only_be_accepted_once(
    anonymous_client, db
):
    tenant, creator = _company_account(db, "invitation")
    raw_token = secrets.token_urlsafe(32)
    invitation = Invitation(
        tenant_id=tenant.id,
        created_by_user_id=creator.id,
        email="invited-company-user@example.test",
        normalized_email="invited-company-user@example.test",
        username="invited-company-user",
        display_name="Invited Company User",
        tenant_role=TenantRole.company_user,
        token_hash=sha256_token(raw_token),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
        delivery_status="sent",
    )
    db.add(invitation)
    db.commit()
    preview = anonymous_client.get(
        f"/api/v1/auth/invitations/{raw_token}", headers=ORIGIN_HEADERS
    )
    assert preview.status_code == 200
    assert preview.json()["company_name"] == tenant.name
    assert preview.json()["role"] == "company_user"
    accepted = anonymous_client.post(
        f"/api/v1/auth/invitations/{raw_token}",
        json={"password": "accepted invitation password long enough"},
        headers=ORIGIN_HEADERS,
    )
    assert accepted.status_code == 204
    db.refresh(invitation)
    assert invitation.accepted_at is not None
    user = db.scalar(
        select(AppUser).where(
            AppUser.normalized_email == "invited-company-user@example.test"
        )
    )
    assert user is not None
    membership = db.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant.id,
            TenantMembership.user_id == user.id,
        )
    )
    assert membership is not None and membership.role == TenantRole.company_user
    assert anonymous_client.get(
        f"/api/v1/auth/invitations/{raw_token}", headers=ORIGIN_HEADERS
    ).status_code == 404
    assert anonymous_client.post(
        f"/api/v1/auth/invitations/{raw_token}",
        json={"password": "another invitation password long enough"},
        headers=ORIGIN_HEADERS,
    ).status_code == 400


def test_expired_company_invitation_cannot_be_previewed_or_accepted(
    anonymous_client, db
):
    tenant, creator = _company_account(db, "expired-invitation")
    raw_token = secrets.token_urlsafe(32)
    db.add(
        Invitation(
            tenant_id=tenant.id,
            created_by_user_id=creator.id,
            email="expired-invitation@example.test",
            normalized_email="expired-invitation@example.test",
            username="expired-invitation",
            display_name="Expired Invitation",
            tenant_role=TenantRole.company_user,
            token_hash=sha256_token(raw_token),
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            delivery_status="sent",
        )
    )
    db.commit()
    assert anonymous_client.get(
        f"/api/v1/auth/invitations/{raw_token}", headers=ORIGIN_HEADERS
    ).status_code == 404
    assert anonymous_client.post(
        f"/api/v1/auth/invitations/{raw_token}",
        json={"password": "expired invitation password long enough"},
        headers=ORIGIN_HEADERS,
    ).status_code == 400


def test_platform_admin_invitation_never_creates_an_owner(anonymous_client, db):
    creator = db.scalar(
        select(AppUser).where(AppUser.platform_role == PlatformRole.owner)
    )
    assert creator is not None
    raw_token = secrets.token_urlsafe(32)
    db.add(
        Invitation(
            created_by_user_id=creator.id,
            email="invited-platform-admin@example.test",
            normalized_email="invited-platform-admin@example.test",
            username="invited-platform-admin",
            display_name="Invited Platform Admin",
            platform_role=PlatformRole.admin,
            token_hash=sha256_token(raw_token),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=72),
            delivery_status="sent",
        )
    )
    db.commit()
    assert anonymous_client.post(
        f"/api/v1/auth/invitations/{raw_token}",
        json={"password": "platform invitation password long enough"},
        headers=ORIGIN_HEADERS,
    ).status_code == 204
    invited = db.scalar(
        select(AppUser).where(
            AppUser.normalized_email == "invited-platform-admin@example.test"
        )
    )
    assert invited is not None and invited.platform_role == PlatformRole.admin
    assert db.scalar(
        select(AppUser).where(AppUser.platform_role == PlatformRole.owner)
    ).id == creator.id


def test_must_change_password_blocks_everything_except_auth_lifecycle(
    anonymous_client, db
):
    tenant = db.scalar(
        select(Tenant).where(Tenant.slug == "salon-haarkunst-test")
    )
    assert tenant is not None
    user = AppUser(
        username="must-change-user",
        normalized_username="must-change-user",
        password_hash=hash_password("temporary must change password long"),
        email="must-change@example.test",
        normalized_email="must-change@example.test",
        display_name="Must Change",
        is_active=True,
        must_change_password=True,
    )
    db.add(user)
    db.flush()
    db.add(
        TenantMembership(
            tenant_id=tenant.id,
            user_id=user.id,
            role=TenantRole.company_user,
            is_active=True,
        )
    )
    db.commit()
    assert anonymous_client.post(
        "/api/v1/auth/login",
        json={
            "username": "must-change-user",
            "password": "temporary must change password long",
        },
        headers=ORIGIN_HEADERS,
    ).status_code == 200
    assert anonymous_client.get("/api/v1/auth/session").status_code == 200
    assert anonymous_client.get("/api/v1/tenant").status_code == 403
    csrf = anonymous_client.cookies.get("telefonagent_csrf")
    changed = anonymous_client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": "temporary must change password long",
            "new_password": "permanent changed password long enough",
        },
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
    )
    assert changed.status_code == 200
    assert changed.json()["user"]["must_change_password"] is False
    assert anonymous_client.get("/api/v1/tenant").status_code == 200
