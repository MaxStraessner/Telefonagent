import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from alembic import command
from alembic.config import Config

os.environ["DATABASE_URL"] = "sqlite:///./test.db"
os.environ["ACTIVE_TENANT_SLUG"] = "salon-haarkunst-test"

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
