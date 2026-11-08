"""Per-rep rate limit for the LLM-backed endpoints (chat, follow-up suggestions, voice notes).

Sliding 60-second window, one shared budget per rep across those endpoints
(RATE_LIMIT_LLM_PER_MINUTE). In-memory: correct for the single backend process
this app runs; several workers would each keep their own window (use Redis then).
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque

from fastapi import Depends, HTTPException

from app.authz import Authz, get_authz
from app.config import settings

WINDOW_SECONDS = 60.0


class RateLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def hit(self, key: str, limit: int, now: float | None = None) -> float | None:
        """Record a call; returns None if allowed, else seconds until the next slot."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] >= WINDOW_SECONDS:
                q.popleft()
            if len(q) >= limit:
                return WINDOW_SECONDS - (now - q[0])
            q.append(now)
            return None

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


llm_limiter = RateLimiter()


async def llm_rate_limit(authz: Authz = Depends(get_authz)) -> Authz:
    """get_authz + the per-rep LLM budget (429 with Retry-After when exhausted)."""
    retry_after = llm_limiter.hit(authz.user.id, settings.rate_limit_llm_per_minute)
    if retry_after is not None:
        raise HTTPException(
            429,
            f"Too many AI requests: limit is {settings.rate_limit_llm_per_minute} per minute.",
            headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
        )
    return authz
