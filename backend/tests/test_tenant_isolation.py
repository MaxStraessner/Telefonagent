from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import Service
from app.services.provisioning import ProvisioningService


def _provision(db, suffix: str):
    return ProvisioningService(db).provision_tenant(
        slug=f"isolation-{suffix}",
        name=f"Isolation {suffix}",
        industry="services",
        timezone_name="Europe/Berlin",
        username=f"isolation-{suffix}",
        display_name=f"Isolation {suffix}",
        email=f"isolation-{suffix}@example.test",
        password=f"a sufficiently strong password {suffix}",
    )


def _login(username: str, password: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers={
            "Origin": "http://testserver",
            "X-Requested-With": "Telefonagent",
        },
    )
    assert response.status_code == 200
    return client


def test_foreign_ids_are_hidden_and_lists_are_tenant_scoped(db):
    tenant_a, _ = _provision(db, "alpha")
    tenant_b, _ = _provision(db, "beta")
    service_a = Service(
        tenant_id=tenant_a.id,
        name="Only Alpha",
        description="",
        duration_minutes=30,
        is_active=True,
    )
    service_b = Service(
        tenant_id=tenant_b.id,
        name="Only Beta",
        description="",
        duration_minutes=30,
        is_active=True,
    )
    db.add_all([service_a, service_b])
    db.commit()

    client = _login("isolation-beta", "a sufficiently strong password beta")
    response = client.get("/api/v1/services")
    assert response.status_code == 200
    assert [item["name"] for item in response.json()] == ["Only Beta"]
    csrf = client.cookies.get("telefonagent_csrf")
    foreign_update = client.put(
        f"/api/v1/services/{service_a.id}",
        json={
            "name": "Tampered",
            "description": "",
            "duration_minutes": 30,
            "is_active": True,
        },
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
    )
    assert foreign_update.status_code == 404
    db.expire_all()
    assert db.scalar(select(Service).where(Service.id == service_a.id)).name == "Only Alpha"
