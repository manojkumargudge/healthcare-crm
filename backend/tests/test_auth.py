"""Authentication, authorization (role x route), cross-rep isolation (HTTP, agent tools,
chat sessions, voice), rate limits, admin endpoints, and migration 008's backfill.

Roles: rep_a / rep_b are reps ("rep" = app user), manager reads all, admin does all.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import jwt
import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import config
from app.agent.tools import build_tools
from app.models import HCP, ChatMessage, FollowUp, Interaction, Material, User
from app.security import ALGORITHM, hash_password
from tests.conftest import TEST_PASSWORD, USERS, authz_for, test_engine

WAV = (Path(__file__).parent / "fixtures" / "voice_note_sample.wav").read_bytes()


async def _tool(db: AsyncSession, who: str, name: str, **kwargs: Any) -> dict:
    tool = next(t for t in build_tools(db, authz_for(who)) if t.name == name)
    return json.loads(await tool.coroutine(**kwargs))  # type: ignore[misc]  # StructuredTool.coroutine is Optional; always set by build_tools


def _fake_whisper(monkeypatch: pytest.MonkeyPatch, text: str = "Met Dr. Rao about CardioMax.") -> None:
    from groq.resources.audio.transcriptions import AsyncTranscriptions

    async def create(self: Any, **_kw: Any) -> Any:
        return SimpleNamespace(text=text)

    monkeypatch.setattr(AsyncTranscriptions, "create", create)


# =================================================================== login / tokens
async def test_login_returns_short_lived_token_and_user(client_as) -> None:
    anon = await client_as(None)
    resp = await anon.post("/auth/login", data={"username": "REP.A@test.example", "password": TEST_PASSWORD})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 15 * 60
    assert body["user"]["email"] == "rep.a@test.example" and body["user"]["role"] == "rep"
    assert "password" not in json.dumps(body)
    claims = jwt.decode(body["access_token"], config.settings.jwt_secret, algorithms=[ALGORITHM])
    assert claims["sub"] == USERS["rep_a"].id and claims["exp"] - claims["iat"] == 15 * 60
    me = await anon.get("/auth/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.json()["email"] == "rep.a@test.example"


@pytest.mark.parametrize(
    ("username", "password"),
    [("rep.a@test.example", "wrong password!!"), ("nobody@test.example", TEST_PASSWORD), ("", "")],
)
async def test_login_failures_are_indistinguishable(client_as, username: str, password: str) -> None:
    anon = await client_as(None)
    resp = await anon.post("/auth/login", data={"username": username, "password": password})
    assert resp.status_code in (401, 422)
    if resp.status_code == 401:
        assert resp.json()["detail"] == "Incorrect email or password"


async def test_inactive_user_cannot_log_in_and_existing_token_stops_working(client_as, db: AsyncSession) -> None:
    rep = await client_as("rep_a")
    assert (await rep.get("/auth/me")).status_code == 200
    user = await db.get(User, USERS["rep_a"].id)
    assert user is not None
    user.is_active = False
    await db.commit()
    assert (await rep.get("/auth/me")).status_code == 401  # immediately, not at expiry
    anon = await client_as(None)
    resp = await anon.post("/auth/login", data={"username": "rep.a@test.example", "password": TEST_PASSWORD})
    assert resp.status_code == 401


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(jwt.encode({"sub": "x", "type": "access", "iat": 0, "exp": 1}, "w" * 40, algorithm="HS256"), id="wrong-secret"),
        pytest.param("not-a-jwt", id="garbage"),
        pytest.param("", id="empty"),
    ],
)
async def test_bad_tokens_are_401(client_as, token: str) -> None:
    anon = await client_as(None)
    assert (await anon.get("/api/hcps", headers={"Authorization": f"Bearer {token}"})).status_code == 401


async def test_expired_and_non_access_tokens_are_401(client_as) -> None:
    from app.models import utcnow

    now = utcnow()
    secret = config.settings.jwt_secret
    expired = jwt.encode({"sub": USERS["admin"].id, "type": "access", "iat": now - timedelta(hours=1), "exp": now - timedelta(minutes=1)}, secret, algorithm=ALGORITHM)
    refresh = jwt.encode({"sub": USERS["admin"].id, "type": "refresh", "iat": now, "exp": now + timedelta(minutes=5)}, secret, algorithm=ALGORITHM)
    anon = await client_as(None)
    for token in (expired, refresh):
        assert (await anon.get("/auth/me", headers={"Authorization": f"Bearer {token}"})).status_code == 401


async def test_role_is_read_from_db_not_token(client_as, db: AsyncSession) -> None:
    rep = await client_as("rep_a")
    assert (await rep.get("/api/admin/reps")).status_code == 403
    user = await db.get(User, USERS["rep_a"].id)
    assert user is not None
    user.role = "admin"
    await db.commit()
    assert (await rep.get("/api/admin/reps")).status_code == 200  # same token, new role


def test_jwt_secret_is_required_and_long(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / ".env")
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h:1/db")
    monkeypatch.setenv("GROQ_API_KEY", "k")
    monkeypatch.delenv("JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        config.load_settings()
    monkeypatch.setenv("JWT_SECRET", "too-short")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        config.load_settings()


# =================================================================== role x route matrix
@pytest.fixture
async def world(client: httpx.AsyncClient, client_as, db: AsyncSession, fake_llm) -> dict[str, str]:
    """rep_a owns an interaction (with a follow-up) and a chat session."""
    hcp = (await client.post("/api/hcps", json={"name": "Dr. Matrix", "state": "Karnataka"})).json()
    rep_a = await client_as("rep_a")
    inter = (await rep_a.post("/api/interactions", json={"hcp_id": hcp["id"]})).json()
    fu = FollowUp(hcp_id=hcp["id"], owner_id=USERS["rep_a"].id, interaction_id=inter["id"],
                  due_date=__import__("datetime").date.today(), reason="Call back", status="Suggested")
    db.add(fu)
    db.add(ChatMessage(session_id="sess-a", owner_id=USERS["rep_a"].id, role="user", content="hi"))
    await db.commit()
    material = (await client.post("/api/materials", json={"name": "Matrix leaflet"})).json()
    product = (await client.get("/api/products", params={"q": "cardio"})).json()[0]
    return {"hcp": hcp["id"], "inter": inter["id"], "fu": fu.id, "material": material["id"], "product": product["id"]}


A, B, M, D = "rep_a", "rep_b", "manager", "admin"
ROLES = [None, A, B, M, D]


def _row(*statuses: int) -> dict:
    return dict(zip(ROLES, statuses, strict=True))


#  method, path template, body, expected status for (anon, rep_a, rep_b, manager, admin)
MATRIX = [
    ("GET", "/auth/me", None, _row(401, 200, 200, 200, 200)),
    ("GET", "/api/hcps", None, _row(401, 200, 200, 200, 200)),
    ("POST", "/api/hcps", {"name": "Dr. New"}, _row(401, 201, 201, 201, 201)),
    ("GET", "/api/hcps/{hcp}", None, _row(401, 200, 200, 200, 200)),
    ("GET", "/api/interactions", None, _row(401, 200, 200, 200, 200)),
    ("POST", "/api/interactions", {"hcp_id": "{hcp}"}, _row(401, 201, 201, 201, 201)),
    ("GET", "/api/interactions/{inter}", None, _row(401, 200, 404, 200, 200)),
    ("PATCH", "/api/interactions/{inter}", {"raw_notes": "x"}, _row(401, 200, 404, 403, 200)),
    ("GET", "/api/interactions/{inter}/follow-ups", None, _row(401, 200, 404, 200, 200)),
    ("POST", "/api/interactions/{inter}/follow-ups/suggestions", None, _row(401, 200, 404, 403, 200)),
    ("PATCH", "/api/follow-ups/{fu}", {"status": "Pending"}, _row(401, 200, 404, 403, 200)),
    ("GET", "/api/materials", None, _row(401, 200, 200, 200, 200)),
    ("POST", "/api/materials", {"name": "Rep idea"}, _row(401, 201, 201, 201, 201)),
    ("GET", "/api/products", None, _row(401, 200, 200, 200, 200)),
    ("POST", "/api/products", {"name": "Nope"}, _row(405, 405, 405, 405, 405)),
    ("POST", "/api/chat/turn", {"session_id": "s-{role}", "message": "hello"}, _row(401, 200, 200, 200, 200)),
    ("GET", "/api/chat/sess-a/history", None, _row(401, 200, 404, 200, 200)),
    ("POST", "/api/voice-notes/summarize?consent=true", "WAV", _row(401, 200, 200, 200, 200)),
    ("GET", "/api/admin/reps", None, _row(401, 403, 403, 403, 200)),
    ("POST", "/api/admin/reps", {"email": "new.rep@test.example", "password": "a-long-password-1", "role": "rep"}, _row(401, 403, 403, 403, 201)),
    ("PATCH", "/api/admin/reps/" + USERS["rep_b"].id, {"role": "rep"}, _row(401, 403, 403, 403, 200)),
    ("GET", "/api/admin/materials/pending", None, _row(401, 403, 403, 403, 200)),
    ("POST", "/api/admin/materials/{material}/approve", None, _row(401, 403, 403, 403, 200)),
    ("POST", "/api/admin/products", {"name": "NeuroCalm"}, _row(401, 403, 403, 403, 201)),
    ("PATCH", "/api/admin/products/{product}", {"name": "CardioMax"}, _row(401, 403, 403, 403, 200)),
    ("DELETE", "/api/admin/products/{product}", None, _row(401, 403, 403, 403, 204)),
]


def _fill(value: Any, ids: dict[str, str], role: str | None) -> Any:
    if isinstance(value, str):
        return value.format(**ids, role=role or "anon")
    if isinstance(value, dict):
        return {k: _fill(v, ids, role) for k, v in value.items()}
    return value


@pytest.mark.parametrize("role", ROLES, ids=lambda r: r or "anon")
@pytest.mark.parametrize(("method", "path", "body", "expected"), MATRIX, ids=[f"{m} {p}" for m, p, _b, _e in MATRIX])
async def test_role_route_matrix(
    world: dict[str, str], client_as, fake_llm, monkeypatch: pytest.MonkeyPatch,
    role: str | None, method: str, path: str, body: Any, expected: dict,
) -> None:
    _fake_whisper(monkeypatch)
    fake_llm(*([json.dumps({"follow_ups": [{"reason": "Send PDF", "days_from_now": 1}]})] if "suggestions" in path else
               ["Hello! How can I help?"] if "chat" in path else
               ["Summary."] if "voice" in path else []))
    c = await client_as(role)
    url = _fill(path, world, role)
    if body == "WAV":
        resp = await c.request(method, url, content=WAV, headers={"content-type": "audio/wav"})
    else:
        resp = await c.request(method, url, json=_fill(body, world, role) if body is not None else None)
    assert resp.status_code == expected[role], f"{role} {method} {url}: {resp.status_code} {resp.text[:200]}"


# =================================================================== scoping
async def test_list_scoping(client_as, client: httpx.AsyncClient) -> None:
    hcp = (await client.post("/api/hcps", json={"name": "Dr. Scope"})).json()
    a, b, m = await client_as("rep_a"), await client_as("rep_b"), await client_as("manager")
    ia = (await a.post("/api/interactions", json={"hcp_id": hcp["id"]})).json()
    ib = (await b.post("/api/interactions", json={"hcp_id": hcp["id"]})).json()
    assert ia["owner_id"] == USERS["rep_a"].id and ib["owner_id"] == USERS["rep_b"].id
    ids = lambda r: sorted(i["id"] for i in r.json())  # noqa: E731
    assert ids(await a.get("/api/interactions")) == [ia["id"]]
    assert ids(await b.get("/api/interactions", params={"hcp_id": hcp["id"]})) == [ib["id"]]
    assert ids(await m.get("/api/interactions")) == sorted([ia["id"], ib["id"]])
    assert ids(await client.get("/api/interactions")) == sorted([ia["id"], ib["id"]])


async def test_manager_can_write_own_rows(client_as, client: httpx.AsyncClient) -> None:
    hcp = (await client.post("/api/hcps", json={"name": "Dr. Mgr"})).json()
    m = await client_as("manager")
    own = (await m.post("/api/interactions", json={"hcp_id": hcp["id"]})).json()
    assert (await m.patch(f"/api/interactions/{own['id']}", json={"raw_notes": "mine"})).status_code == 200


# =================================================================== agent tools: same scoping
@pytest.fixture
async def two_reps(db: AsyncSession) -> dict[str, str]:
    hcp = HCP(name="Dr. Shared", state="Karnataka")
    db.add(hcp)
    await db.commit()
    rows = {}
    for who in ("rep_a", "rep_b"):
        row = Interaction(owner_id=USERS[who].id, hcp_id=hcp.id, ai_summary=f"secret of {who}")
        db.add(row)
        await db.commit()
        rows[who] = row.id
    return {"hcp": hcp.id, **rows}


async def test_agent_edit_cross_rep_denied(db: AsyncSession, two_reps: dict[str, str]) -> None:
    out = await _tool(db, "rep_b", "edit_interaction", interaction_id=two_reps["rep_a"], updates=json.dumps({"raw_notes": "pwned"}))
    assert out == {"error": f"No interaction found with id {two_reps['rep_a']}"}
    out = await _tool(db, "manager", "edit_interaction", interaction_id=two_reps["rep_a"], updates=json.dumps({"raw_notes": "x"}))
    assert "Not allowed" in out["error"]
    row = await db.get(Interaction, two_reps["rep_a"])
    await db.refresh(row)
    assert row is not None and row.raw_notes is None and row.edit_history == []
    ok = await _tool(db, "rep_a", "edit_interaction", interaction_id=two_reps["rep_a"], updates=json.dumps({"raw_notes": "mine"}))
    assert ok["status"] == "updated"


async def test_agent_context_and_recommendation_only_see_own(db: AsyncSession, two_reps: dict[str, str], fake_llm) -> None:
    ctx = await _tool(db, "rep_b", "get_hcp_context", hcp_id=two_reps["hcp"])
    assert [i["summary"] for i in ctx["recent_interactions"]] == ["secret of rep_b"]
    mgr = await _tool(db, "manager", "get_hcp_context", hcp_id=two_reps["hcp"])
    assert sorted(i["summary"] for i in mgr["recent_interactions"]) == ["secret of rep_a", "secret of rep_b"]
    fake = fake_llm("Bring the study.")
    await _tool(db, "rep_b", "suggest_next_best_action", hcp_id=two_reps["hcp"])
    assert "secret of rep_b" in fake.calls[0]["messages"] and "secret of rep_a" not in fake.calls[0]["messages"]


async def test_agent_follow_up_on_other_reps_interaction_denied(db: AsyncSession, two_reps: dict[str, str]) -> None:
    out = await _tool(db, "rep_b", "schedule_follow_up", hcp_id=two_reps["hcp"], reason="x", interaction_id=two_reps["rep_a"])
    assert out["error"].startswith("No interaction found")
    ok = await _tool(db, "rep_b", "schedule_follow_up", hcp_id=two_reps["hcp"], reason="mine", interaction_id=two_reps["rep_b"])
    fu = await db.get(FollowUp, ok["follow_up_id"])
    assert fu is not None and fu.owner_id == USERS["rep_b"].id


async def test_agent_log_is_owned_by_caller(db: AsyncSession, two_reps: dict[str, str], fake_llm) -> None:
    fake_llm(json.dumps({"summary": "s"}))
    out = await _tool(db, "rep_b", "log_interaction", hcp_id=two_reps["hcp"], raw_text="notes")
    row = await db.get(Interaction, out["interaction_id"])
    assert row is not None and row.owner_id == USERS["rep_b"].id


async def test_chat_session_of_another_rep_is_off_limits(client_as, client: httpx.AsyncClient, fake_llm) -> None:
    fake_llm("hi A", "hi B")
    a, b = await client_as("rep_a"), await client_as("rep_b")
    assert (await a.post("/api/chat/turn", json={"session_id": "shared-id", "message": "secret plan"})).status_code == 200
    resp = await b.post("/api/chat/turn", json={"session_id": "shared-id", "message": "what did they say?"})
    assert resp.status_code == 404
    assert (await b.get("/api/chat/shared-id/history")).status_code == 404
    assert (await client.post("/api/chat/turn", json={"session_id": "shared-id", "message": "x"})).status_code == 404
    history = (await (await client_as("manager")).get("/api/chat/shared-id/history")).json()
    assert [h["content"] for h in history if h["role"] == "user"] == ["secret plan"]


async def test_chat_reply_never_returns_other_reps_interaction(client_as, client: httpx.AsyncClient, fake_llm, db: AsyncSession) -> None:
    """Even if the model claims to have logged someone else's interaction id, it isn't returned."""
    hcp = (await client.post("/api/hcps", json={"name": "Dr. Leak"})).json()
    other = Interaction(owner_id=USERS["rep_a"].id, hcp_id=hcp["id"])
    db.add(other)
    await db.commit()
    fake_llm(AIMessage(content="Done."))
    b = await client_as("rep_b")
    resp = (await b.post("/api/chat/turn", json={"session_id": "s-leak", "message": "x"})).json()
    assert resp["interaction"] is None


# =================================================================== voice + rate limits
async def test_voice_requires_login(client_as, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_whisper(monkeypatch)
    anon = await client_as(None)
    resp = await anon.post("/api/voice-notes/summarize", params={"consent": "true"}, content=WAV, headers={"content-type": "audio/wav"})
    assert resp.status_code == 401


async def test_llm_rate_limit_is_per_rep_and_shared_across_ai_endpoints(
    client_as, client: httpx.AsyncClient, fake_llm, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.settings, "rate_limit_llm_per_minute", 3)
    _fake_whisper(monkeypatch)
    fake_llm("chat 1", "Summary.", "chat 2", "chat for rep b")
    a, b = await client_as("rep_a"), await client_as("rep_b")
    hcp = (await client.post("/api/hcps", json={"name": "Dr. Rate"})).json()
    assert (await a.post("/api/chat/turn", json={"session_id": "r1", "message": "1"})).status_code == 200
    voice = await a.post("/api/voice-notes/summarize", params={"consent": "true"}, content=WAV, headers={"content-type": "audio/wav"})
    assert voice.status_code == 200
    assert (await a.post("/api/chat/turn", json={"session_id": "r1", "message": "2"})).status_code == 200
    inter = (await a.post("/api/interactions", json={"hcp_id": hcp["id"]})).json()  # not an AI endpoint
    limited = await a.post(f"/api/interactions/{inter['id']}/follow-ups/suggestions")
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) >= 1
    assert (await b.post("/api/chat/turn", json={"session_id": "r2", "message": "b"})).status_code == 200  # own budget


def test_rate_limiter_window_slides() -> None:
    from app.ratelimit import RateLimiter

    rl = RateLimiter()
    assert rl.hit("u", 2, now=0.0) is None and rl.hit("u", 2, now=1.0) is None
    assert rl.hit("u", 2, now=2.0) == pytest.approx(58.0)
    assert rl.hit("u", 2, now=60.5) is None  # first hit expired


# =================================================================== admin endpoints
async def test_approve_material_reruns_compliance_and_clears_stale_flags(client_as, client: httpx.AsyncClient, make_hcp) -> None:
    a = await client_as("rep_a")
    ka = await make_hcp(name="Dr. K", state="Karnataka")
    ca = await make_hcp(name="Dr. C", state="California")
    clean = (await a.post("/api/interactions", json={"hcp_id": ka["id"], "materials_shared": ["Rep slide deck"]})).json()
    both = (await a.post("/api/interactions", json={"hcp_id": ca["id"], "materials_shared": ["rep SLIDE deck"],
                                                    "samples_dropped": [{"product": "CardioMax", "qty": 3}]})).json()
    unrelated = (await a.post("/api/interactions", json={"hcp_id": ka["id"]})).json()
    assert clean["compliance_notes"] == "Materials pending approval: Rep slide deck"
    material = next(m for m in (await client.get("/api/admin/materials/pending")).json() if m["name"] == "Rep slide deck")

    result = (await client.post(f"/api/admin/materials/{material['id']}/approve")).json()
    assert result["material"]["approved"] is True
    assert (result["interactions_rechecked"], result["flags_cleared"]) == (2, 1)
    clean_now = (await a.get(f"/api/interactions/{clean['id']}")).json()
    both_now = (await a.get(f"/api/interactions/{both['id']}")).json()
    assert (clean_now["compliance_flag"], clean_now["compliance_notes"]) == (False, None)
    assert both_now["compliance_flag"] is True
    assert both_now["compliance_notes"] == "CardioMax: 3 units exceeds the 2-unit limit for California"
    assert (await a.get(f"/api/interactions/{unrelated['id']}")).json()["compliance_notes"] is None
    assert (await client.post("/api/admin/materials/nope/approve")).status_code == 404


async def test_admin_product_management(client: httpx.AsyncClient) -> None:
    created = await client.post("/api/admin/products", json={"name": "  NeuroCalm "})
    assert created.status_code == 201 and created.json()["name"] == "NeuroCalm"
    assert (await client.post("/api/admin/products", json={"name": "neurocalm"})).status_code == 409
    pid = created.json()["id"]
    assert (await client.patch(f"/api/admin/products/{pid}", json={"name": "NeuroCalm XR"})).json()["name"] == "NeuroCalm XR"
    assert (await client.patch(f"/api/admin/products/{pid}", json={"name": "cardiomax"})).status_code == 409
    assert (await client.delete(f"/api/admin/products/{pid}")).status_code == 204
    assert (await client.get("/api/products", params={"q": "neuro"})).json() == []
    assert (await client.delete(f"/api/admin/products/{pid}")).status_code == 404


async def test_admin_user_management_and_last_admin_guard(client: httpx.AsyncClient, client_as) -> None:
    new = await client.post("/api/admin/reps", json={"email": "New.Rep@test.example", "password": "a-long-password-1", "role": "rep"})
    assert new.status_code == 201 and new.json()["role"] == "rep" and "password" not in new.text
    assert (await client.post("/api/admin/reps", json={"email": "new.rep@TEST.example", "password": "a-long-password-1"})).status_code == 409
    assert (await client.post("/api/admin/reps", json={"email": "short@test.example", "password": "short"})).status_code == 422
    assert (await client.post("/api/admin/reps", json={"email": "not-an-email", "password": "a-long-password-1"})).status_code == 422
    uid = new.json()["id"]
    anon = await client_as(None)
    login = lambda pw: anon.post("/auth/login", data={"username": "new.rep@test.example", "password": pw})  # noqa: E731
    assert (await login("a-long-password-1")).status_code == 200
    await client.patch(f"/api/admin/reps/{uid}", json={"password": "another-long-pass-2"})
    assert (await login("a-long-password-1")).status_code == 401 and (await login("another-long-pass-2")).status_code == 200
    assert (await client.patch(f"/api/admin/reps/{uid}", json={"is_active": False})).json()["is_active"] is False
    assert (await login("another-long-pass-2")).status_code == 401

    admin_id = USERS["admin"].id
    assert (await client.patch(f"/api/admin/reps/{admin_id}", json={"role": "rep"})).status_code == 409
    assert (await client.patch(f"/api/admin/reps/{admin_id}", json={"is_active": False})).status_code == 409
    await client.patch(f"/api/admin/reps/{USERS['manager'].id}", json={"role": "admin"})
    assert (await client.patch(f"/api/admin/reps/{admin_id}", json={"role": "manager"})).status_code == 200


async def test_admin_cli_create_user(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import admin
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(admin, "AsyncSessionLocal", TestSessionLocal)
    uid = await admin.create_user("cli.rep@test.example", "rep", "cli-password-123")
    user = await db.get(User, uid)
    assert user is not None and user.role == "rep" and user.password_hash.startswith("$argon2")
    assert any(line.startswith("cli.rep@test.example\trep\tactive") for line in await admin.list_users())


# =================================================================== item 7: products in chat
async def test_chat_typo_product_is_matched_and_original_text_kept(db: AsyncSession, two_reps: dict[str, str], fake_llm) -> None:
    fake_llm(json.dumps({"summary": "s", "products_discussed": ["cardiomx", "OncoBoost", "Zylotrex"],
                         "samples_dropped": [{"product": "oncobost", "qty": 2}]}))
    out = await _tool(db, "rep_a", "log_interaction", hcp_id=two_reps["hcp"], raw_text="notes")
    assert out["products_discussed"] == ["CardioMax", "OncoBoost"]
    assert out["unmatched_products"] == ["Zylotrex"]
    assert out["samples_dropped"] == [{"product": "OncoBoost", "qty": 2}]
    assert out["product_matches"] == [
        {"field": "products_discussed", "product": "CardioMax", "from": "cardiomx"},
        {"field": "samples_dropped", "product": "OncoBoost", "from": "oncobost"},
    ]


async def test_chat_unknown_sample_product_saves_nothing_and_offers_choices(db: AsyncSession, two_reps: dict[str, str], fake_llm) -> None:
    before = len((await db.execute(select(Interaction))).scalars().all())
    fake_llm(json.dumps({"summary": "s", "samples_dropped": [{"product": "CardioPlusXR", "qty": 1}, {"product": "CardioMax", "qty": 1}]}),
             json.dumps({"summary": "s"}))
    out = await _tool(db, "rep_a", "log_interaction", hcp_id=two_reps["hcp"], raw_text="left a CardioPlusXR")
    assert out["status"] == "needs_product_choice" and out["saved"] is False
    assert out["unknown_sample_products"] == ["CardioPlusXR"]
    assert "CardioMax" in out["choices"]["CardioPlusXR"]
    assert len((await db.execute(select(Interaction))).scalars().all()) == before  # nothing saved

    # The rep picks; the agent retries with explicit catalog samples.
    ok = await _tool(db, "rep_a", "log_interaction", hcp_id=two_reps["hcp"], raw_text="left a CardioPlusXR",
                     samples=[{"product": "CardioMax", "qty": 2}])
    assert ok["status"] == "logged" and ok["samples_dropped"] == [{"product": "CardioMax", "qty": 2}]


async def test_chat_edit_with_unknown_sample_changes_nothing(db: AsyncSession, two_reps: dict[str, str]) -> None:
    out = await _tool(db, "rep_a", "edit_interaction", interaction_id=two_reps["rep_a"],
                      updates=json.dumps({"samples_dropped": [{"product": "Mystery", "qty": 1}], "raw_notes": "x"}))
    assert out["status"] == "needs_product_choice"
    row = await db.get(Interaction, two_reps["rep_a"])
    await db.refresh(row)
    assert row is not None and row.samples_dropped == [] and row.raw_notes is None


# =================================================================== migration 008
MIGRATION_008 = Path(__file__).resolve().parents[1] / "migrations" / "008_users_ownership.sql"


async def _legacy_schema(conn: Any, name: str) -> None:
    await conn.exec_driver_sql(f"DROP SCHEMA IF EXISTS {name} CASCADE")
    await conn.exec_driver_sql(f"CREATE SCHEMA {name}")
    await conn.exec_driver_sql(f"SET search_path TO {name}")
    for table in ("interactions", "chat_messages", "follow_ups"):
        await conn.exec_driver_sql(f"CREATE TABLE {table} (id varchar(36) primary key)")
    await conn.exec_driver_sql("ALTER TABLE interactions ADD COLUMN materials_shared json")
    await conn.exec_driver_sql("INSERT INTO interactions VALUES ('i1', '[]'), ('i2', '[]')")
    await conn.exec_driver_sql("INSERT INTO chat_messages VALUES ('c1')")
    await conn.commit()


async def test_migration_008_backfills_to_seed_admin(_schema: None) -> None:
    sql = MIGRATION_008.read_text(encoding="utf-8")
    async with test_engine.connect() as conn:
        await _legacy_schema(conn, "legacy_008")
        raw = (await conn.get_raw_connection()).driver_connection
        assert raw is not None
        await raw.execute("SELECT set_config('app.seed_admin_email', 'boss@test.example', false)")
        await raw.execute("SELECT set_config('app.seed_admin_password_hash', $1, false)", hash_password("boss-password-123"))
        await raw.execute(sql)
        await raw.execute(sql)  # idempotent
        admin = (await conn.exec_driver_sql("SELECT id, role FROM users WHERE email = 'boss@test.example'")).one()
        assert admin[1] == "admin"
        owners = {r[0] for r in (await conn.exec_driver_sql("SELECT owner_id FROM interactions UNION SELECT owner_id FROM chat_messages")).all()}
        assert owners == {admin[0]}
        nullable = dict(
            (str(r[0]), str(r[1])) for r in (await conn.exec_driver_sql(
                "SELECT table_name, is_nullable FROM information_schema.columns "
                "WHERE table_schema = 'legacy_008' AND column_name = 'owner_id'")).all()
        )
        assert nullable == {"interactions": "NO", "chat_messages": "NO", "follow_ups": "NO"}
        await conn.exec_driver_sql("SET search_path TO public")
        await conn.exec_driver_sql("DROP SCHEMA legacy_008 CASCADE")
        await conn.commit()


async def test_migration_008_refuses_to_orphan_rows_without_admin(_schema: None) -> None:
    import asyncpg

    sql = MIGRATION_008.read_text(encoding="utf-8")
    async with test_engine.connect() as conn:
        await _legacy_schema(conn, "legacy_008b")
        raw = (await conn.get_raw_connection()).driver_connection
        assert raw is not None
        await raw.execute("SELECT set_config('app.seed_admin_email', '', false)")
        with pytest.raises(asyncpg.exceptions.RaiseError, match="need an owner"):
            await raw.execute(sql)
        await raw.execute("ROLLBACK")
        owner_col = (await conn.exec_driver_sql(
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'legacy_008b' AND column_name = 'owner_id'")).scalar_one()
        assert owner_col == 0  # whole migration rolled back
        await conn.exec_driver_sql("SET search_path TO public")
        await conn.exec_driver_sql("DROP SCHEMA legacy_008b CASCADE")
        await conn.commit()


async def test_seed_admin_at_startup(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.catalog import ensure_seed_admin

    monkeypatch.setattr(config.settings, "admin_email", "Founder@test.example")
    monkeypatch.setattr(config.settings, "admin_password", "founder-password-1")
    uid = await ensure_seed_admin(db)
    assert uid is not None and await ensure_seed_admin(db) == uid  # idempotent
    user = await db.get(User, uid)
    assert user is not None and user.role == "admin"
    _ = Material  # (imported for type completeness in this module)
