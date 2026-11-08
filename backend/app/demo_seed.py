"""Demo data for a public deployment.

    python -m app.demo_seed            # create/refresh demo users; add sample data if the DB has none
    python -m app.demo_seed --reset    # wipe ALL app data, then seed again (nightly job)

Users (passwords from DEMO_REP_PASSWORD, DEMO_MANAGER_PASSWORD, DEMO_ADMIN_PASSWORD):
    demo-rep@example.com      rep
    demo-manager@example.com  manager
    demo-admin@example.com    admin

Applies migrations first, so it also works on a brand-new database. --reset
refuses to wipe a database that has users but no demo admin (i.e. a database
that was never seeded as a demo), unless --force is also given.
Passwords are never printed.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools import apply_compliance
from app.catalog import ensure_seed_admin, seed_materials, seed_products
from app.config import settings
from app.database import AsyncSessionLocal, init_db
from app.models import HCP, FollowUp, Interaction, Material, User, utcnow
from app.security import hash_password

DEMO_USERS: tuple[tuple[str, str, str], ...] = (
    ("demo-rep@example.com", "rep", "demo_rep_password"),
    ("demo-manager@example.com", "manager", "demo_manager_password"),
    ("demo-admin@example.com", "admin", "demo_admin_password"),
)
DEMO_ADMIN_EMAIL = "demo-admin@example.com"

# Every app table, children first. TRUNCATE ... CASCADE also covers any table added later
# that references these.
APP_TABLES = ("chat_messages", "follow_ups", "interactions", "hcps", "materials", "products", "users")

DEMO_HCPS: tuple[dict[str, str], ...] = (
    dict(name="Dr. Anjali Rao", specialty="Cardiologist", institution="Manipal Hospital", state="Karnataka", tier="A"),
    dict(name="Dr. Vivek Sharma", specialty="Endocrinologist", institution="Apollo Clinic", state="California", tier="B"),
    dict(name="Dr. Emily Chen", specialty="Oncologist", institution="Stanford Health", state="Vermont", tier="A"),
    dict(name="Dr. Marcus Webb", specialty="Cardiologist", institution="Mayo Clinic", state="Minnesota", tier="B"),
    dict(name="Dr. Priya Nair", specialty="General Physician", institution="City Care Clinic", state="Karnataka", tier="C"),
)

# A rep-added material that is still pending approval, so the approval flow has something to show.
PENDING_MATERIAL = "CardioMax lunch-and-learn slides"


def _sample_interactions(today: date) -> list[dict[str, Any]]:
    """Sample interactions for the demo rep (HCP referenced by index into DEMO_HCPS)."""
    return [
        dict(
            hcp=0, days_ago=2, interaction_type="Visit", channel="form",
            products_discussed=["CardioMax"], materials_shared=["CardioMax Phase III study reprint"],
            samples_dropped=[{"product": "CardioMax", "qty": 2}], key_topics=["efficacy", "dosing"],
            hcp_sentiment="Positive",
            raw_notes="Discussed CardioMax Phase III results; she wants to start two stable patients.",
            ai_summary="Positive visit: reviewed CardioMax Phase III data and dosing; 2 samples left.",
            next_best_action="Send the CardioMax dosing guide and check in on the first two patients.",
            outcomes="Agreed to trial CardioMax with two patients.",
            follow_up=(5, "Check how the first two CardioMax patients are doing"),
        ),
        dict(
            hcp=1, days_ago=6, interaction_type="Call", channel="chat",
            products_discussed=["GlucoBalance"], materials_shared=["GlucoBalance product monograph"],
            key_topics=["formulary", "pricing"], hcp_sentiment="Neutral", sentiment_score=0.1,
            raw_notes="Call with Dr. Sharma about GlucoBalance formulary status; concerned about cost.",
            ai_summary="Neutral call: GlucoBalance formulary coverage and patient cost concerns.",
            next_best_action="Share the formulary coverage sheet and patient assistance program details.",
            follow_up=(3, "Send formulary coverage sheet for GlucoBalance"),
        ),
        dict(
            hcp=2, days_ago=9, interaction_type="Visit", channel="form",
            products_discussed=["OncoBoost"], samples_dropped=[{"product": "OncoBoost", "qty": 1}],
            materials_shared=["OncoBoost safety profile summary"], key_topics=["safety"], hcp_sentiment="Neutral",
            raw_notes="Reviewed the OncoBoost safety profile. Left one sample.",
            # Deliberate: Vermont allows no samples, so this row shows a sample-compliance flag.
            ai_summary="Reviewed OncoBoost safety data; one sample dropped despite Vermont's no-samples rule.",
            next_best_action="Follow up with the medical information team on her safety questions.",
        ),
        dict(
            hcp=3, days_ago=1, interaction_type="Visit", channel="form",
            products_discussed=["CardioMax"], materials_shared=[PENDING_MATERIAL], key_topics=["education"],
            hcp_sentiment="Positive",
            raw_notes="Proposed a lunch-and-learn for the cardiology team using new slides.",
            ai_summary="Planned a CardioMax lunch-and-learn; slides await compliance approval.",
            next_best_action="Get the lunch-and-learn slides approved before booking the session.",
        ),
    ]


def _passwords() -> dict[str, str]:
    missing = [attr.upper() for _e, _r, attr in DEMO_USERS if not getattr(settings, attr)]
    if missing:
        raise SystemExit(f"demo_seed: set {', '.join(missing)} (at least 12 characters each)")
    return {attr: getattr(settings, attr) for _e, _r, attr in DEMO_USERS}


async def _reset(db: AsyncSession, *, force: bool) -> None:
    users = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    has_demo_admin = (
        await db.execute(select(User.id).where(func.lower(User.email) == DEMO_ADMIN_EMAIL))
    ).first() is not None
    if users and not has_demo_admin and not force:
        raise SystemExit(
            f"demo_seed: refusing to reset: this database has {users} user(s) but no {DEMO_ADMIN_EMAIL}, "
            "so it does not look like a demo database (pass --force to wipe it anyway)"
        )
    await db.execute(text(f"TRUNCATE {', '.join(APP_TABLES)} CASCADE"))
    await db.commit()
    print("demo_seed: all app data wiped")


async def _upsert_users(db: AsyncSession, passwords: dict[str, str]) -> dict[str, str]:
    """Create the demo users, or reset their password/role/active flag. Returns email -> id."""
    ids: dict[str, str] = {}
    for email, role, attr in DEMO_USERS:
        user = (await db.execute(select(User).where(func.lower(User.email) == email))).scalar_one_or_none()
        if user is None:
            user = User(email=email, role=role, password_hash=hash_password(passwords[attr]))
            db.add(user)
        else:
            user.role, user.is_active, user.password_hash = role, True, hash_password(passwords[attr])
        await db.flush()
        ids[email] = user.id
    await db.commit()
    return ids


async def _add_sample_data(db: AsyncSession, rep_id: str) -> int:
    hcps = [HCP(**data) for data in DEMO_HCPS]
    db.add_all(hcps)
    db.add(Material(name=PENDING_MATERIAL, approved=False))
    await db.flush()
    today = date.today()
    for spec in _sample_interactions(today):
        spec = dict(spec)
        hcp = hcps[spec.pop("hcp")]
        follow_up = spec.pop("follow_up", None)
        interaction = Interaction(
            hcp_id=hcp.id,
            owner_id=rep_id,
            interaction_date=today - timedelta(days=spec.pop("days_ago")),
            attendees=[],
            edit_history=[],
            **spec,
        )
        interaction.samples_dropped = interaction.samples_dropped or []
        await apply_compliance(db, interaction, hcp)
        db.add(interaction)
        await db.flush()
        if follow_up:
            days, reason = follow_up
            db.add(FollowUp(hcp_id=hcp.id, owner_id=rep_id, interaction_id=interaction.id,
                            due_date=today + timedelta(days=days), reason=reason, created_at=utcnow()))
    await db.commit()
    return len(hcps)


async def seed_demo(*, reset: bool = False, force: bool = False) -> None:
    passwords = _passwords()  # fail before touching the database
    await init_db()
    async with AsyncSessionLocal() as db:
        if reset:
            await _reset(db, force=force)
        await seed_materials(db)
        await seed_products(db)
        ids = await _upsert_users(db, passwords)
        await ensure_seed_admin(db)  # ADMIN_EMAIL admin too, if configured
        if (await db.execute(select(func.count()).select_from(Interaction))).scalar_one() == 0:
            n = await _add_sample_data(db, ids["demo-rep@example.com"])
            print(f"demo_seed: added {n} HCPs with sample interactions for demo-rep")
        else:
            print("demo_seed: interactions already exist; sample data not added")
    print(f"demo_seed: demo users ready: {', '.join(e for e, _r, _a in DEMO_USERS)}")


def main(argv: list[str]) -> int:
    args = set(argv[1:])
    if not args <= {"--reset", "--force"} or ("--force" in args and "--reset" not in args):
        print(__doc__)
        return 2
    asyncio.run(seed_demo(reset="--reset" in args, force="--force" in args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
