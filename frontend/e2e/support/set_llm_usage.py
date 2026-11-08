"""Set today's (UTC) AI-request count in the e2e database, to test the daily cap.

Usage (from frontend/): python e2e/support/set_llm_usage.py CALLS
Reads DATABASE_URL from the environment; refuses anything but a *_test database.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "backend"))

import asyncpg  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.db_url import asyncpg_dsn_and_args  # noqa: E402  (no settings import: no secrets needed)


async def main(calls: int) -> None:
    url = os.environ["DATABASE_URL"]
    if not (make_url(url).database or "").endswith("_test"):
        raise SystemExit("refusing: DATABASE_URL must point at a *_test database")
    dsn, args = asyncpg_dsn_and_args(url)
    conn = await asyncpg.connect(dsn, **args)
    try:
        await conn.execute(
            "INSERT INTO llm_usage (day, calls) VALUES ((now() AT TIME ZONE 'utc')::date, $1) "
            "ON CONFLICT (day) DO UPDATE SET calls = EXCLUDED.calls",
            calls,
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1])))
