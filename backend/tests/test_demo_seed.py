"""python -m app.demo_seed: demo users, sample data, and the guarded --reset."""

from __future__ import annotations

import pytest
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import demo_seed
from app.models import HCP, FollowUp, Interaction, Material, User
from tests.conftest import TestSessionLocal

PASSWORD = "demo-password-for-tests"


@pytest.fixture
def seeded_env(monkeypatch: pytest.MonkeyPatch, db: AsyncSession) -> None:
    """Point the seed at the test database (already migrated by conftest)."""

    async def no_migrations() -> None:
        return None

    monkeypatch.setattr(demo_seed, "init_db", no_migrations)
    monkeypatch.setattr(demo_seed, "AsyncSessionLocal", TestSessionLocal)
    monkeypatch.setattr(demo_seed.settings, "admin_email", None)  # ignore a developer's root .env admin
    for attr in ("demo_rep_password", "demo_manager_password", "demo_admin_password"):
        monkeypatch.setattr(demo_seed.settings, attr, PASSWORD)


async def _count(db: AsyncSession, model: type, *where: ColumnElement[bool]) -> int:
    return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


async def test_seed_creates_demo_users_and_sample_data(seeded_env: None, db: AsyncSession, client_as) -> None:
    await demo_seed.seed_demo()

    users = {u.email: u.role for u in (await db.execute(select(User))).scalars() if u.email.startswith("demo-")}
    assert users == {
        "demo-rep@example.com": "rep", "demo-manager@example.com": "manager", "demo-admin@example.com": "admin",
    }
    rep_id = (await db.execute(select(User.id).where(User.email == "demo-rep@example.com"))).scalar_one()
    assert await _count(db, HCP) == len(demo_seed.DEMO_HCPS)
    assert await _count(db, Interaction, Interaction.owner_id == rep_id) == 4
    assert await _count(db, FollowUp, FollowUp.owner_id == rep_id) == 2
    # Two flags to demo: a Vermont sample over the limit, and a rep-added material pending approval.
    flagged = (await db.execute(select(Interaction).where(Interaction.compliance_flag.is_(True)))).scalars().all()
    notes = sorted(i.compliance_notes for i in flagged)
    assert len(notes) == 2
    assert any(demo_seed.PENDING_MATERIAL in n for n in notes) and any("OncoBoost" in n for n in notes)
    assert (await db.execute(select(Material.approved).where(Material.name == demo_seed.PENDING_MATERIAL))).scalar_one() is False

    # The demo logins work through the real sign-in endpoint.
    anonymous = await client_as(None)
    resp = await anonymous.post("/auth/login", data={"username": "demo-manager@example.com", "password": PASSWORD})
    assert resp.status_code == 200, resp.text


async def test_seed_is_idempotent(seeded_env: None, db: AsyncSession) -> None:
    await demo_seed.seed_demo()
    await demo_seed.seed_demo()
    assert await _count(db, User, User.email.like("demo-%")) == 3
    assert await _count(db, Interaction) == 4
    assert await _count(db, HCP) == len(demo_seed.DEMO_HCPS)


async def test_reset_wipes_visitor_data(seeded_env: None, db: AsyncSession) -> None:
    await demo_seed.seed_demo()
    db.add(HCP(name="Dr. Added By A Visitor"))
    await db.commit()

    await demo_seed.seed_demo(reset=True)

    async with TestSessionLocal() as fresh:
        assert await _count(fresh, HCP, HCP.name == "Dr. Added By A Visitor") == 0
        assert await _count(fresh, Interaction) == 4
        assert await _count(fresh, User) == 3  # conftest's test users were wiped too


async def test_reset_refuses_a_database_that_is_not_a_demo(seeded_env: None, db: AsyncSession) -> None:
    # conftest created ordinary users and there is no demo admin.
    with pytest.raises(SystemExit, match="refusing to reset"):
        await demo_seed.seed_demo(reset=True)
    assert await _count(db, User) == 4  # nothing was wiped


async def test_missing_passwords_fail_before_touching_the_db(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo_seed.settings, "demo_admin_password", None)

    async def must_not_run() -> None:
        raise AssertionError("database touched")

    monkeypatch.setattr(demo_seed, "init_db", must_not_run)
    with pytest.raises(SystemExit, match="DEMO_ADMIN_PASSWORD"):
        await demo_seed.seed_demo()


def test_cli_rejects_unknown_arguments() -> None:
    assert demo_seed.main(["demo_seed", "--wipe"]) == 2
    assert demo_seed.main(["demo_seed", "--force"]) == 2  # --force only makes sense with --reset
