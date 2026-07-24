import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alembic import command
from alembic.config import Config

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["APP_ENV"] = "test"
os.environ["AUTH_HMAC_SECRET"] = "test-auth-secret-with-at-least-thirty-two-bytes"
os.environ["CORS_ORIGINS"] = "http://testserver"
os.environ["DEV_BOOTSTRAP_ENABLED"] = "true"
os.environ["DEV_BOOTSTRAP_USERNAME"] = "owner@telefonagent.local"
os.environ["DEV_BOOTSTRAP_PASSWORD"] = "correct horse battery staple"
os.environ["CALENDAR_TOKEN_ENCRYPTION_KEY"] = "MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA="

from app.core.config import get_settings  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.seed import seed_database  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def migrated_database():
    database_file = Path("test.db")
    if database_file.exists():
        database_file.unlink()
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    with SessionLocal() as db:
        seed_database(db)
    yield
    engine.dispose()
    if database_file.exists():
        database_file.unlink()


@pytest.fixture()
def client() -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login",
        json={
            "username": "owner@telefonagent.local",
            "password": "correct horse battery staple",
        },
        headers={
            "Origin": "http://testserver",
            "X-Requested-With": "Telefonagent",
        },
    )
    assert response.status_code == 200, response.text
    original_request = client.request

    def authenticated_request(method: str, url: str, **kwargs):
        if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
            headers = dict(kwargs.pop("headers", {}) or {})
            headers.setdefault("Origin", "http://testserver")
            csrf_token = client.cookies.get("telefonagent_csrf")
            if csrf_token:
                headers.setdefault("X-CSRF-Token", csrf_token)
            kwargs["headers"] = headers
        return original_request(method, url, **kwargs)

    client.request = authenticated_request  # type: ignore[method-assign]
    return client


@pytest.fixture()
def anonymous_client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def db():
    with SessionLocal() as session:
        yield session
        session.rollback()


@pytest.fixture(autouse=True)
def reset_overrides():
    yield
    app.dependency_overrides.clear()
    get_settings.cache_clear()
