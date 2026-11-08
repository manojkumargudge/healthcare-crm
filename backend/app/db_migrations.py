"""Apply the hand-written SQL migrations and check them against the models.

    python -m app.db_migrations apply         # apply all migrations to DATABASE_URL (idempotent)
    python -m app.db_migrations check         # report drift between DATABASE_URL and app/models.py
    python -m app.db_migrations check-fresh   # DROP the public schema of a *_test database, apply
                                              # every migration from empty, then check drift (CI)

Migrations are backend/migrations/NNN_*.sql, applied in file-name order. Each
file is idempotent and manages its own transaction, so "apply" always runs
all of them (there is no applied-migrations table).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

import asyncpg
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.db_url import asyncpg_dsn_and_args, split_ssl
from app import models  # noqa: F401  (registers every table on Base.metadata)
from app.database import Base

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

# Functional indexes are invisible to alembic's autogenerate, so they are checked by name.
EXPECTED_EXPRESSION_INDEXES = {
    ("materials", "uq_materials_name_lower"): "lower((name)::text)",
    ("products", "uq_products_name_lower"): "lower((name)::text)",
    ("users", "uq_users_email_lower"): "lower((email)::text)",
}


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))


# Serializes concurrent "apply" runs (e.g. two instances starting at once). Arbitrary constant.
_MIGRATION_LOCK_ID = 727_001


async def _connect(url: str, **kwargs: Any) -> asyncpg.Connection:
    """asyncpg connection for a SQLAlchemy-style URL (sslmode=... becomes asyncpg's ssl argument)."""
    dsn, args = asyncpg_dsn_and_args(url)
    return await asyncpg.connect(dsn, **args, **kwargs)


def _server_settings(schema: str | None) -> dict[str, str]:
    return {"search_path": schema} if schema else {}


async def apply_all(url: str, *, schema: str | None = None, verbose: bool = False) -> list[str]:
    """Run every migration file, in order, on one connection (simple-query protocol, like psql).

    `schema` (tests only) sets search_path so the migrations build into that schema.
    """
    conn = await _connect(url, server_settings=_server_settings(schema))
    if verbose:
        conn.add_log_listener(lambda _c, msg: print(f"    {msg.message}"))
    applied = []
    try:
        # Migration 008 backfills ownership to the seed admin. Pass its email and an
        # argon2 hash (never the plain password) as session-local settings.
        from app.config import settings
        from app.security import hash_password

        if settings.admin_email and settings.admin_password:
            await conn.execute("SELECT set_config('app.seed_admin_email', $1, false)", settings.admin_email)
            await conn.execute(
                "SELECT set_config('app.seed_admin_password_hash', $1, false)", hash_password(settings.admin_password)
            )
        await conn.execute("SELECT pg_advisory_lock($1)", _MIGRATION_LOCK_ID)
        for path in migration_files():
            if verbose:
                print(f"applying {path.name}")
            await conn.execute(path.read_text(encoding="utf-8"))
            applied.append(path.name)
    finally:
        await conn.close()
    return applied


async def reset_test_schema(url: str) -> None:
    """Drop and recreate the public schema. Refuses unless the database name ends in _test."""
    name = make_url(url).database or ""
    if not name.endswith("_test"):
        raise SystemExit(f"refusing to wipe database {name!r}: name must end with '_test'")
    conn = await _connect(url)
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    finally:
        await conn.close()


def _describe(diff: Any) -> str:
    if isinstance(diff, list):  # column-level modifications come grouped
        return "; ".join(_describe(d) for d in diff)
    op, *rest = diff
    return f"{op}: " + ", ".join(str(r) for r in rest)


async def check_drift(url: str, *, schema: str | None = None) -> list[str]:
    """Differences between the live schema and app/models.py (empty list = no drift)."""
    clean, ssl_args = split_ssl(url)
    engine = create_async_engine(
        clean, poolclass=NullPool, connect_args={**ssl_args, "server_settings": _server_settings(schema)}
    )
    try:
        async with engine.connect() as conn:
            diffs = await conn.run_sync(
                lambda sync_conn: compare_metadata(
                    MigrationContext.configure(sync_conn, opts={"compare_type": True}), Base.metadata
                )
            )
            problems = [_describe(d) for d in diffs]
            rows = (
                await conn.execute(
                    text(
                        "SELECT tablename, indexname, indexdef FROM pg_indexes "
                        "WHERE schemaname = current_schema()"
                    )
                )
            ).all()
    finally:
        await engine.dispose()
    found = {(r.tablename, r.indexname): r.indexdef for r in rows}
    for (table, index), expr in EXPECTED_EXPRESSION_INDEXES.items():
        definition = found.get((table, index))
        if definition is None:
            problems.append(f"missing_index: {table}.{index}")
        elif expr not in definition or "UNIQUE" not in definition:
            problems.append(f"index_mismatch: {table}.{index}: {definition}")
    return problems


async def _main(argv: list[str]) -> int:
    from app.config import settings

    cmd = argv[1] if len(argv) > 1 else "check"
    url = settings.database_url
    if cmd == "check-fresh":
        await reset_test_schema(url)
    if cmd in ("apply", "check-fresh"):
        await apply_all(url, verbose=True)
    if cmd not in ("apply", "check", "check-fresh"):
        print(__doc__)
        return 2
    if cmd == "apply":
        return 0
    problems = await check_drift(url)
    if problems:
        print("SCHEMA DRIFT between migrations and app/models.py:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"no drift: {len(migration_files())} migrations match app/models.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv)))
