from uuid import UUID

from sqlalchemy import func, select

from app.api.v1.auth import get_mail_adapter
from app.core.security import verify_password
from app.main import app
from app.models import AppUser, AuditLog, PlatformRole, Tenant, TenantMembership, TenantRole, TenantStatus
from app.services.mail import MailDeliveryError, OutboundMail

ORIGIN_HEADERS = {"Origin": "http://testserver", "X-Requested-With": "Telefonagent"}
OWNER_PASSWORD = "correct horse battery staple"


class FakeMailer:
    def __init__(self, fail: bool = False):
        self.fail = fail
        self.messages: list[OutboundMail] = []

    def send(self, message: OutboundMail) -> None:
        if self.fail:
            raise MailDeliveryError("simulated")
        self.messages.append(message)


def _invitation_token(message: OutboundMail) -> str:
    return message.text.strip().rsplit("/", 1)[-1]


def _company_payload(slug: str, *, delivery: str = "invitation") -> dict:
    first_admin = {
        "username": f"admin-{slug}",
        "display_name": f"Admin {slug}",
        "email": f"admin-{slug}@example.test",
        "delivery": delivery,
    }
    if delivery == "temporary_password":
        first_admin["temporary_password"] = f"temporary password for {slug} long"
    return {
        "slug": slug,
        "name": f"Company {slug}",
        "legal_name": f"Company {slug} GmbH",
        "industry": "services",
        "timezone": "Europe/Berlin",
        "contact_name": "Company Contact",
        "contact_email": f"contact-{slug}@example.test",
        "contact_phone": "+49 30 123456",
        "status": "trial",
        "is_demo": False,
        "first_admin": first_admin,
    }


def _csrf_headers(test_client) -> dict:
    return {
        "Origin": "http://testserver",
        "X-CSRF-Token": test_client.cookies.get("telefonagent_csrf"),
    }


def test_platform_creates_company_atomically_and_suspension_revokes_company_session(
    client, anonymous_client, db
):
    mailer = FakeMailer()
    app.dependency_overrides[get_mail_adapter] = lambda: mailer
    created = client.post(
        "/api/v1/platform/companies", json=_company_payload("phase3-atomic")
    )
    assert created.status_code == 201, created.text
    company = created.json()
    assert company["onboarding_complete"] is False
    assert company["active_user_count"] == 0
    assert len(mailer.messages) == 1
    assert "temporary password" not in mailer.messages[0].text.lower()
    assert client.post(
        f"/api/v1/platform/companies/{company['id']}/status",
        json={"status": "active"},
    ).status_code == 409

    token = _invitation_token(mailer.messages[0])
    accepted = anonymous_client.post(
        f"/api/v1/auth/invitations/{token}",
        json={"password": "accepted phase three admin password"},
        headers=ORIGIN_HEADERS,
    )
    assert accepted.status_code == 204, accepted.text
    detail = client.get(f"/api/v1/platform/companies/{company['id']}")
    assert detail.status_code == 200
    assert detail.json()["onboarding_complete"] is True

    company_client = anonymous_client
    login = company_client.post(
        "/api/v1/auth/login",
        json={
            "username": "admin-phase3-atomic@example.test",
            "password": "accepted phase three admin password",
        },
        headers=ORIGIN_HEADERS,
    )
    assert login.status_code == 200
    assert company_client.get("/api/v1/company").status_code == 200
    assert company_client.get("/api/v1/platform/companies").status_code == 403

    suspended = client.post(
        f"/api/v1/platform/companies/{company['id']}/status",
        json={"status": "suspended"},
    )
    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"
    assert company_client.get("/api/v1/auth/session").status_code == 401
    tenant = db.get(Tenant, UUID(company["id"]))
    assert tenant is not None and tenant.status == TenantStatus.suspended
    assert db.scalar(
        select(AuditLog.id).where(
            AuditLog.tenant_id == tenant.id,
            AuditLog.action == "platform.company.status_changed",
        )
    ) is not None


def test_failed_company_invitation_rolls_back_the_whole_company(client, db):
    app.dependency_overrides[get_mail_adapter] = lambda: FakeMailer(fail=True)
    response = client.post(
        "/api/v1/platform/companies", json=_company_payload("phase3-mail-failure")
    )
    assert response.status_code == 503
    assert db.scalar(select(Tenant.id).where(Tenant.slug == "phase3-mail-failure")) is None


def test_only_owner_manages_platform_admins_with_reauthentication(
    client, anonymous_client, db
):
    mailer = FakeMailer()
    app.dependency_overrides[get_mail_adapter] = lambda: mailer
    wrong_password = client.post(
        "/api/v1/platform/admins/invitations",
        json={
            "username": "phase3-platform-admin",
            "display_name": "Phase 3 Platform Admin",
            "email": "phase3-platform-admin@example.test",
            "current_password": "definitely wrong password",
        },
    )
    assert wrong_password.status_code == 403
    invited = client.post(
        "/api/v1/platform/admins/invitations",
        json={
            "username": "phase3-platform-admin",
            "display_name": "Phase 3 Platform Admin",
            "email": "phase3-platform-admin@example.test",
            "current_password": OWNER_PASSWORD,
        },
    )
    assert invited.status_code == 201, invited.text
    audit_entry = db.scalar(
        select(AuditLog)
        .where(AuditLog.action == "platform.admin.invitation.created")
        .order_by(AuditLog.created_at.desc())
    )
    assert audit_entry is not None
    assert "password" not in str(audit_entry.metadata_after).lower()
    assert "token" not in str(audit_entry.metadata_after).lower()
    token = _invitation_token(mailer.messages[-1])
    assert anonymous_client.post(
        f"/api/v1/auth/invitations/{token}",
        json={"password": "phase three platform admin password"},
        headers=ORIGIN_HEADERS,
    ).status_code == 204
    login = anonymous_client.post(
        "/api/v1/auth/login",
        json={
            "username": "phase3-platform-admin",
            "password": "phase three platform admin password",
        },
        headers=ORIGIN_HEADERS,
    )
    assert login.status_code == 200
    assert login.json()["mode"] == "platform"
    assert anonymous_client.get("/api/v1/platform/companies").status_code == 200
    assert anonymous_client.get("/api/v1/platform/admins").status_code == 403

    owner = db.scalar(select(AppUser).where(AppUser.platform_role == PlatformRole.owner))
    assert owner is not None
    protected = client.put(
        f"/api/v1/platform/admins/{owner.id}",
        json={
            "display_name": owner.display_name,
            "email": owner.email,
            "is_active": False,
            "current_password": OWNER_PASSWORD,
        },
    )
    assert protected.status_code == 409
    db.refresh(owner)
    assert owner.platform_role == PlatformRole.owner and owner.is_active is True


def test_primary_and_last_company_admin_are_protected_until_explicit_transfer(
    client, anonymous_client, db
):
    app.dependency_overrides[get_mail_adapter] = lambda: FakeMailer()
    created = client.post(
        "/api/v1/platform/companies",
        json=_company_payload("phase3-primary", delivery="temporary_password"),
    )
    assert created.status_code == 201, created.text
    company_id = created.json()["id"]
    users = client.get(f"/api/v1/platform/companies/{company_id}/users").json()
    first = users[0]
    assert first["is_primary_admin"] is True
    blocked = client.put(
        f"/api/v1/platform/companies/{company_id}/users/{first['id']}",
        json={
            "display_name": first["display_name"],
            "email": first["email"],
            "role": "company_user",
            "is_active": True,
        },
    )
    assert blocked.status_code == 409

    mailer = FakeMailer()
    app.dependency_overrides[get_mail_adapter] = lambda: mailer
    second_invite = client.post(
        f"/api/v1/platform/companies/{company_id}/invitations",
        json={
            "username": "phase3-second-admin",
            "display_name": "Phase 3 Second Admin",
            "email": "phase3-second-admin@example.test",
            "role": "company_admin",
        },
    )
    assert second_invite.status_code == 201
    token = _invitation_token(mailer.messages[0])
    assert anonymous_client.post(
        f"/api/v1/auth/invitations/{token}",
        json={"password": "phase three second admin password"},
        headers=ORIGIN_HEADERS,
    ).status_code == 204
    users = client.get(f"/api/v1/platform/companies/{company_id}/users").json()
    second = next(item for item in users if item["username"] == "phase3-second-admin")
    transferred = client.post(
        f"/api/v1/platform/companies/{company_id}/primary-admin",
        json={"user_id": second["id"]},
    )
    assert transferred.status_code == 200
    assert transferred.json()["is_primary_admin"] is True
    demoted = client.put(
        f"/api/v1/platform/companies/{company_id}/users/{first['id']}",
        json={
            "display_name": first["display_name"],
            "email": first["email"],
            "role": "company_user",
            "is_active": True,
        },
    )
    assert demoted.status_code == 200
    protected_new_primary = client.put(
        f"/api/v1/platform/companies/{company_id}/users/{second['id']}",
        json={
            "display_name": second["display_name"],
            "email": second["email"],
            "role": "company_user",
            "is_active": True,
        },
    )
    assert protected_new_primary.status_code == 409
    primary_count = db.scalar(
        select(func.count(TenantMembership.id)).where(
            TenantMembership.tenant_id == UUID(company_id),
            TenantMembership.is_active.is_(True),
            TenantMembership.role == TenantRole.company_admin,
            TenantMembership.is_primary_admin.is_(True),
        )
    )
    assert primary_count == 1


def test_platform_admin_creates_direct_company_users_without_email(
    client, anonymous_client, db
):
    company_payload = _company_payload("direct-users", delivery="temporary_password")
    company_payload["first_admin"]["email"] = None
    created = client.post("/api/v1/platform/companies", json=company_payload)
    assert created.status_code == 201, created.text
    assert created.json()["onboarding_complete"] is True
    company_id = created.json()["id"]

    credentials = {
        "direct-company-admin": ("company_admin", "direct admin starting password"),
        "direct-company-member": ("company_user", "direct member starting password"),
    }
    created_users = []
    for username, (role, password) in credentials.items():
        response = client.post(
            f"/api/v1/platform/companies/{company_id}/users",
            json={
                "username": username,
                "display_name": username.replace("-", " ").title(),
                "email": None,
                "role": role,
                "password": password,
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["role"] == role
        assert response.json()["email"] is None
        assert response.json()["must_change_password"] is True
        created_users.append(response.json())

    listed = client.get(f"/api/v1/platform/companies/{company_id}/users")
    assert listed.status_code == 200
    assert {item["username"] for item in listed.json()}.issuperset(credentials)

    duplicate = client.post(
        f"/api/v1/platform/companies/{company_id}/users",
        json={
            "username": "direct-company-member",
            "display_name": "Duplicate",
            "email": None,
            "role": "company_user",
            "password": "another sufficiently long password",
        },
    )
    assert duplicate.status_code == 409
    invalid_role = client.post(
        f"/api/v1/platform/companies/{company_id}/users",
        json={
            "username": "direct-invalid-role",
            "display_name": "Invalid Role",
            "email": None,
            "role": "owner",
            "password": "another sufficiently long password",
        },
    )
    assert invalid_role.status_code == 422

    tenant_id = UUID(company_id)
    for username, (role, password) in credentials.items():
        user = db.scalar(select(AppUser).where(AppUser.username == username))
        assert user is not None
        assert user.password_hash != password
        assert user.password_hash.startswith("$argon2")
        assert verify_password(password, user.password_hash)[0] is True
        assert user.must_change_password is True
        assert user.platform_role is None
        membership = db.scalar(
            select(TenantMembership).where(
                TenantMembership.tenant_id == tenant_id,
                TenantMembership.user_id == user.id,
            )
        )
        assert membership is not None and membership.role == TenantRole(role)

    audit_count = db.scalar(
        select(func.count(AuditLog.id)).where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.action == "company.user.created",
        )
    )
    assert audit_count == 3
    latest_audit = db.scalar(
        select(AuditLog)
        .where(
            AuditLog.tenant_id == tenant_id,
            AuditLog.action == "company.user.created",
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert latest_audit is not None
    assert "password" not in str(latest_audit.metadata_after).lower()

    login = anonymous_client.post(
        "/api/v1/auth/login",
        json={
            "username": "direct-company-admin",
            "password": credentials["direct-company-admin"][1],
        },
        headers=ORIGIN_HEADERS,
    )
    assert login.status_code == 200
    assert login.json()["user"]["must_change_password"] is True
    assert anonymous_client.get("/api/v1/platform/companies").status_code == 403
    changed = anonymous_client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": credentials["direct-company-admin"][1],
            "new_password": "direct admin changed password",
        },
        headers=_csrf_headers(anonymous_client),
    )
    assert changed.status_code == 200, changed.text
    assert anonymous_client.get("/api/v1/company").status_code == 200
    assert anonymous_client.get("/api/v1/platform/companies").status_code == 403


def test_owner_creates_direct_platform_admin_with_reauthentication(
    client, anonymous_client, db
):
    payload = {
        "username": "direct-platform-admin",
        "display_name": "Direct Platform Admin",
        "email": None,
        "password": "direct platform starting password",
        "current_password": OWNER_PASSWORD,
    }
    wrong = client.post(
        "/api/v1/platform/admins",
        json={**payload, "current_password": "wrong owner password"},
    )
    assert wrong.status_code == 403

    created = client.post("/api/v1/platform/admins", json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["platform_role"] == "admin"
    assert created.json()["email"] is None
    assert created.json()["must_change_password"] is True

    user = db.scalar(select(AppUser).where(AppUser.username == payload["username"]))
    assert user is not None and user.platform_role == PlatformRole.admin
    assert verify_password(payload["password"], user.password_hash)[0] is True
    audit = db.scalar(
        select(AuditLog)
        .where(AuditLog.action == "platform.admin.created")
        .order_by(AuditLog.created_at.desc())
    )
    assert audit is not None
    assert "password" not in str(audit.metadata_after).lower()

    login = anonymous_client.post(
        "/api/v1/auth/login",
        json={"username": payload["username"], "password": payload["password"]},
        headers=ORIGIN_HEADERS,
    )
    assert login.status_code == 200
    assert login.json()["user"]["must_change_password"] is True
    assert anonymous_client.get("/api/v1/platform/companies").status_code == 403
    changed = anonymous_client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": payload["password"],
            "new_password": "direct platform changed password",
        },
        headers=_csrf_headers(anonymous_client),
    )
    assert changed.status_code == 200, changed.text
    assert anonymous_client.get("/api/v1/platform/companies").status_code == 200
    assert anonymous_client.get("/api/v1/platform/admins").status_code == 403
