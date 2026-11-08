"""Start the real backend for end-to-end tests.

Wipes a *_test database (refuses anything else), applies every migration from
empty, seeds demo HCPs / products / materials, creates one user per role, then
runs uvicorn. Reads DATABASE_URL, JWT_SECRET, E2E_PASSWORD and E2E_BACKEND_PORT
from the environment (set by playwright.config.js / CI).

Usage (from frontend/): python e2e/support/run_backend.py
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[3] / "backend"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)

USERS = [
    ("e2e-admin@example.com", "admin"),
    ("e2e-manager@example.com", "manager"),
    ("e2e-rep@example.com", "rep"),
]


async def prepare() -> None:
    from app.db_migrations import apply_all, reset_test_schema
    from app.config import settings

    await reset_test_schema(settings.database_url)  # refuses unless the db name ends with _test
    await apply_all(settings.database_url)

    from app.seed import seed
    from app.admin import create_user

    await seed()
    password = os.environ["E2E_PASSWORD"]
    for email, role in USERS:
        await create_user(email, role, password)
    print("e2e backend: schema migrated, demo data seeded, users created", flush=True)


if __name__ == "__main__":
    asyncio.run(prepare())
    port = os.environ.get("E2E_BACKEND_PORT", "8100")
    raise SystemExit(
        subprocess.call([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", port])
    )
