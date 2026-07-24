from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.security import pseudonymize
from app.models import AuthenticationRateLimit


class LoginThrottledError(Exception):
    pass


class LoginThrottle:
    def __init__(self, db: Session, settings: Settings):
        self.db = db
        self.settings = settings

    def bucket_id(self, scope: str, value: str) -> str:
        return pseudonymize(f"{scope}:{value}", self.settings.auth_hmac_secret)

    def assert_allowed(self, scope: str, value: str) -> None:
        bucket = self._get_bucket(scope, value, for_update=False)
        if bucket and bucket.blocked_until and self._as_utc(bucket.blocked_until) > self._now():
            raise LoginThrottledError

    def record_failure(self, scope: str, value: str, limit: int) -> str:
        now = self._now()
        bucket = self._get_bucket(scope, value, for_update=True)
        window = timedelta(minutes=self.settings.auth_rate_limit_window_minutes)
        if bucket is None:
            if self.db.bind and self.db.bind.dialect.name == "postgresql":
                self.db.execute(
                    postgres_insert(AuthenticationRateLimit)
                    .values(
                        scope=scope,
                        key_hash=self.bucket_id(scope, value),
                        failed_count=0,
                        window_started_at=now,
                    )
                    .on_conflict_do_nothing(
                        constraint="uq_auth_rate_limit_scope_key"
                    )
                )
                bucket = self._get_bucket(scope, value, for_update=True)
            else:
                bucket = AuthenticationRateLimit(
                    scope=scope,
                    key_hash=self.bucket_id(scope, value),
                    failed_count=0,
                    window_started_at=now,
                )
                self.db.add(bucket)
                self.db.flush()
            assert bucket is not None
        elif self._as_utc(bucket.window_started_at) + window <= now:
            bucket.failed_count = 0
            bucket.window_started_at = now
            bucket.blocked_until = None

        bucket.failed_count += 1
        if bucket.failed_count >= limit:
            exponent = min(bucket.failed_count - limit, 4)
            delay_seconds = min(60 * (2**exponent), 15 * 60)
            bucket.blocked_until = now + timedelta(seconds=delay_seconds)
        return bucket.key_hash

    def clear(self, scope: str, value: str) -> None:
        bucket = self._get_bucket(scope, value, for_update=True)
        if bucket is not None:
            self.db.delete(bucket)

    def _get_bucket(
        self, scope: str, value: str, *, for_update: bool
    ) -> AuthenticationRateLimit | None:
        statement = select(AuthenticationRateLimit).where(
            AuthenticationRateLimit.scope == scope,
            AuthenticationRateLimit.key_hash == self.bucket_id(scope, value),
        )
        if for_update and self.db.bind and self.db.bind.dialect.name == "postgresql":
            statement = statement.with_for_update()
        return self.db.scalar(statement)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
