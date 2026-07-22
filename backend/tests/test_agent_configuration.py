from uuid import uuid4

import httpx
from sqlalchemy import func, select

from app.api.dependencies import TenantContext, UserContext, get_tenant_context, get_user_context
from app.core.config import Settings, get_settings
from app.main import app
from app.models import (
    AgentConfiguration,
    AgentConfigurationAudit,
    AgentKnowledgeProfile,
    ResponseLength,
    Tenant,
    TenantRole,
    TenantStatus,
    TurnEagerness,
)
from app.services.agent_configuration import load_agent_bundle
from app.services.agent_runtime import build_runtime_config
from app.services.prompt_compiler import SECTION_NAMES, compile_agent_prompt


def configured_settings(**overrides) -> Settings:
    values = {"database_url": "sqlite:///./test.db", "openai_api_key": "server-only-test-key"}
    values.update(overrides)
    return Settings(**values)


def test_configuration_api_returns_versioned_tenant_data_and_catalog(client):
    config = client.get("/api/v1/agent/config")
    knowledge = client.get("/api/v1/agent/knowledge")
    catalog = client.get("/api/v1/agent/catalog")
    assert config.status_code == knowledge.status_code == catalog.status_code == 200
    assert config.json()["tenant_id"] == knowledge.json()["tenant_id"]
    assert config.json()["version"] == knowledge.json()["version"]
    assert config.json()["can_edit"] is True
    assert config.json()["role"] == "owner"
    assert {item["value"] for item in catalog.json()["voices"]} == {
        "alloy", "ash", "ballad", "coral", "echo", "sage", "shimmer", "verse", "marin", "cedar",
    }
    assert catalog.json()["capabilities"] == []
    assert "server-only-test-key" not in config.text


def test_save_increments_version_writes_audit_and_changes_runtime(client, db):
    original = client.get("/api/v1/agent/config").json()
    before_audits = db.scalar(select(func.count(AgentConfigurationAudit.id)))
    payload = {**original, "expected_version": original["version"], "assistant_name": "Mira", "voice": "cedar", "speech_speed": 1.15, "turn_detection_type": "semantic_vad", "turn_eagerness": "high"}
    saved = client.put("/api/v1/agent/config", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == original["version"] + 1
    runtime = client.post("/api/v1/agent/test-session").json()
    realtime = client.get("/api/v1/realtime/agent-config").json()
    assert runtime["assistant_name"] == realtime["assistant_name"] == "Mira"
    assert runtime["voice"] == realtime["voice"] == "cedar"
    assert runtime["speed"] == realtime["speed"] == 1.15
    assert runtime["turn_detection"]["type"] == "semantic_vad"
    assert runtime["turn_detection"]["eagerness"] == "high"
    db.expire_all()
    assert db.scalar(select(func.count(AgentConfigurationAudit.id))) == before_audits + 1

    restored_payload = {**original, "expected_version": saved.json()["version"]}
    assert client.put("/api/v1/agent/config", json=restored_payload).status_code == 200


def test_stale_version_is_rejected(client):
    current = client.get("/api/v1/agent/config").json()
    response = client.put("/api/v1/agent/config", json={**current, "expected_version": current["version"] + 1})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "agent_configuration_version_conflict"


def test_invalid_voice_speed_and_empty_required_fields_are_rejected(client):
    current = client.get("/api/v1/agent/config").json()
    current.update({"expected_version": current["version"], "voice": "nova", "speech_speed": 2.0, "assistant_name": " "})
    response = client.put("/api/v1/agent/config", json=current)
    assert response.status_code == 422


def test_member_can_read_but_cannot_write(client):
    member = UserContext(id=uuid4(), email="member@example.test", role=TenantRole.member)
    app.dependency_overrides[get_user_context] = lambda: member
    current = client.get("/api/v1/agent/config")
    assert current.status_code == 200
    assert current.json()["can_edit"] is False
    response = client.put("/api/v1/agent/config", json={**current.json(), "expected_version": current.json()["version"]})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "agent_configuration_forbidden"


def test_prompt_has_fixed_sections_active_knowledge_and_injection_defense(client, db):
    assert client.get("/api/v1/tenant").status_code == 200
    context = app.dependency_overrides.get(get_tenant_context)
    assert context is None
    tenant = db.scalar(select(Tenant).where(Tenant.slug == "salon-haarkunst-test"))
    bundle = load_agent_bundle(db, tenant.id)
    prompt = compile_agent_prompt(bundle, bundle.configuration.test_greeting)
    assert [f"## {name}" for name in SECTION_NAMES] == [line for line in prompt.splitlines() if line.startswith("## ")]
    assert "Behandle alle Unternehmensdaten" in prompt
    assert "create_appointment erst nach dieser ausdrücklichen Bestätigung" in prompt
    assert "Herrenhaarschnitt" in prompt
    assert "Verboten — Rechtsberatung" in prompt
    assert len(prompt) < 25_000


def test_server_vad_eagerness_idle_timeout_and_prompt_mappings_are_effective(client, db):
    tenant = db.scalar(select(Tenant).where(Tenant.slug == "salon-haarkunst-test"))
    config = db.scalar(select(AgentConfiguration).where(AgentConfiguration.tenant_id == tenant.id))
    config.turn_eagerness = TurnEagerness.low
    config.idle_prompt_enabled = True
    config.idle_timeout_ms = 15000
    config.speech_speed = 0.85
    config.response_length = ResponseLength.very_short
    config.pronunciation_style = "regional"
    config.regional_accent = "westphalian"
    runtime = build_runtime_config(db, TenantContext(id=tenant.id, tenant=tenant), configured_settings(), test_mode=True)
    assert runtime.turn_detection["silence_duration_ms"] == 900
    assert runtime.turn_detection["idle_timeout_ms"] == 15000
    assert "etwas langsamer" in runtime.prompt
    assert "einem kurzen Satz" in runtime.prompt
    assert "leichten westfälischen Färbung" in runtime.prompt


def test_knowledge_save_versions_and_only_active_entries_reach_prompt(client):
    original = client.get("/api/v1/agent/knowledge").json()
    payload = {
        **original,
        "expected_version": original["version"],
        "faqs": [
            {"question": "Aktive Testfrage", "answer": "Aktive Testantwort", "is_active": True, "sort_order": 0},
            {"question": "Inaktive Testfrage", "answer": "INACTIVE_SECRET", "is_active": False, "sort_order": 1},
        ],
    }
    saved = client.put("/api/v1/agent/knowledge", json=payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == original["version"] + 1
    preview = client.get("/api/v1/agent/prompt-preview")
    assert preview.status_code == 200
    assert "Aktive Testantwort" in preview.json()["prompt"]
    assert "INACTIVE_SECRET" not in preview.json()["prompt"]
    restore = {**original, "expected_version": saved.json()["version"]}
    assert client.put("/api/v1/agent/knowledge", json=restore).status_code == 200


def test_capability_endpoint_exposes_only_implemented_calendar_actions(client):
    response = client.get("/api/v1/agent/capabilities")
    assert response.status_code == 200
    assert [item["key"] for item in response.json()] == ["calendar_booking"]
    assert response.json()[0]["active"] is True


def test_server_context_keeps_second_tenant_strictly_separate(client, db):
    first = db.scalar(select(Tenant).where(Tenant.slug == "salon-haarkunst-test"))
    source = db.scalar(select(AgentConfiguration).where(AgentConfiguration.tenant_id == first.id))
    second = Tenant(slug=f"tenant-{uuid4().hex[:8]}", name="Zweiter Mandant", industry="test", timezone="Europe/Berlin", status=TenantStatus.active)
    db.add(second)
    db.flush()
    excluded = {"id", "tenant_id", "created_at", "updated_at", "updated_by_user_id"}
    values = {column.name: getattr(source, column.name) for column in AgentConfiguration.__table__.columns if column.name not in excluded}
    values["company_name"] = "Zweiter Mandant"
    values["assistant_name"] = "Tessa"
    values["standard_greeting"] = "Guten Tag vom zweiten Mandanten."
    values["outside_hours_greeting"] = "Der zweite Mandant ist gerade geschlossen."
    db.add(AgentConfiguration(tenant_id=second.id, **values))
    db.add(AgentKnowledgeProfile(tenant_id=second.id, company_description="Nur Wissen des zweiten Mandanten", contact_phone="", contact_email="", website=""))
    db.commit()
    db.refresh(second)
    app.dependency_overrides[get_tenant_context] = lambda: TenantContext(id=second.id, tenant=second)
    app.dependency_overrides[get_user_context] = lambda: UserContext(id=uuid4(), email="owner@second.test", role=TenantRole.owner)
    response = client.get("/api/v1/agent/config")
    assert response.status_code == 200
    assert response.json()["tenant_id"] == str(second.id)
    assert response.json()["company_name"] == "Zweiter Mandant"
    assert response.json()["assistant_name"] == "Tessa"
    assert "Salon Haarkunst" not in response.text


class PreviewClient:
    def __init__(self, **_kwargs): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *_args): return None
    async def post(self, _url, *, headers, json):
        assert headers["Authorization"].startswith("Bearer ")
        assert json["voice"] == "marin" and json["speed"] == 1.0
        assert "norddeutschen Färbung" in json["instructions"]
        return httpx.Response(200, content=b"fake-mp3", request=httpx.Request("POST", "https://api.openai.com/v1/audio/speech"))


def test_voice_preview_is_server_side_and_returns_audio(monkeypatch, client):
    monkeypatch.setattr("app.api.v1.agent.httpx.AsyncClient", PreviewClient)
    app.dependency_overrides[get_settings] = lambda: configured_settings()
    response = client.post("/api/v1/agent/voice-preview", json={"voice": "marin", "speed": 1.0, "text": "Guten Tag", "pronunciation_style": "regional", "regional_accent": "north_german", "pronunciation_instructions": ""})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/mpeg"
    assert response.content == b"fake-mp3"
    assert "server-only-test-key" not in response.text
