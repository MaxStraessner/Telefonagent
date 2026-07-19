from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.db.session import engine


def test_migration_created_all_tables():
    command.upgrade(Config("alembic.ini"), "head")
    tables = set(inspect(engine).get_table_names())
    assert {"tenants", "tenant_settings", "locations", "services", "staff_members", "appointments", "call_sessions", "tool_executions"} <= tables

