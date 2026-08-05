from uuid import uuid4

from sqlalchemy import create_engine, inspect, text

from alembic import command
from alembic.config import Config
from app.core.config import get_settings
from app.db.session import SessionLocal, engine
from app.seed import seed_database


def test_migration_created_all_tables():
    command.upgrade(Config("alembic.ini"), "head")
    tables = set(inspect(engine).get_table_names())
    assert {
        "tenants", "tenant_settings", "locations", "services", "staff_members",
        "appointments", "call_sessions", "tool_executions", "app_users",
        "tenant_memberships", "agent_configurations", "agent_topics",
        "agent_behavior_rules", "agent_knowledge_profiles", "agent_faqs",
        "agent_knowledge_services", "agent_business_hours", "agent_capabilities",
        "agent_configuration_audits",
        "calendar_connections", "calendar_oauth_states", "external_calendars",
        "booking_configurations", "calendar_business_hours", "calendar_appointment_types",
        "calendar_bookings",
    } <= tables


def test_migrations_can_roundtrip_and_seed_existing_demo_configuration():
    config = Config("alembic.ini")
    command.downgrade(config, "base")
    assert "agent_configurations" not in set(inspect(engine).get_table_names())
    command.upgrade(config, "head")
    with SessionLocal() as db:
        tenant = seed_database(db)
        assert tenant.settings.assistant_name == "Lina"


def test_realtime_lifecycle_migration_reconciles_legacy_active_rows(
    tmp_path, monkeypatch
):
    database_path = tmp_path / "realtime-migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("MIGRATION_DATABASE_URL", database_url)
    get_settings.cache_clear()
    config = Config("alembic.ini")
    command.upgrade(config, "0012")
    isolated_engine = create_engine(database_url)
    tenant_id = uuid4()
    session_id = uuid4()
    user_id = uuid4()
    membership_id = uuid4()
    with isolated_engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO tenants (
                    id, slug, name, industry, timezone, status
                ) VALUES (
                    :id, 'legacy-realtime', 'Legacy Realtime',
                    'services', 'Europe/Berlin', 'active'
                )
                """
            ),
            {"id": tenant_id.hex},
        )
        connection.execute(
            text(
                """
                INSERT INTO app_users (
                    id, email, display_name, is_active, username,
                    normalized_username, password_hash, is_platform_admin
                ) VALUES (
                    :id, 'legacy-owner@example.test', 'Legacy Owner', 1,
                    'legacy-owner', 'legacy-owner', '!unusable!', 0
                )
                """
            ),
            {"id": user_id.hex},
        )
        connection.execute(
            text(
                """
                INSERT INTO tenant_memberships (
                    id, tenant_id, user_id, role, is_active
                ) VALUES (
                    :id, :tenant_id, :user_id, 'owner', 1
                )
                """
            ),
            {
                "id": membership_id.hex,
                "tenant_id": tenant_id.hex,
                "user_id": user_id.hex,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO call_sessions (
                    id, tenant_id, channel, status
                ) VALUES (
                    :id, :tenant_id, 'browser', 'active'
                )
                """
            ),
            {"id": session_id.hex, "tenant_id": tenant_id.hex},
        )

    command.upgrade(config, "head")
    with isolated_engine.connect() as connection:
        row = connection.execute(
            text(
                """
                SELECT status, ended_at, failure_phase, error_code
                FROM call_sessions
                WHERE id = :id
                """
            ),
            {"id": session_id.hex},
        ).mappings().one()
    assert row["status"] == "abandoned"
    assert row["ended_at"] is not None
    assert row["failure_phase"] == "migration"
    assert row["error_code"] == "legacy_session_state_reconciled"

    command.downgrade(config, "0012")
    with isolated_engine.connect() as connection:
        row = connection.execute(
            text("SELECT status, ended_at FROM call_sessions WHERE id = :id"),
            {"id": session_id.hex},
        ).mappings().one()
    assert row["status"] == "active"
    assert row["ended_at"] is None
    isolated_engine.dispose()
    get_settings.cache_clear()

