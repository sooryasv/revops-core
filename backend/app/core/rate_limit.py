"""In-process rate limiting on the `limits` library (03 section 6.5). Per-process counters: a documented limitation."""

from __future__ import annotations

import hashlib

from limits import RateLimitItem, parse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter

from app.domain.errors import RateLimited


class RateLimiter:
    def __init__(self) -> None:
        self._limiter = MovingWindowRateLimiter(MemoryStorage())

    def check(self, rule: str, bucket: str, identifier: str) -> None:
        """Count one hit; raise RateLimited when over `rule` (for example "10/minute")."""
        item: RateLimitItem = parse(rule)
        # Hash so raw emails and tokens never sit in the counter store.
        key = hashlib.sha256(identifier.lower().encode()).hexdigest()[:32]
        if not self._limiter.hit(item, bucket, key):
            raise RateLimited("Too many requests. Please try again shortly.", retry_after=60)
