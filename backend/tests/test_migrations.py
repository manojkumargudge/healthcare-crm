"""Migrations from an empty database must produce exactly the schema of app/models.py.

The whole test suite already runs on a migrated schema (conftest._schema). These
tests build a second, private copy in its own Postgres schema so they can also
prove the drift check fails when it should.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.db_migrations import apply_all, check_drift, migration_files
from tests.conftest import TEST_DATABASE_URL, test_engine


@pytest.fixture
async def probe_schema(_schema: None) -> AsyncIterator[str]:
    name = "mig_probe"
    async with test_engine.begin() as conn:
        await conn.exec_driver_sql(f"DROP SCHEMA IF EXISTS {name} CASCADE")
        await conn.exec_driver_sql(f"CREATE SCHEMA {name}")
    yield name
    async with test_engine.begin() as conn:
        await conn.exec_driver_sql(f"DROP SCHEMA IF EXISTS {name} CASCADE")


def test_migration_files_are_ordered_and_start_at_baseline() -> None:
    names = [p.name for p in migration_files()]
    assert names[0] == "000_baseline.sql"
    assert names == sorted(names)
    assert [n[:3] for n in names] == [f"{i:03d}" for i in range(len(names))]  # no gaps or duplicates


async def test_suite_schema_matches_models(_schema: None) -> None:
    """The schema every other test runs on (all migrations, from empty) has no drift."""
    assert await check_drift(TEST_DATABASE_URL) == []


async def test_fresh_migrations_match_models_and_are_idempotent(probe_schema: str) -> None:
    applied = await apply_all(TEST_DATABASE_URL, schema=probe_schema)
    assert applied == [p.name for p in migration_files()]
    assert await check_drift(TEST_DATABASE_URL, schema=probe_schema) == []
    await apply_all(TEST_DATABASE_URL, schema=probe_schema)  # second run: still no drift
    assert await check_drift(TEST_DATABASE_URL, schema=probe_schema) == []


@pytest.mark.parametrize(
    ("break_sql", "expected"),
    [
        ("ALTER TABLE materials DROP COLUMN approved", "add_column"),
        ("ALTER TABLE interactions ALTER COLUMN attendees DROP NOT NULL", "modify_nullable"),
        ("ALTER TABLE hcps ALTER COLUMN name TYPE varchar(99)", "modify_type"),
        ("CREATE TABLE stray (x int)", "remove_table"),
        ("DROP INDEX uq_materials_name_lower", "missing_index"),
    ],
)
async def test_drift_check_detects_mismatch(probe_schema: str, break_sql: str, expected: str) -> None:
    await apply_all(TEST_DATABASE_URL, schema=probe_schema)
    async with test_engine.begin() as conn:
        await conn.exec_driver_sql(f"SET LOCAL search_path TO {probe_schema}")
        await conn.exec_driver_sql(break_sql)
    problems = await check_drift(TEST_DATABASE_URL, schema=probe_schema)
    assert any(p.startswith(expected) for p in problems), problems
