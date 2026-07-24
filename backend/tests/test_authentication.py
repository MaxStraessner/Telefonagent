from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.security import sha256_token
from app.main import app
from app.models import AuthenticationRateLimit, UserSession
from app.services.provisioning import ProvisioningService

ORIGIN_HEADERS = {
    "Origin": "http://testserver",
    "X-Requested-With": "Telefonagent",
}


def login(client, username: str = "owner@telefonagent.local", password: str = "correct horse battery staple"):
    return client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
        headers=ORIGIN_HEADERS,
    )


def test_login_is_neutral_for_unknown_and_wrong_password(anonymous_client):
    unknown = login(anonymous_client, "not-a-user", "this password is definitely wrong")
    wrong = login(anonymous_client, password="this password is definitely wrong")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()
    assert unknown.json()["error"]["code"] == "invalid_credentials"


def test_login_sets_server_session_and_csrf_cookies(anonymous_client):
    response = login(anonymous_client)
    assert response.status_code == 200
    cookies = response.headers.get_list("set-cookie")
    assert any(
        "telefonagent_session=" in value
        and "HttpOnly" in value
        and "SameSite=lax" in value
        for value in cookies
    )
    assert any(
        "telefonagent_csrf=" in value and "HttpOnly" not in value
        for value in cookies
    )
    session = anonymous_client.get("/api/v1/auth/session")
    assert session.status_code == 200
    assert session.json()["user"]["role"] == "owner"
    assert session.json()["tenant"]["slug"] == "salon-haarkunst-test"


def test_csrf_is_required_and_logout_revokes_session(anonymous_client):
    assert login(anonymous_client).status_code == 200
    rejected = anonymous_client.post(
        "/api/v1/auth/logout", headers={"Origin": "http://testserver"}
    )
    assert rejected.status_code == 403
    csrf = anonymous_client.cookies.get("telefonagent_csrf")
    accepted = anonymous_client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
    )
    assert accepted.status_code == 204
    assert anonymous_client.get("/api/v1/auth/session").status_code == 401


def test_expired_session_is_rejected(anonymous_client, db):
    assert login(anonymous_client).status_code == 200
    raw_token = anonymous_client.cookies.get("telefonagent_session")
    session = db.scalar(
        select(UserSession).where(UserSession.token_hash == sha256_token(raw_token))
    )
    assert session is not None
    assert session.token_hash != raw_token
    assert session.csrf_token_hash != anonymous_client.cookies.get(
        "telefonagent_csrf"
    )
    session.idle_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    response = anonymous_client.get("/api/v1/auth/session")
    assert response.status_code == 401
    db.refresh(session)
    assert session.revoked_at is not None


def test_login_requires_exact_allowed_origin_and_ajax_header(anonymous_client):
    no_origin = anonymous_client.post(
        "/api/v1/auth/login",
        json={
            "username": "owner@telefonagent.local",
            "password": "correct horse battery staple",
        },
    )
    assert no_origin.status_code == 403
    cross_site = anonymous_client.post(
        "/api/v1/auth/login",
        json={
            "username": "owner@telefonagent.local",
            "password": "correct horse battery staple",
        },
        headers={
            **ORIGIN_HEADERS,
            "Sec-Fetch-Site": "cross-site",
        },
    )
    assert cross_site.status_code == 403


def test_rate_limit_uses_pseudonymous_persistent_buckets(
    anonymous_client, db
):
    app.dependency_overrides[get_settings] = lambda: Settings(
        app_env="test",
        database_url="sqlite:///./test.db",
        cors_origins="http://testserver",
        auth_hmac_secret="test-auth-secret-with-at-least-thirty-two-bytes",
        auth_username_failure_limit=2,
        auth_ip_failure_limit=30,
    )
    assert login(anonymous_client, password="wrong password one").status_code == 401
    assert login(anonymous_client, password="wrong password two").status_code == 401
    assert login(anonymous_client).status_code == 401
    buckets = list(db.scalars(select(AuthenticationRateLimit)))
    assert buckets
    assert all(
        "owner@telefonagent.local" not in bucket.key_hash for bucket in buckets
    )
    for bucket in buckets:
        db.delete(bucket)
    db.commit()


def test_password_change_revokes_other_sessions_and_rotates_current_session(
    anonymous_client, db
):
    ProvisioningService(db).provision_tenant(
        slug="password-change",
        name="Password Change",
        industry="services",
        timezone_name="Europe/Berlin",
        username="password-change-owner",
        display_name="Password Owner",
        email="password-change@example.test",
        password="initial password with enough length",
    )
    second_client = type(anonymous_client)(app)
    assert login(
        anonymous_client,
        "password-change-owner",
        "initial password with enough length",
    ).status_code == 200
    assert login(
        second_client,
        "password-change-owner",
        "initial password with enough length",
    ).status_code == 200
    old_token = anonymous_client.cookies.get("telefonagent_session")
    csrf = anonymous_client.cookies.get("telefonagent_csrf")
    changed = anonymous_client.post(
        "/api/v1/auth/change-password",
        json={
            "current_password": "initial password with enough length",
            "new_password": "replacement password with enough length",
        },
        headers={"Origin": "http://testserver", "X-CSRF-Token": csrf},
    )
    assert changed.status_code == 200
    assert anonymous_client.cookies.get("telefonagent_session") != old_token
    assert second_client.get("/api/v1/auth/session").status_code == 401
    assert anonymous_client.get("/api/v1/auth/session").status_code == 200


def test_production_security_configuration_fails_closed():
    with pytest.raises(ValidationError):
        Settings(app_env="production")
    settings = Settings(
        app_env="production",
        database_url="postgresql+psycopg://runtime:test@database/app",
        auth_hmac_secret="production-auth-secret-with-more-than-thirty-two-bytes",
        app_base_url="https://api.example.test",
        cors_origins="https://app.example.test",
    )
    assert settings.session_cookie_name == "__Host-telefonagent_session"
    assert settings.csrf_cookie_name == "__Host-telefonagent_csrf"
    assert settings.is_production
