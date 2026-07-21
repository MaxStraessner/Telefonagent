import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, status

_lock = threading.Lock()
_buckets: dict[str, deque[float]] = defaultdict(deque)


def enforce_rate_limit(key: str, *, limit: int, window_seconds: int = 60) -> None:
    now = time.monotonic()
    with _lock:
        bucket = _buckets[key]
        while bucket and bucket[0] <= now - window_seconds:
            bucket.popleft()
        if len(bucket) >= limit:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"code": "rate_limit_exceeded", "message": "Zu viele Anfragen. Bitte versuchen Sie es in Kürze erneut."},
                headers={"Retry-After": str(window_seconds)},
            )
        bucket.append(now)
