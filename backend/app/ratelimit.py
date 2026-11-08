"""Rate limits for the LLM-backed endpoints (chat, follow-up suggestions, voice notes).

1. Per rep: sliding 60-second window, one shared budget per rep across those
   endpoints (RATE_LIMIT_LLM_PER_MINUTE). In-memory: correct for the single backend
   process this app runs; several workers would each keep their own window.
2. Global: LLM_DAILY_CAP requests per UTC day across everyone, counted in the
   llm_usage table (atomic upsert), so it holds across restarts and instances.
   One request counts once, however many model calls it makes.
"""

from __future__ import annotations

import math
import threading
import time
from collections import defaultdict, deque
from datetime import date, datetime, timedelta, timezone

from fastapi import Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import Authz, get_authz
from app.config import settings
from app.database import get_db

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


# Count one call for `day` unless the cap is reached; RETURNING is empty when it is.
_COUNT_CALL = text(
    "INSERT INTO llm_usage (day, calls) VALUES (:day, 1) "
    "ON CONFLICT (day) DO UPDATE SET calls = llm_usage.calls + 1 WHERE llm_usage.calls < :cap "
    "RETURNING calls"
)


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def seconds_until_utc_midnight(now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
    return max(1, math.ceil((midnight - now).total_seconds()))


async def count_daily_llm_call(db: AsyncSession, cap: int, day: date | None = None) -> bool:
    """Atomically count one AI request for today; False (nothing counted) when the cap is reached."""
    if cap <= 0:
        return True  # cap disabled
    row = (await db.execute(_COUNT_CALL, {"day": day or utc_today(), "cap": cap})).first()
    await db.commit()
    return row is not None


async def llm_rate_limit(authz: Authz = Depends(get_authz), db: AsyncSession = Depends(get_db)) -> Authz:
    """get_authz + the per-rep and global daily LLM budgets (429 with Retry-After when exhausted)."""
    per_minute = settings.rate_limit_llm_per_minute
    retry_after = llm_limiter.hit(authz.user.id, per_minute)
    if retry_after is not None:
        wait = max(1, math.ceil(retry_after))
        raise HTTPException(
            429,
            f"You're sending AI requests too quickly (limit: {per_minute} per minute). "
            f"Please try again in {wait} seconds.",
            headers={"Retry-After": str(wait)},
        )
    if not await count_daily_llm_call(db, settings.llm_daily_cap):
        raise HTTPException(
            429,
            f"The daily limit of {settings.llm_daily_cap} AI requests has been reached. "
            "AI features (chat, follow-up suggestions, voice notes) are back after midnight UTC; "
            "you can still log interactions with the form.",
            headers={"Retry-After": str(seconds_until_utc_midnight())},
        )
    return authz
