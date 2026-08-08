from sqlalchemy import inspect, select

from app.db.session import engine
from app.models import AppUser, InitialAppSetup, PlatformRole
from app.seed import seed_database


def test_authentication_schema_and_backfill_are_present(db):
    inspector = inspect(engine)
    assert {
        "user_sessions",
        "authentication_rate_limits",
        "tenant_inbound_routes",
        "initial_app_setup",
        "invitations",
        "password_reset_tokens",
        "audit_logs",
    }.issubset(inspector.get_table_names())
    user_columns = {column["name"] for column in inspector.get_columns("app_users")}
    assert {
        "username",
        "normalized_username",
        "password_hash",
        "is_platform_admin",
        "last_login_at",
        "password_changed_at",
        "normalized_email",
        "platform_role",
        "must_change_password",
    }.issubset(user_columns)
    session_columns = {
        column["name"] for column in inspector.get_columns("user_sessions")
    }
    assert "active_tenant_id" in session_columns
    assert "tenant_id" not in session_columns
    membership_columns = {
        column["name"] for column in inspector.get_columns("tenant_memberships")
    }
    assert "is_primary_admin" in membership_columns
    call_session_columns = {
        column["name"]
        for column in inspector.get_columns("call_sessions")
    }
    assert {
        "call_attempt_id",
        "provider_session_id",
        "provider_request_id",
        "connected_at",
        "failure_phase",
        "error_code",
        "http_status",
        "failure_retryable",
    }.issubset(call_session_columns)
    assert any(
        constraint["name"] == "uq_call_sessions_call_attempt_id"
        for constraint in inspector.get_unique_constraints("call_sessions")
    )
    owner = db.scalar(
        select(AppUser).where(
            AppUser.normalized_username == "owner@telefonagent.local"
        )
    )
    assert owner is not None
    assert owner.platform_role in {PlatformRole.owner, PlatformRole.admin}
    assert owner.password_hash.startswith("$argon2id$")
    assert "m=19456,t=2,p=1" in owner.password_hash
    setup_state = db.get(InitialAppSetup, 1)
    assert setup_state is not None
    setup_state.completed_at = None
    setup_state.tenant_id = None
    setup_state.user_id = None
    db.commit()
    seed_database(db)
    db.refresh(setup_state)
    assert setup_state.completed_at is not None
    assert setup_state.user_id == owner.id
