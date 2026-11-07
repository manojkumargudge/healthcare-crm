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
    "postgresql+asyncpg://hcp_user:hcp_pass@localhost:5432/hcp_crm_test",
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

# Env vars take precedence over backend/.env in pydantic-settings, so a
# developer's real key in .env is never picked up by the test run.
os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["GROQ_API_KEY"] = "test-key-not-a-real-groq-key"

import httpx  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402
from langchain_groq import ChatGroq  # noqa: E402
from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from app import models  # noqa: E402,F401  (registers tables on Base.metadata)
from app.database import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402

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


@pytest.fixture(autouse=True)
def _block_real_groq(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _refuse(*_a: Any, **_kw: Any) -> Any:
        raise GroqCalledError("Test attempted a real Groq call; fake the LLM instead.")

    def _refuse_sync(*_a: Any, **_kw: Any) -> Any:
        raise GroqCalledError("Test attempted a real Groq call; fake the LLM instead.")

    monkeypatch.setattr(ChatGroq, "ainvoke", _refuse)
    monkeypatch.setattr(ChatGroq, "invoke", _refuse_sync)
    monkeypatch.setattr(ChatGroq, "_agenerate", _refuse)
    monkeypatch.setattr(ChatGroq, "_generate", _refuse_sync)


class FakeLLM:
    """Stand-in for `app.agent.llm.ainvoke_resilient`.

    Queue AIMessages (or plain strings) in the order the code under test will
    request them; every call is recorded in `.calls`.
    """

    def __init__(self, *responses: AIMessage | str) -> None:
        self._responses = [r if isinstance(r, AIMessage) else AIMessage(content=r) for r in responses]
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, messages: Any, temperature: float = 0.2, tools: Any = None, heavy: bool = False):
        self.calls.append({"messages": messages, "temperature": temperature, "tools": tools, "heavy": heavy})
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
        return fake

    return _install


# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
@pytest.fixture(scope="session")
def _schema() -> Iterator[None]:
    async def _reset() -> None:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_reset())
    yield


@pytest.fixture
async def db(_schema: None) -> AsyncIterator[AsyncSession]:
    async with test_engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE {_TABLES} RESTART IDENTITY CASCADE"))
    async with TestSessionLocal() as session:
        yield session


@pytest.fixture
async def client(db: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    async def _override_get_db() -> AsyncIterator[AsyncSession]:
        async with TestSessionLocal() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    # ASGITransport does not run startup events, so init_db() (which uses the
    # import-time engine) is skipped; `_schema` already created the tables.
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


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
