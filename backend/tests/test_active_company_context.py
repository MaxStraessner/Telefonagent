from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.main import app
from app.models import CallChannel, CallSession, Service, Tenant
from app.services.provisioning import ProvisioningService

ORIGIN_HEADERS = {"Origin": "http://testserver", "X-Requested-With": "Telefonagent"}


def _platform_login() -> TestClient:
    result = TestClient(app)
    response = result.post(
        "/api/v1/auth/login",
        json={
            "username": "owner@telefonagent.local",
            "password": "correct horse battery staple",
        },
        headers=ORIGIN_HEADERS,
    )
    assert response.status_code == 200 and response.json()["mode"] == "platform"
    return result


def _csrf(client: TestClient) -> dict[str, str]:
    return {
        "Origin": "http://testserver",
        "X-CSRF-Token": client.cookies.get("telefonagent_csrf"),
    }


def test_context_rotation_reloads_only_target_company_and_hides_foreign_identifiers(
    client, db
):
    tenant_a, _user_a = ProvisioningService(db).provision_tenant(
        slug="active-context-alpha",
        name="Active Context Alpha",
        industry="services",
        timezone_name="Europe/Berlin",
        username="active-context-alpha-admin",
        display_name="Active Context Alpha Admin",
        email="active-context-alpha@example.test",
        password="active context alpha password long enough",
    )
    tenant_b, _user_b = ProvisioningService(db).provision_tenant(
        slug="active-context-beta",
        name="Active Context Beta",
        industry="services",
        timezone_name="Europe/Berlin",
        username="active-context-beta-admin",
        display_name="Active Context Beta Admin",
        email="active-context-beta@example.test",
        password="active context beta password long enough",
    )
    service_a = Service(
        tenant_id=tenant_a.id,
        name="Only Context Alpha",
        description="",
        duration_minutes=30,
        is_active=True,
    )
    service_b = Service(
        tenant_id=tenant_b.id,
        name="Only Context Beta",
        description="",
        duration_minutes=45,
        is_active=True,
    )
    attempt_id = uuid4()
    foreign_call = CallSession(
        tenant_id=tenant_a.id,
        call_attempt_id=attempt_id,
        channel=CallChannel.browser,
        status="provisioned",
        configuration_status="sdk_managed",
        runtime_state="idle",
        bootstrap_status="ready",
    )
    db.add_all([service_a, service_b, foreign_call])
    db.commit()

    assert client.post(
        "/api/v1/auth/context", json={"company_id": str(tenant_a.id)}
    ).status_code == 200

    old_session = client.cookies.get("telefonagent_session")
    old_csrf = client.cookies.get("telefonagent_csrf")
    switched = client.post(
        "/api/v1/auth/context", json={"company_id": str(tenant_b.id)}
    )
    assert switched.status_code == 200
    assert switched.json()["active_company"]["id"] == str(tenant_b.id)
    assert client.cookies.get("telefonagent_session") != old_session
    assert client.cookies.get("telefonagent_csrf") != old_csrf
    assert client.get("/api/v1/tenant").json()["name"] == "Active Context Beta"
    assert [item["name"] for item in client.get("/api/v1/services").json()] == [
        "Only Context Beta"
    ]
    assert client.put(
        f"/api/v1/services/{service_a.id}",
        json={
            "name": "Tampered",
            "description": "",
            "duration_minutes": 30,
            "is_active": True,
        },
    ).status_code == 404
    assert client.post(
        f"/api/v1/realtime/call-attempts/{attempt_id}/connected"
    ).status_code == 404
    assert client.post(
        "/api/v1/calendar/conversation/bootstrap",
        json={"session_id": str(foreign_call.id)},
    ).status_code == 404
    db.expire_all()
    assert db.get(Service, service_a.id).name == "Only Context Alpha"
    assert db.get(CallSession, foreign_call.id).status == "provisioned"


def test_suspended_and_archived_companies_revoke_sessions_and_block_all_features(
    client, db
):
    tenant, _user = ProvisioningService(db).provision_tenant(
        slug="blocked-context-company",
        name="Blocked Context Company",
        industry="services",
        timezone_name="Europe/Berlin",
        username="blocked-context-admin",
        display_name="Blocked Context Admin",
        email="blocked-context-admin@example.test",
        password="blocked context admin password long enough",
    )
    assert client.post(
        "/api/v1/auth/context", json={"company_id": str(tenant.id)}
    ).status_code == 200

    platform_client = _platform_login()
    suspended = platform_client.post(
        f"/api/v1/platform/companies/{tenant.id}/status",
        json={"status": "suspended"},
        headers=_csrf(platform_client),
    )
    assert suspended.status_code == 200
    assert client.get("/api/v1/auth/session").status_code == 401

    selected = platform_client.post(
        "/api/v1/auth/context",
        json={"company_id": str(tenant.id)},
        headers=_csrf(platform_client),
    )
    assert selected.status_code == 200
    assert platform_client.get("/api/v1/tenant").status_code == 403
    assert platform_client.post(
        "/api/v1/realtime/client-secret", headers=_csrf(platform_client)
    ).status_code == 403
    assert platform_client.post(
        "/api/v1/calendar/oauth/google/start", headers=_csrf(platform_client)
    ).status_code == 403

    manager = _platform_login()
    archived = manager.post(
        f"/api/v1/platform/companies/{tenant.id}/status",
        json={"status": "archived"},
        headers=_csrf(manager),
    )
    assert archived.status_code == 200
    assert manager.get(f"/api/v1/platform/companies/{tenant.id}").status_code == 200
    db.expire_all()
    assert db.get(Tenant, UUID(str(tenant.id))).status.value == "archived"
