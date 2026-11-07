"""Config fail-fast, lifespan wiring, aware timestamps, and the timestamptz migration."""

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app import main as main_module
from tests.conftest import test_engine


# ------------------------------------------------------------- config -------
@pytest.fixture
def no_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for var in ("DATABASE_URL", "GROQ_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # no .env file here either


@pytest.mark.usefixtures("no_env")
def test_settings_fail_fast_when_both_missing() -> None:
    with pytest.raises(RuntimeError, match=r"DATABASE_URL, GROQ_API_KEY"):
        config.load_settings()


@pytest.mark.usefixtures("no_env")
@pytest.mark.parametrize(("present", "missing"), [("DATABASE_URL", "GROQ_API_KEY"), ("GROQ_API_KEY", "DATABASE_URL")])
def test_settings_fail_fast_when_one_missing(monkeypatch: pytest.MonkeyPatch, present: str, missing: str) -> None:
    monkeypatch.setenv(present, "x")
    with pytest.raises(RuntimeError, match=rf"setting\(s\): {missing}\."):
        config.load_settings()


@pytest.mark.usefixtures("no_env")
def test_settings_reject_empty_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u@h/db")
    monkeypatch.setenv("GROQ_API_KEY", "")
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        config.load_settings()


@pytest.mark.usefixtures("no_env")
def test_settings_have_no_credential_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u@h/db")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    s = config.load_settings()
    assert (s.database_url, s.groq_api_key) == ("postgresql+asyncpg://u@h/db", "k")
    assert config.Settings.model_fields["database_url"].is_required()
    assert config.Settings.model_fields["groq_api_key"].is_required()


# ------------------------------------------------------------ lifespan ------
async def test_lifespan_inits_db_and_disposes_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_init_db() -> None:
        calls.append("init_db")

    class FakeEngine:
        async def dispose(self) -> None:
            calls.append("dispose")

    monkeypatch.setattr(main_module, "init_db", fake_init_db)
    monkeypatch.setattr(main_module, "engine", FakeEngine())

    async with main_module.app.router.lifespan_context(main_module.app):
        assert calls == ["init_db"]
    assert calls == ["init_db", "dispose"]


def test_no_on_event_handlers() -> None:
    assert main_module.app.router.on_startup == []
    assert main_module.app.router.on_shutdown == []


# --------------------------------------------------- aware timestamps -------
def _aware(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    assert dt.tzinfo is not None and dt.utcoffset().total_seconds() == 0, value  # type: ignore[union-attr]  # tzinfo checked on the same line
    return dt


async def test_api_timestamps_are_utc_aware(client: httpx.AsyncClient, hcp: dict) -> None:
    _aware(hcp["created_at"])
    created = (await client.post("/api/interactions", json={"hcp_id": hcp["id"]})).json()
    _aware(created["created_at"])
    _aware(created["updated_at"])

    patched = (await client.patch(f"/api/interactions/{created['id']}", json={"raw_notes": "x"})).json()
    assert _aware(patched["updated_at"]) >= _aware(created["updated_at"])
    _aware(patched["edit_history"][0]["edited_at"])


async def test_schema_uses_timestamptz(db: AsyncSession) -> None:
    rows = (
        await db.execute(
            text(
                "SELECT table_name, column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND column_name IN ('created_at', 'updated_at')"
            )
        )
    ).all()
    assert len(rows) == 5
    assert {r.data_type for r in rows} == {"timestamp with time zone"}


# ----------------------------------------------------------- migration ------
MIGRATION = Path(__file__).resolve().parents[1] / "migrations" / "001_timestamps_to_timestamptz.sql"


async def test_timestamptz_migration_converts_legacy_naive_columns(_schema: None) -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    async with test_engine.begin() as conn:
        await conn.exec_driver_sql("DROP SCHEMA IF EXISTS legacy_mig CASCADE")
        await conn.exec_driver_sql("CREATE SCHEMA legacy_mig")
        await conn.exec_driver_sql("SET LOCAL search_path TO legacy_mig")
        # Old schema shape: naive timestamps holding UTC wall-clock values.
        await conn.exec_driver_sql("CREATE TABLE hcps (id text primary key, created_at timestamp)")
        await conn.exec_driver_sql("CREATE TABLE interactions (id text primary key, created_at timestamp, updated_at timestamp)")
        await conn.exec_driver_sql("CREATE TABLE follow_ups (id text primary key, created_at timestamp)")
        await conn.exec_driver_sql("CREATE TABLE chat_messages (id text primary key, created_at timestamp)")
        await conn.exec_driver_sql("CREATE TABLE unrelated (created_at timestamp)")
        await conn.exec_driver_sql("INSERT INTO hcps VALUES ('h1', '2026-01-02 03:04:05')")

        await conn.exec_driver_sql("SET LOCAL TIME ZONE 'Asia/Kolkata'")
        await conn.exec_driver_sql(sql)
        await conn.exec_driver_sql(sql)  # idempotent

        result = await conn.exec_driver_sql(
            "SELECT table_name || '.' || column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = 'legacy_mig' AND column_name IN ('created_at', 'updated_at')"
        )
        types: dict[str, str] = {str(r[0]): str(r[1]) for r in result.all()}
        assert types.pop("unrelated.created_at") == "timestamp without time zone"  # untouched
        assert set(types.values()) == {"timestamp with time zone"} and len(types) == 5

        # Old naive value was UTC wall-clock, so the instant must be 03:04:05 UTC,
        # even when the migration's session runs in a non-UTC time zone.
        value = (await conn.exec_driver_sql("SELECT created_at FROM hcps WHERE id = 'h1'")).scalar_one()
        assert value == datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
        await conn.exec_driver_sql("DROP SCHEMA legacy_mig CASCADE")
