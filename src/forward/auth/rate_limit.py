"""In-process rate limiting.

A token bucket per key, guarded by a lock. In-process is the right scope here:
this is a single-process modular monolith, and a Redis-backed limiter would add
an operational dependency to protect one operator's own API.

Limiter errors are allowed to reach the caller. Continuing without a working
limit would silently disable a security control.

Buckets are pruned lazily: an attacker cycling source IPs would otherwise grow
the dict without bound, which turns a rate limiter into a memory-exhaustion
vector.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from math import isfinite


@dataclass(slots=True)
class _Bucket:
    tokens: float
    updated: float = field(default_factory=time.monotonic)


class RateLimiter:
    """Token buckets keyed by an opaque string (``scope:identity``)."""

    def __init__(self, *, default_per_minute: int = 240, max_keys: int = 10_000) -> None:
        """Create the in-memory limiter.

        Args:
            default_per_minute: Request limit used when a check has no override.
            max_keys: Maximum number of client buckets to keep before pruning.

        Raises:
            ValueError: If either configured limit is not positive.
        """
        if default_per_minute <= 0:
            raise ValueError("default_per_minute must be positive")
        if max_keys <= 0:
            raise ValueError("max_keys must be positive")
        self.default_per_minute = default_per_minute
        self.max_keys = max_keys
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ core
    def check(
        self,
        key: str,
        *,
        per_minute: int | None = None,
        cost: float = 1.0,
    ) -> tuple[bool, float]:
        """Consume tokens and report whether the request is allowed.

        Args:
            key: Bucket identity, such as ``api:127.0.0.1``.
            per_minute: Request limit for this bucket, or the configured default.
            cost: Number of tokens to consume.

        Returns:
            Whether the request is allowed and the wait time before retrying.
        """
        rate = self.default_per_minute if per_minute is None else per_minute
        if rate <= 0:
            raise ValueError("per_minute must be positive")
        if not isfinite(cost) or cost <= 0:
            raise ValueError("cost must be finite and positive")
        capacity = float(rate)
        refill_per_second = rate / 60.0
        now = time.monotonic()

        with self._lock:
            self._prune(now)
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(tokens=capacity, updated=now)
                self._buckets[key] = bucket

            elapsed = max(0.0, now - bucket.updated)
            bucket.tokens = min(capacity, bucket.tokens + elapsed * refill_per_second)
            bucket.updated = now

            if bucket.tokens >= cost:
                bucket.tokens -= cost
                return True, 0.0

            deficit = cost - bucket.tokens
            retry_after = deficit / refill_per_second
            return False, max(0.001, min(retry_after, 3600.0))

    # ----------------------------------------------------------------- admin
    def peek(self, key: str) -> float:
        """Remaining tokens, for diagnostics. Does not consume."""
        with self._lock:
            bucket = self._buckets.get(key)
            return float(bucket.tokens) if bucket else 0.0

    def reset(self, key: str | None = None) -> None:
        """Clear one client bucket or every bucket."""
        with self._lock:
            if key is None:
                self._buckets.clear()
            else:
                self._buckets.pop(key, None)

    def _prune(self, now: float) -> None:
        """Drop buckets that have fully refilled and gone quiet.

        Caller holds the lock. A full bucket is indistinguishable from a missing
        one, so discarding it loses nothing.
        """
        if len(self._buckets) <= self.max_keys:
            return
        cutoff = now - 600.0
        stale = [k for k, b in self._buckets.items() if b.updated < cutoff]
        for k in stale:
            del self._buckets[k]
        if len(self._buckets) > self.max_keys:
            # Still over budget (a flood within the last 10 minutes). Drop the
            # oldest half by last-seen rather than refusing to serve.
            ordered = sorted(self._buckets.items(), key=lambda kv: kv[1].updated)
            for k, _ in ordered[: len(ordered) // 2]:
                del self._buckets[k]


__all__ = ["RateLimiter"]
