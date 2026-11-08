"""Demo hardening: DEMO_MODE read-only user management, the global daily AI cap,
and the owner email ("Rep" column) on interactions."""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import ratelimit
from app.config import settings
from app.models import LlmUsage
from tests.conftest import USERS


# --------------------------------------------------------------- demo mode --
@pytest.fixture
def demo_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "demo_mode", True)


async def test_meta_is_public_and_reports_demo_mode(client_as, demo_mode: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_daily_cap", 123)
    anonymous = await client_as(None)
    resp = await anonymous.get("/api/meta")
    assert resp.status_code == 200
    assert resp.json() == {"demo_mode": True, "llm_daily_cap": 123}


async def test_meta_default_is_not_demo(client_as) -> None:
    assert (await (await client_as(None)).get("/api/meta")).json()["demo_mode"] is False


@pytest.mark.parametrize(
    "change",
    [{"is_active": False}, {"role": "rep"}, {"password": "a-brand-new-password"}],
    ids=["deactivate", "change-role", "reset-password"],
)
async def test_demo_mode_blocks_user_changes(client: httpx.AsyncClient, demo_mode: None, change: dict) -> None:
    target = USERS["manager"].id
    resp = await client.patch(f"/api/admin/reps/{target}", json=change)
    assert resp.status_code == 403
    assert "read-only in demo mode" in resp.json()["detail"]
    users = {u["id"]: u for u in (await client.get("/api/admin/reps")).json()}  # reading still works
    assert users[target]["is_active"] is True and users[target]["role"] == "manager"


async def test_demo_mode_blocks_adding_users(client: httpx.AsyncClient, demo_mode: None) -> None:
    resp = await client.post(
        "/api/admin/reps", json={"email": "new@test.example", "password": "long-enough-pass", "role": "rep"}
    )
    assert resp.status_code == 403
    assert all(u["email"] != "new@test.example" for u in (await client.get("/api/admin/reps")).json())


async def test_demo_accounts_can_still_sign_in_and_admin_reads_users(client_as, demo_mode: None) -> None:
    admin = await client_as("admin")
    assert (await admin.get("/api/admin/reps")).status_code == 200


async def test_without_demo_mode_user_changes_work(client: httpx.AsyncClient) -> None:
    resp = await client.patch(f"/api/admin/reps/{USERS['manager'].id}", json={"is_active": False})
    assert resp.status_code == 200 and resp.json()["is_active"] is False


# --------------------------------------------------------- daily AI cap -----
async def _voice(c: httpx.AsyncClient) -> httpx.Response:
    # consent=false fails right after the rate-limit dependency: a cheap, LLM-free AI request.
    return await c.post("/api/voice-notes/summarize", content=b"x", headers={"Content-Type": "audio/webm"})


async def test_daily_cap_is_shared_by_all_users(client_as, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_daily_cap", 2)
    rep_a, rep_b = await client_as("rep_a"), await client_as("rep_b")
    assert (await _voice(rep_a)).status_code == 400  # counted (1)
    assert (await _voice(rep_b)).status_code == 400  # counted (2)
    blocked = await _voice(rep_a)
    assert blocked.status_code == 429
    assert "daily limit of 2 AI requests" in blocked.json()["detail"]
    assert "you can still log interactions with the form" in blocked.json()["detail"]
    assert 1 <= int(blocked.headers["Retry-After"]) <= 86_400


async def test_daily_cap_counts_in_the_database(client_as, db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "llm_daily_cap", 5)
    rep = await client_as("rep_a")
    for _ in range(3):
        await _voice(rep)
    row = (await db.execute(select(LlmUsage).where(LlmUsage.day == ratelimit.utc_today()))).scalar_one()
    assert row.calls == 3


async def test_daily_cap_stops_counting_at_the_cap(db: AsyncSession) -> None:
    day = date(2026, 1, 1)
    results = [await ratelimit.count_daily_llm_call(db, 3, day) for _ in range(5)]
    assert results == [True, True, True, False, False]
    assert (await db.get(LlmUsage, day)).calls == 3  # type: ignore[union-attr]  # row exists after the first call
    assert await ratelimit.count_daily_llm_call(db, 3, date(2026, 1, 2)) is True  # a new day starts fresh


async def test_cap_zero_disables_it(db: AsyncSession) -> None:
    assert all([await ratelimit.count_daily_llm_call(db, 0) for _ in range(3)])
    assert (await db.execute(select(LlmUsage))).first() is None


async def test_per_minute_limit_message_is_friendly(client_as, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "rate_limit_llm_per_minute", 1)
    rep = await client_as("rep_a")
    await _voice(rep)
    limited = await _voice(rep)
    assert limited.status_code == 429
    assert limited.json()["detail"].startswith("You're sending AI requests too quickly (limit: 1 per minute).")


def test_seconds_until_utc_midnight() -> None:
    assert ratelimit.seconds_until_utc_midnight(datetime(2026, 3, 1, 23, 59, 30, tzinfo=timezone.utc)) == 30
    assert ratelimit.seconds_until_utc_midnight(datetime(2026, 3, 1, 0, 0, 0, tzinfo=timezone.utc)) == 86_400


def test_default_cap_is_500() -> None:
    from app.config import Settings

    assert Settings.model_fields["llm_daily_cap"].default == 500


# ------------------------------------------------------- Rep column data ----
async def test_interactions_carry_the_owner_email(client_as, hcp: dict) -> None:
    rep_a, rep_b, manager = await client_as("rep_a"), await client_as("rep_b"), await client_as("manager")
    created = await rep_a.post("/api/interactions", json={"hcp_id": hcp["id"], "raw_notes": "a"})
    assert created.status_code == 201 and created.json()["owner_email"] == USERS["rep_a"].email
    await rep_b.post("/api/interactions", json={"hcp_id": hcp["id"], "raw_notes": "b"})

    rows = (await manager.get("/api/interactions")).json()
    assert sorted(r["owner_email"] for r in rows) == sorted([USERS["rep_a"].email, USERS["rep_b"].email])

    one = (await manager.get(f"/api/interactions/{created.json()['id']}")).json()
    assert one["owner_email"] == USERS["rep_a"].email
    patched = await rep_a.patch(f"/api/interactions/{created.json()['id']}", json={"raw_notes": "a2"})
    assert patched.json()["owner_email"] == USERS["rep_a"].email

    # Reps still only see their own rows.
    assert {r["owner_email"] for r in (await rep_b.get("/api/interactions")).json()} == {USERS["rep_b"].email}
