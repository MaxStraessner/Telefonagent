from collections.abc import Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

settings = get_settings()
engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@event.listens_for(Session, "after_begin")
def reapply_tenant_context(
    session: Session, _transaction: object, connection: object
) -> None:
    tenant_id = session.info.get("tenant_id")
    if tenant_id and connection.dialect.name == "postgresql":
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )


def bind_tenant_context(db: Session, tenant_id: object) -> None:
    """Keep the local RLS tenant bound across commits in one request/session."""
    db.info["tenant_id"] = str(tenant_id)
    if db.bind and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.info.pop("tenant_id", None)
        db.close()

