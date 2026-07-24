from sqlalchemy import inspect, select

from app.db.session import engine
from app.models import AppUser


def test_authentication_schema_and_backfill_are_present(db):
    inspector = inspect(engine)
    assert {
        "user_sessions",
        "authentication_rate_limits",
        "tenant_inbound_routes",
    }.issubset(inspector.get_table_names())
    user_columns = {column["name"] for column in inspector.get_columns("app_users")}
    assert {
        "username",
        "normalized_username",
        "password_hash",
        "is_platform_admin",
        "last_login_at",
        "password_changed_at",
    }.issubset(user_columns)
    owner = db.scalar(
        select(AppUser).where(
            AppUser.normalized_username == "owner@telefonagent.local"
        )
    )
    assert owner is not None
    assert owner.password_hash.startswith("$argon2id$")
    assert "m=19456,t=2,p=1" in owner.password_hash
