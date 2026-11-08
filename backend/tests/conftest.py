"""Shared test fixtures.

- Points the app at TEST_DATABASE_URL (a disposable Postgres) *before* any
  `app.*` import, because `app.config.settings` and `app.database.engine`
  are created at import time.
- Forces a dummy GROQ_API_KEY and blocks every real Groq call: any test that
  reaches the LLM without explicitly faking it fails loudly.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://hcp_user:hcp_pass@127.0.0.1:5432/hcp_crm_test",
)

# Safety guard: the suite drops and recreates every table, so refuse to run
# against anything that isn't clearly a throwaway test database.
from sqlalchemy.engine import make_url  # noqa: E402

try:
    _test_db_name = make_url(TEST_DATABASE_URL).database or ""
except Exception as exc:  # malformed URL
    pytest.exit(f"Refusing to run: TEST_DATABASE_URL is not a valid database URL ({exc}).", returncode=4)
if not _test_db_name.endswith("_test"):
    pytest.exit(
        f"Refusing to run: TEST_DATABASE_URL database name {_test_db_name!r} must end with '_test' "
        "(the suite drops and recreates all tables).",
        returncode=4,
    )

# Env vars take precedence over the root .env in pydantic-settings, so a
# developer's real key in .env is never picked up by the test run.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["GROQ_API_KEY"] = "test-key-not-a-real-groq-key"
os.environ["JWT_SECRET"] = "test-only-jwt-secret-0123456789-abcdefghijklmnop"
for _var in ("ADMIN_EMAIL", "ADMIN_PASSWORD"):  # tests create their own users
    os.environ.pop(_var, None)

import httpx  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_groq import ChatGroq  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app import models  # noqa: E402,F401  (registers tables on Base.metadata)
from app.database import Base, get_db  # noqa: E402,F401
from app.main import app  # noqa: E402
from app.catalog import SEED_PRODUCTS  # noqa: E402
from app.models import Product, gen_uuid, utcnow  # noqa: E402
from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: E402

TEST_PRODUCTS = (*SEED_PRODUCTS, "A", "B", "C", "X", "DrugX", "Good", "ok")

from app.authz import Authz, Principal  # noqa: E402
from app.models import User  # noqa: E402
from app.ratelimit import llm_limiter  # noqa: E402
from app.security import create_access_token, hash_password  # noqa: E402

# Fixed test users (recreated for every test). Hashes are computed once: argon2 is slow on purpose.
TEST_PASSWORD = "correct horse battery staple"
_HASH = hash_password(TEST_PASSWORD)
USERS = {
    "admin": Principal(id="00000000-0000-0000-0000-00000000000a", email="admin@test.example", role="admin"),
    "manager": Principal(id="00000000-0000-0000-0000-00000000000b", email="manager@test.example", role="manager"),
    "rep_a": Principal(id="00000000-0000-0000-0000-00000000000c", email="rep.a@test.example", role="rep"),
    "rep_b": Principal(id="00000000-0000-0000-0000-00000000000d", email="rep.b@test.example", role="rep"),
}
ADMIN_ID = USERS["admin"].id
ADMIN_AUTHZ = Authz(USERS["admin"])


def authz_for(name: str) -> Authz:
    return Authz(USERS[name])


def auth_header(name: str) -> dict[str, str]:
    token, _ = create_access_token(USERS[name].id, USERS[name].role)
    return {"Authorization": f"Bearer {token}"}

# NullPool: every session opens a fresh connection on the current event loop,
# so pytest-asyncio's per-test loops never share an asyncpg connection.
test_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
TestSessionLocal = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)

_TABLES = ", ".join(t.name for t in Base.metadata.sorted_tables)


# --------------------------------------------------------------------------
# Groq guard
# --------------------------------------------------------------------------
class GroqCalledError(AssertionError):
    pass


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """`live` tests hit the real Groq API: skip them unless explicitly enabled."""
    if os.environ.get("RUN_LIVE_GROQ") == "1":
        return
    skip = pytest.mark.skip(reason="live Groq test; set RUN_LIVE_GROQ=1 and LIVE_GROQ_API_KEY to run")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _block_real_groq(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("live"):
        return  # live tests deliberately call Groq
    async def _refuse(*_a: Any, **_kw: Any) -> Any:
        raise GroqCalledError("Test attempted a real Groq call; fake the LLM instead.")

    def _refuse_sync(*_a: Any, **_kw: Any) -> Any:
        raise GroqCalledError("Test attempted a real Groq call; fake the LLM instead.")

    monkeypatch.setattr(ChatGroq, "ainvoke", _refuse)
    monkeypatch.setattr(ChatGroq, "invoke", _refuse_sync)
    monkeypatch.setattr(ChatGroq, "_agenerate", _refuse)
    monkeypatch.setattr(ChatGroq, "_generate", _refuse_sync)
    # Audio (Whisper) goes through the Groq SDK directly, not ChatGroq.
    from groq.resources.audio.transcriptions import AsyncTranscriptions, Transcriptions

    monkeypatch.setattr(AsyncTranscriptions, "create", _refuse)
    monkeypatch.setattr(Transcriptions, "create", _refuse_sync)


class FakeLLM:
    """Stand-in for `app.agent.llm.ainvoke_resilient`.

    Queue AIMessages (or plain strings) in the order the code under test will
    request them; every call is recorded in `.calls`.
    """

    def __init__(self, *responses: AIMessage | str) -> None:
        self._responses = [r if isinstance(r, AIMessage) else AIMessage(content=r) for r in responses]
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, messages: Any, temperature: float = 0.2, tools: Any = None, fast: bool = False):
        self.calls.append({"messages": messages, "temperature": temperature, "tools": tools, "fast": fast})
        if not self._responses:
            raise AssertionError("FakeLLM ran out of queued responses")
        return self._responses.pop(0), "fake-model"


@pytest.fixture
def fake_llm(monkeypatch: pytest.MonkeyPatch):
    """Factory: `fake_llm(resp1, resp2, ...)` patches every ainvoke_resilient import site."""

    def _install(*responses: AIMessage | str) -> FakeLLM:
        fake = FakeLLM(*responses)
        monkeypatch.setattr("app.agent.tools.ainvoke_resilient", fake)
        monkeypatch.setattr("app.agent.graph.ainvoke_resilient", fake)
        monkeypatch.setattr("app.follow_ups.ainvoke_resilient", fake)
        monkeypatch.setattr("app.voice_notes.ainvoke_resilient", fake)
        return fake

    return _install


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
@pytest.fixture(scope="session")
def _schema() -> Iterator[None]:
    """Build the test schema the way real databases get it: every migration, from empty.

    (Not Base.metadata.create_all: tests must run on the migrated schema, and
    test_migrations checks that it matches the models.)
    """
    from app.db_migrations import apply_all, reset_test_schema

    async def _reset() -> None:
        await reset_test_schema(TEST_DATABASE_URL)
        await apply_all(TEST_DATABASE_URL)

    asyncio.run(_reset())
    yield


@pytest.fixture
async def db(_schema: None) -> AsyncIterator[AsyncSession]:
    async with test_engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
    async with TestSessionLocal() as session:
        # The product catalog is admin-only (no API writes), so tests start with one:
        # the starter products plus the placeholder names tests use for samples.
        await session.execute(
            pg_insert(Product).values([{"id": gen_uuid(), "name": n, "created_at": utcnow()} for n in TEST_PRODUCTS])
        )
        session.add_all(
            User(id=p.id, email=p.email, password_hash=_HASH, role=p.role, is_active=True) for p in USERS.values()
        )
        await session.commit()
        yield session


@pytest.fixture(autouse=True)
def _fresh_rate_limits() -> Iterator[None]:
    llm_limiter.reset()
    yield
    llm_limiter.reset()


@pytest.fixture
async def client(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    # ASGITransport does not run startup events, so init_db() (which uses the
    # import-time engine) is skipped; `_schema` already created the tables.
    transport = httpx.ASGITransport(app=app)
    # The default client is signed in as the admin (full access); use client_as(...) for other roles.
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=auth_header("admin")) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
async def client_as(client: httpx.AsyncClient):
    """client_as("rep_a"|"rep_b"|"manager"|"admin"|None) -> client with that user's token (None = anonymous)."""
    opened: list[httpx.AsyncClient] = []

    async def make(name: str | None) -> httpx.AsyncClient:
        headers = auth_header(name) if name else {}
        c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers)
        opened.append(c)
        return c

    yield make
    for c in opened:
        await c.aclose()


@pytest.fixture
async def hcp(client: httpx.AsyncClient) -> dict[str, Any]:
    resp = await client.post(
        "/api/hcps",
        json={"name": "Dr. Test Default", "specialty": "Cardiology", "state": "Karnataka", "tier": "A"},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
async def make_hcp(client: httpx.AsyncClient):
    async def _make(**overrides: Any) -> dict[str, Any]:
        body = {"name": "Dr. Factory", **overrides}
        resp = await client.post("/api/hcps", json=body)
        assert resp.status_code == 201, resp.text
        return resp.json()

    return _make
