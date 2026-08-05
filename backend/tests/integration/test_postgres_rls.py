import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import hash_password
from app.services.authentication import AuthenticationService
from app.services.tenant_resolution import InboundRouteTenantResolver

MIGRATOR_URL = os.environ.get("TEST_POSTGRES_MIGRATOR_URL")
RUNTIME_URL = os.environ.get("TEST_POSTGRES_RLS_URL")

pytestmark = pytest.mark.skipif(
    not MIGRATOR_URL or not RUNTIME_URL,
    reason=(
        "TEST_POSTGRES_MIGRATOR_URL und TEST_POSTGRES_RLS_URL sind nicht "
        "für den isolierten PostgreSQL-17-Test gesetzt."
    ),
)

TENANT_A = "10000000-0000-4000-8000-000000000001"
TENANT_B = "10000000-0000-4000-8000-000000000002"
SERVICE_A = "20000000-0000-4000-8000-000000000001"
SERVICE_B = "20000000-0000-4000-8000-000000000002"
USER_A = "30000000-0000-4000-8000-000000000001"
MEMBERSHIP_A = "40000000-0000-4000-8000-000000000001"
INBOUND_ROUTE_A = "50000000-0000-4000-8000-000000000001"
OAUTH_STATE_A = "60000000-0000-4000-8000-000000000001"
PASSWORD = "postgres runtime authentication password"


@pytest.fixture(scope="module", autouse=True)
def postgres_rls_fixture():
    migrator = create_engine(MIGRATOR_URL)
    with migrator.begin() as connection:
        connection.execute(
            text(
                """
                DO $$
                BEGIN
                    IF NOT EXISTS (
                        SELECT 1 FROM pg_roles
                        WHERE rolname = 'telefonagent_rls_runtime'
                    ) THEN
                        CREATE ROLE telefonagent_rls_runtime
                        LOGIN PASSWORD 'runtime-test-password';
                    END IF;
                END
                $$;
                """
            )
        )
        connection.execute(
            text(
                "GRANT CONNECT ON DATABASE telefonagent_auth_test "
                "TO telefonagent_rls_runtime"
            )
        )
        connection.execute(
            text(
                "GRANT USAGE ON SCHEMA public TO telefonagent_rls_runtime"
            )
        )
        connection.execute(
            text(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public "
                "TO telefonagent_rls_runtime"
            )
        )
        for tenant_id, slug, name in (
            (TENANT_A, "rls-alpha", "RLS Alpha"),
            (TENANT_B, "rls-beta", "RLS Beta"),
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO tenants (
                        id, slug, name, industry, timezone, status
                    ) VALUES (
                        CAST(:id AS uuid), :slug, :name, 'services',
                        'Europe/Berlin', 'active'
                    )
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {"id": tenant_id, "slug": slug, "name": name},
            )
        for service_id, tenant_id, name in (
            (SERVICE_A, TENANT_A, "Alpha Service"),
            (SERVICE_B, TENANT_B, "Beta Service"),
        ):
            connection.execute(
                text(
                    """
                    INSERT INTO services (
                        id, tenant_id, name, description,
                        duration_minutes, is_active
                    ) VALUES (
                        CAST(:id AS uuid), CAST(:tenant_id AS uuid), :name,
                        '', 30, true
                    )
                    ON CONFLICT (id) DO NOTHING
                    """
                ),
                {"id": service_id, "tenant_id": tenant_id, "name": name},
            )
        connection.execute(
            text(
                """
                INSERT INTO app_users (
                    id, username, normalized_username, password_hash,
                    email, display_name, is_active, is_platform_admin
                ) VALUES (
                    CAST(:id AS uuid), 'rls-owner', 'rls-owner', :password_hash,
                    'rls-owner@example.test', 'RLS Owner', true, false
                )
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": USER_A, "password_hash": hash_password(PASSWORD)},
        )
        connection.execute(
            text(
                """
                INSERT INTO tenant_memberships (
                    id, tenant_id, user_id, role, is_active
                ) VALUES (
                    CAST(:id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:user_id AS uuid), 'owner', true
                )
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {
                "id": MEMBERSHIP_A,
                "tenant_id": TENANT_A,
                "user_id": USER_A,
            },
        )
        connection.execute(
            text(
                """
                INSERT INTO tenant_inbound_routes (
                    id, tenant_id, route_type, normalized_identifier, is_active
                ) VALUES (
                    CAST(:id AS uuid), CAST(:tenant_id AS uuid),
                    'phone_number', '+4930123456', true
                )
                ON CONFLICT (id) DO NOTHING
                """
            ),
            {"id": INBOUND_ROUTE_A, "tenant_id": TENANT_A},
        )
        connection.execute(
            text(
                """
                INSERT INTO calendar_oauth_states (
                    id, tenant_id, user_id, provider, state_hash,
                    encrypted_code_verifier, expires_at
                ) VALUES (
                    CAST(:id AS uuid), CAST(:tenant_id AS uuid),
                    CAST(:user_id AS uuid), 'google', 'known-state-hash',
                    'encrypted-test-verifier', now() + interval '10 minutes'
                )
                ON CONFLICT (id) DO UPDATE
                SET tenant_id = EXCLUDED.tenant_id,
                    user_id = EXCLUDED.user_id,
                    provider = EXCLUDED.provider,
                    state_hash = EXCLUDED.state_hash,
                    encrypted_code_verifier = EXCLUDED.encrypted_code_verifier,
                    expires_at = EXCLUDED.expires_at,
                    consumed_at = NULL
                """
            ),
            {
                "id": OAUTH_STATE_A,
                "tenant_id": TENANT_A,
                "user_id": USER_A,
            },
        )
    yield
    migrator.dispose()


def test_runtime_role_cannot_read_without_transaction_tenant_context():
    engine = create_engine(RUNTIME_URL)
    with engine.begin() as connection:
        assert connection.scalar(text("SELECT count(*) FROM services")) == 0
    engine.dispose()


def test_runtime_role_sees_only_selected_tenant():
    engine = create_engine(RUNTIME_URL)
    with engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": TENANT_A},
        )
        assert connection.scalars(
            text("SELECT name FROM services ORDER BY name")
        ).all() == ["Alpha Service"]
    engine.dispose()


def test_rls_blocks_cross_tenant_write():
    engine = create_engine(RUNTIME_URL)
    with pytest.raises(ProgrammingError):
        with engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": TENANT_A},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO services (
                        id, tenant_id, name, description,
                        duration_minutes, is_active
                    ) VALUES (
                        gen_random_uuid(), CAST(:foreign_tenant AS uuid),
                        'Blocked', '', 30, true
                    )
                    """
                ),
                {"foreign_tenant": TENANT_B},
            )
    engine.dispose()


def test_composite_foreign_key_rejects_cross_tenant_relation():
    engine = create_engine(RUNTIME_URL)
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": TENANT_A},
            )
            connection.execute(
                text(
                    """
                    INSERT INTO calendar_appointment_types (
                        id, tenant_id, service_id, name, description,
                        duration_minutes, location_type, location_text, is_active
                    ) VALUES (
                        gen_random_uuid(), CAST(:tenant_id AS uuid),
                        CAST(:foreign_service AS uuid), 'Invalid', '',
                        30, 'phone', '', true
                    )
                    """
                ),
                {"tenant_id": TENANT_A, "foreign_service": SERVICE_B},
            )
    engine.dispose()


def test_authentication_establishes_rls_context_for_runtime_role():
    engine = create_engine(RUNTIME_URL)
    settings = Settings(
        app_env="test",
        database_url=RUNTIME_URL,
        cors_origins="http://testserver",
        auth_hmac_secret="postgres-test-auth-secret-with-thirty-two-bytes",
    )
    with Session(engine, expire_on_commit=False) as db:
        authenticated, secrets = AuthenticationService(db, settings).login(
            "rls-owner", PASSWORD, "127.0.0.1"
        )
        assert str(authenticated.tenant.id) == TENANT_A
        restored = AuthenticationService(db, settings).authenticate(secrets.token)
        assert str(restored.tenant.id) == TENANT_A
        assert db.scalars(
            text("SELECT name FROM services ORDER BY name")
        ).all() == ["Alpha Service"]
    engine.dispose()


def test_security_definer_resolvers_establish_tenant_without_bypassing_rls():
    engine = create_engine(RUNTIME_URL)
    with Session(engine, expire_on_commit=False) as db:
        resolved = InboundRouteTenantResolver(db).resolve(
            "phone_number", "+49 30 123456"
        )
        assert str(resolved.id) == TENANT_A
        assert db.scalars(text("SELECT name FROM services")).all() == [
            "Alpha Service"
        ]
    with engine.begin() as connection:
        assert str(
            connection.scalar(
                text(
                    "SELECT resolve_calendar_oauth_tenant("
                    "'known-state-hash', 'google')"
                )
            )
        ) == TENANT_A
    engine.dispose()
