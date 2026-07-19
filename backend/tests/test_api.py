from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.main import app
from app.models import Service, StaffMember, Tenant, TenantStatus
from app.seed import seed_database


def test_health_reports_database(client):
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy", "database": "connected"}


def test_active_tenant_response_model(client):
    response = client.get("/api/v1/tenant")
    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "Salon Haarkunst Test"
    assert payload["settings"]["assistant_name"] == "Lina"
    assert payload["primary_location"]["timezone"] == "Europe/Berlin"


def test_services_are_tenant_scoped(client, db):
    other = Tenant(slug="other", name="Other", industry="other", timezone="UTC", status=TenantStatus.active)
    db.add(other)
    db.flush()
    db.add(Service(tenant_id=other.id, name="Hidden", description="", duration_minutes=10, is_active=True))
    db.commit()
    payload = client.get("/api/v1/services").json()
    assert len(payload) == 3
    assert "Hidden" not in {item["name"] for item in payload}


def test_staff_are_tenant_scoped(client, db):
    other = db.scalar(select(Tenant).where(Tenant.slug == "other"))
    db.add(StaffMember(tenant_id=other.id, display_name="Hidden", role_name="Test", is_active=True))
    db.commit()
    payload = client.get("/api/v1/staff").json()
    assert {item["display_name"] for item in payload} == {"Anna", "Ben"}


def test_appointments_start_empty(client):
    response = client.get("/api/v1/appointments")
    assert response.status_code == 200
    assert response.json() == []


def test_seed_is_idempotent(db):
    seed_database(db)
    seed_database(db)
    tenant = db.scalar(select(Tenant).where(Tenant.slug == "salon-haarkunst-test"))
    assert db.scalar(select(func.count(Service.id)).where(Service.tenant_id == tenant.id)) == 3
    assert db.scalar(select(func.count(StaffMember.id)).where(StaffMember.tenant_id == tenant.id)) == 2


def test_platform_status_without_openai_key(client):
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="sqlite:///./test.db", openai_api_key=None
    )
    payload = client.get("/api/v1/platform/status").json()
    assert payload["realtime_voice_configured"] is False
    assert payload["database_connected"] is True


def test_unknown_active_tenant_is_controlled_error(client):
    app.dependency_overrides[get_settings] = lambda: Settings(
        database_url="sqlite:///./test.db", active_tenant_slug="missing"
    )
    response = client.get("/api/v1/tenant")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "active_tenant_unavailable"


def test_openapi_contains_typed_response_contracts(client):
    schema = client.get("/openapi.json").json()
    tenant_response = schema["paths"]["/api/v1/tenant"]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
    assert tenant_response["$ref"].endswith("/TenantResponse")
    assert "ServiceResponse" in schema["components"]["schemas"]
    assert "AppointmentResponse" in schema["components"]["schemas"]
