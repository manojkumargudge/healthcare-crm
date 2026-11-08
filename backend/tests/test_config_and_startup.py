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
SETTINGS_VARS = (
    "DATABASE_URL", "GROQ_API_KEY", "GROQ_MODEL", "GROQ_MODEL_FAST", "FRONTEND_ORIGIN", "BACKEND_PORT",
    "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "POSTGRES_HOST", "POSTGRES_PORT",
    "CORS_ORIGINS", "RUN_MIGRATIONS_ON_STARTUP",
)


@pytest.fixture
def env_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Isolate settings from the real environment and the developer's root .env.

    Returns a writer: env_file("KEY=value", ...) creates the .env that load_settings reads.
    """
    for var in SETTINGS_VARS:
        monkeypatch.delenv(var, raising=False)
    path = tmp_path / ".env"
    monkeypatch.setattr(config, "ENV_FILE", path)

    def write(*lines: str) -> Path:
        path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
        return path

    return write


def test_env_file_is_the_repo_root_dotenv() -> None:
    assert config.ENV_FILE == Path(config.__file__).resolve().parents[2] / ".env"


def test_settings_fail_fast_when_nothing_is_set(env_file) -> None:
    with pytest.raises(RuntimeError) as exc:
        config.load_settings()  # no .env written at all
    # Field errors are reported first; the database check runs once fields are valid.
    assert "GROQ_API_KEY (missing)" in str(exc.value)


def test_settings_missing_database_settings(env_file) -> None:
    env_file("GROQ_API_KEY=k")
    with pytest.raises(RuntimeError, match="neither are POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB"):
        config.load_settings()


def test_settings_missing_groq_key(env_file) -> None:
    env_file("DATABASE_URL=postgresql+asyncpg://u:p@h:1/db")
    with pytest.raises(RuntimeError, match=r"GROQ_API_KEY \(missing\)"):
        config.load_settings()


def test_settings_reject_empty_groq_key(env_file) -> None:
    env_file("DATABASE_URL=postgresql+asyncpg://u:p@h:1/db", "GROQ_API_KEY=")
    with pytest.raises(RuntimeError, match=r"GROQ_API_KEY \(missing\)"):
        config.load_settings()


def test_settings_partial_postgres_values_name_whats_missing(env_file) -> None:
    env_file("GROQ_API_KEY=k", "POSTGRES_USER=u")
    with pytest.raises(RuntimeError, match="neither are POSTGRES_PASSWORD, POSTGRES_DB"):
        config.load_settings()


def test_database_url_built_from_root_env_postgres_values(env_file) -> None:
    env_file(
        "POSTGRES_USER=hcp_user", "POSTGRES_PASSWORD=s3cret", "POSTGRES_DB=hcp_crm",
        "POSTGRES_PORT=55432", "GROQ_API_KEY=k",
    )
    s = config.load_settings()
    assert s.database_url == "postgresql+asyncpg://hcp_user:s3cret@127.0.0.1:55432/hcp_crm"


def test_explicit_database_url_wins(env_file, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file("POSTGRES_USER=u", "POSTGRES_PASSWORD=p", "POSTGRES_DB=d", "GROQ_API_KEY=k")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://x:y@db:5432/other")
    assert config.load_settings().database_url == "postgresql+asyncpg://x:y@db:5432/other"


@pytest.mark.parametrize("key", ["GROQ_FALLBACK_MODEL", "POSTGRS_USER", "SOMETHING_ELSE"])
def test_unknown_key_in_dotenv_fails_startup(env_file, key: str) -> None:
    env_file("DATABASE_URL=postgresql+asyncpg://u:p@h:1/db", "GROQ_API_KEY=k", f"{key}=x")
    with pytest.raises(RuntimeError, match=rf"{key} \(unknown key\)"):
        config.load_settings()


def test_every_env_example_key_is_accepted(env_file) -> None:
    """The template must never trip the strict unknown-key check."""
    example = Path(config.__file__).resolve().parents[2] / ".env.example"
    lines = [ln.lstrip("# ").strip() for ln in example.read_text(encoding="utf-8").splitlines()]
    keys = sorted({ln.split("=", 1)[0] for ln in lines if "=" in ln and ln.split("=", 1)[0].isupper()})
    assert {"POSTGRES_USER", "GROQ_API_KEY", "POSTGRES_PORT", "GROQ_MODEL_FAST"} <= set(keys)
    valid = {
        "GROQ_API_KEY": "k", "JWT_SECRET": "x" * 40, "ADMIN_PASSWORD": "long-enough-password",
        "ADMIN_EMAIL": "admin@example.com", "ACCESS_TOKEN_MINUTES": "15", "RATE_LIMIT_LLM_PER_MINUTE": "20",
        "RUN_MIGRATIONS_ON_STARTUP": "true", "DATABASE_URL": "postgresql+asyncpg://u:p@h:1/db",
        **{f"DEMO_{r}_PASSWORD": "long-enough-password" for r in ("REP", "MANAGER", "ADMIN")},
    }
    env_file(*(f"{k}={valid.get(k, '5432' if k.endswith('_PORT') else 'v')}" for k in keys))
    config.load_settings()


# ------------------------------------------------- hosted DATABASE_URL -----
def test_hosted_postgres_url_is_normalized(env_file, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file("GROQ_API_KEY=k")
    monkeypatch.setenv("DATABASE_URL", "postgres://u:p@ep-x.neon.tech/neondb?sslmode=require&channel_binding=require")
    s = config.load_settings()
    assert s.database_url.startswith("postgresql+asyncpg://u:p@ep-x.neon.tech/neondb?")
    assert "sslmode=require" in s.database_url  # kept; split into asyncpg's ssl arg at connect time


def test_invalid_sslmode_fails_startup(env_file, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file("GROQ_API_KEY=k")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db?sslmode=always")
    with pytest.raises(RuntimeError, match="sslmode must be one of"):
        config.load_settings()


def test_cors_origins_from_env(env_file, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file("DATABASE_URL=postgresql+asyncpg://u:p@h:1/db", "GROQ_API_KEY=k")
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")
    monkeypatch.setenv("CORS_ORIGINS", " https://hcp-crm-web.onrender.com/ ,, http://localhost:5173,https://x.example")
    assert config.load_settings().allowed_origins == [
        "http://localhost:5173", "https://hcp-crm-web.onrender.com", "https://x.example",
    ]


async def test_health_answers_head_for_uptime_monitors() -> None:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main_module.app), base_url="http://test") as c:
        resp = await c.head("/health")
    assert resp.status_code == 200


async def test_lifespan_skips_migrations_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake(*_a: object) -> None:
        calls.append("called")

    class FakeEngine:
        async def dispose(self) -> None:
            pass

    monkeypatch.setattr(main_module.settings, "run_migrations_on_startup", False)
    monkeypatch.setattr(main_module, "init_db", fake)
    monkeypatch.setattr(main_module, "AsyncSessionLocal", lambda: _NullSession())
    monkeypatch.setattr(main_module, "ensure_seed_admin", fake)
    monkeypatch.setattr(main_module, "seed_materials", fake)
    monkeypatch.setattr(main_module, "seed_products", fake)
    monkeypatch.setattr(main_module, "engine", FakeEngine())
    monkeypatch.setattr(main_module, "init_groq_client", lambda: None)
    monkeypatch.setattr(main_module, "close_groq_client", lambda: fake())
    async with main_module.app.router.lifespan_context(main_module.app):
        pass
    assert calls == ["called"] * 4  # 3 seeds + close; init_db was skipped


class _NullSession:
    async def __aenter__(self) -> "_NullSession":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


# ------------------------------------------------------------ lifespan ------
async def test_lifespan_inits_db_and_disposes_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake_init_db() -> None:
        calls.append("init_db")

    class FakeEngine:
        async def dispose(self) -> None:
            calls.append("dispose")

    async def fake_close_groq() -> None:
        calls.append("close_groq")

    monkeypatch.setattr(main_module, "init_db", fake_init_db)
    monkeypatch.setattr(main_module, "seed_materials", lambda _s: fake_init_db())
    monkeypatch.setattr(main_module, "engine", FakeEngine())
    monkeypatch.setattr(main_module, "init_groq_client", lambda: calls.append("init_groq"))
    monkeypatch.setattr(main_module, "close_groq_client", fake_close_groq)

    async with main_module.app.router.lifespan_context(main_module.app):
        assert calls == ["init_db", "init_db", "init_groq"]  # init_db, then seed (reuses the fake)
    assert calls == ["init_db", "init_db", "init_groq", "close_groq", "dispose"]


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
    assert len(rows) == 8  # 5 original + materials, products and users created_at
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


async def test_timestamptz_migration_preserves_naive_utc_values_exactly(_schema: None) -> None:
    """Every migrated column: a naive UTC wall-clock value becomes the same instant in UTC,
    to the microsecond, regardless of the session time zone used to run or read it."""
    naive = datetime(2025, 12, 31, 23, 59, 58, 123456)  # near a date boundary, with microseconds
    columns = [
        ("hcps", "created_at"),
        ("interactions", "created_at"),
        ("interactions", "updated_at"),
        ("follow_ups", "created_at"),
        ("chat_messages", "created_at"),
    ]
    sql = MIGRATION.read_text(encoding="utf-8")
    async with test_engine.begin() as conn:
        await conn.exec_driver_sql("DROP SCHEMA IF EXISTS legacy_vals CASCADE")
        await conn.exec_driver_sql("CREATE SCHEMA legacy_vals")
        await conn.exec_driver_sql("SET LOCAL search_path TO legacy_vals")
        await conn.exec_driver_sql("CREATE TABLE hcps (created_at timestamp)")
        await conn.exec_driver_sql("CREATE TABLE interactions (created_at timestamp, updated_at timestamp)")
        await conn.exec_driver_sql("CREATE TABLE follow_ups (created_at timestamp)")
        await conn.exec_driver_sql("CREATE TABLE chat_messages (created_at timestamp)")
        for table, column in columns:
            await conn.exec_driver_sql(f"INSERT INTO {table} ({column}) VALUES ($1)", (naive,))

        await conn.exec_driver_sql("SET LOCAL TIME ZONE 'America/Los_Angeles'")  # hostile session TZ
        await conn.exec_driver_sql(sql)

        expected = naive.replace(tzinfo=timezone.utc)
        for table, column in columns:
            got = (await conn.exec_driver_sql(f"SELECT {column} FROM {table} WHERE {column} IS NOT NULL")).scalar_one()
            assert got == expected, f"{table}.{column}: {got!r} != {expected!r}"
            # Same wall-clock digits when rendered in UTC: the value is unchanged, only typed.
            utc_text = (
                await conn.exec_driver_sql(
                    f"SELECT to_char({column} AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS.US') FROM {table} "
                    f"WHERE {column} IS NOT NULL"
                )
            ).scalar_one()
            assert utc_text == "2025-12-31 23:59:58.123456", f"{table}.{column}"
        await conn.exec_driver_sql("DROP SCHEMA legacy_vals CASCADE")


def test_groq_model_defaults_and_overrides(env_file, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file("DATABASE_URL=postgresql+asyncpg://u:p@h:1/db", "GROQ_API_KEY=k")
    s = config.load_settings()
    assert (s.groq_model, s.groq_model_fast) == ("openai/gpt-oss-120b", "openai/gpt-oss-20b")

    monkeypatch.setenv("GROQ_MODEL", "m-main")
    monkeypatch.setenv("GROQ_MODEL_FAST", "m-fast")
    s = config.load_settings()
    assert (s.groq_model, s.groq_model_fast) == ("m-main", "m-fast")


MIGRATION_002 = Path(__file__).resolve().parents[1] / "migrations" / "002_chat_messages_dedupe.sql"


async def test_chat_dedupe_migration(_schema: None) -> None:
    sql = MIGRATION_002.read_text(encoding="utf-8")
    async with test_engine.connect() as conn:
        await conn.exec_driver_sql("DROP SCHEMA IF EXISTS legacy_chat CASCADE")
        await conn.exec_driver_sql("CREATE SCHEMA legacy_chat")
        await conn.exec_driver_sql("SET search_path TO legacy_chat")
        # Pre-002 shape: no tool_call_id column.
        await conn.exec_driver_sql(
            "CREATE TABLE chat_messages (id varchar(36) primary key, session_id varchar(36), role varchar(20), "
            "content text, tool_name varchar(50), created_at timestamptz)"
        )
        rows = [
            # session A: one real log_interaction tool row, re-saved on 2 later turns
            ("t1", "A", "tool", '{"logged":1}', "log_interaction", "2026-01-01 10:00:01+00:00"),
            ("t1b", "A", "tool", '{"logged":1}', "log_interaction", "2026-01-01 10:01:01+00:00"),
            ("t1c", "A", "tool", '{"logged":1}', "log_interaction", "2026-01-01 10:02:01+00:00"),
            ("t2", "A", "tool", '{"ctx":1}', "get_hcp_context", "2026-01-01 10:01:00+00:00"),
            # genuinely repeated user/assistant messages: must be kept
            ("u1", "A", "user", "ok", None, "2026-01-01 10:00:00+00:00"),
            ("u2", "A", "user", "ok", None, "2026-01-01 10:01:00+00:00"),
            ("a1", "A", "assistant", "Done.", None, "2026-01-01 10:00:02+00:00"),
            ("a2", "A", "assistant", "Done.", None, "2026-01-01 10:01:02+00:00"),
            # same tool content in another session: not a duplicate
            ("t3", "B", "tool", '{"logged":1}', "log_interaction", "2026-01-01 11:00:00+00:00"),
        ]
        for r in rows:
            await conn.exec_driver_sql(
                "INSERT INTO chat_messages VALUES ($1, $2, $3, $4, $5, $6)", (*r[:5], datetime.fromisoformat(r[5]))
            )
        await conn.commit()

        # Multi-statement file (own BEGIN/COMMIT): run it like psql does, via the
        # simple-query protocol on the raw asyncpg connection.
        raw = (await conn.get_raw_connection()).driver_connection
        assert raw is not None
        await raw.execute(sql)
        await raw.execute(sql)  # idempotent

        kept = sorted(r[0] for r in (await conn.exec_driver_sql("SELECT id FROM chat_messages")).all())
        assert kept == ["a1", "a2", "t1", "t2", "t3", "u1", "u2"]  # earliest copy of t1 kept
        col = (
            await conn.exec_driver_sql(
                "SELECT data_type FROM information_schema.columns WHERE table_schema = 'legacy_chat' "
                "AND table_name = 'chat_messages' AND column_name = 'tool_call_id'"
            )
        ).scalar_one()
        assert col == "character varying"
        await conn.exec_driver_sql("SET search_path TO public")
        await conn.exec_driver_sql("DROP SCHEMA legacy_chat CASCADE")
        await conn.commit()
