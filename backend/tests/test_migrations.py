from sqlalchemy import inspect

from alembic import command
from alembic.config import Config
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
    } <= tables


def test_migrations_can_roundtrip_and_seed_existing_demo_configuration():
    config = Config("alembic.ini")
    command.downgrade(config, "base")
    assert "agent_configurations" not in set(inspect(engine).get_table_names())
    command.upgrade(config, "head")
    with SessionLocal() as db:
        tenant = seed_database(db)
        assert tenant.settings.assistant_name == "Lina"

