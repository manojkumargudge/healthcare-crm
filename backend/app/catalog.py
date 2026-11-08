"""Shared rules for sentiment labels, attendees and the materials catalog.

Used by the form endpoints and the agent tools so both channels store the
same canonical values.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Literal, Optional

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HCP, Material, gen_uuid, utcnow

logger = logging.getLogger("hcp_crm.catalog")

Sentiment = Literal["Positive", "Neutral", "Negative"]
SENTIMENT_LABELS: tuple[str, ...] = ("Positive", "Neutral", "Negative")

MAX_NAME_LEN = 200
MAX_ITEMS = 50

# Starter catalog for the "Materials shared" picker. Reps can add more from
# the form; free-text materials from the agent are added automatically.
SEED_MATERIALS: tuple[str, ...] = (
    "CardioMax Phase III study reprint",
    "CardioMax dosing guide",
    "CardioMax patient brochure",
    "OncoBoost Phase III efficacy PDF",
    "OncoBoost safety profile summary",
    "GlucoBalance product monograph",
    "GlucoBalance patient starter kit",
    "Formulary coverage sheet",
    "Patient assistance program flyer",
    "Clinical trial enrollment info",
    "Medical information request form",
    "Advisory board invitation",
)


def normalize_sentiment(value: Any) -> Optional[str]:
    """Case-insensitive match to Positive/Neutral/Negative; None/'' -> None."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str):
        for label in SENTIMENT_LABELS:
            if value.strip().lower() == label.lower():
                return label
    raise ValueError(f"hcp_sentiment must be one of {', '.join(SENTIMENT_LABELS)}")


def clean_names(values: Optional[Iterable[Any]]) -> list[str]:
    """Strip, drop empties/non-strings, truncate, de-duplicate case-insensitively (first wins)."""
    out: list[str] = []
    seen: set[str] = set()
    for v in values or []:
        if not isinstance(v, str):
            logger.warning("skipping non-string name %r", v)
            continue
        name = " ".join(v.split())[:MAX_NAME_LEN]
        if name and name.lower() not in seen:
            seen.add(name.lower())
            out.append(name)
        if len(out) >= MAX_ITEMS:
            break
    return out


async def resolve_attendees(db: AsyncSession, names: Optional[Iterable[Any]]) -> list[str]:
    """Clean attendee names; a case-insensitive match to an HCP uses the HCP's exact name."""
    cleaned = clean_names(names)
    if not cleaned:
        return []
    rows = await db.execute(select(HCP.name).where(func.lower(HCP.name).in_([n.lower() for n in cleaned])))
    canonical = {name.lower(): name for (name,) in rows.all()}
    return clean_names(canonical.get(n.lower(), n) for n in cleaned)


def display_name(name: Optional[str]) -> str:
    """HCP name with exactly one "Dr." prefix (seeded names already include it)."""
    name = (name or "").strip()
    return name if re.match(r"(?i)^dr\.?\s", name) else f"Dr. {name}"


async def resolve_materials(db: AsyncSession, names: Optional[Iterable[Any]]) -> list[str]:
    """Map material names to catalog entries (case-insensitive), adding unknown ones to the catalog.

    New entries are unapproved ("Pending approval") until compliance approves them.
    Does not commit; the caller's transaction commits the catalog rows with the interaction.
    """
    cleaned = clean_names(names)
    if not cleaned:
        return []
    await db.execute(
        pg_insert(Material)
        .values([{"id": gen_uuid(), "name": n, "approved": False, "created_at": utcnow()} for n in cleaned])
        .on_conflict_do_nothing()
    )
    rows = await db.execute(select(Material.name).where(func.lower(Material.name).in_([n.lower() for n in cleaned])))
    canonical = {name.lower(): name for (name,) in rows.all()}
    return [canonical.get(n.lower(), n) for n in cleaned]


async def unapproved_materials(db: AsyncSession, names: Optional[Iterable[Any]]) -> list[str]:
    """The subset of `names` whose catalog entry is not approved (catalog spelling, input order)."""
    cleaned = clean_names(names)
    if not cleaned:
        return []
    rows = await db.execute(
        select(Material.name).where(
            func.lower(Material.name).in_([n.lower() for n in cleaned]), Material.approved.is_(False)
        )
    )
    pending = {name.lower(): name for (name,) in rows.all()}
    return [pending[n.lower()] for n in cleaned if n.lower() in pending]


async def seed_materials(db: AsyncSession) -> int:
    """Insert the starter catalog as approved (idempotent). A starter name a rep added
    earlier is marked approved. Returns rows inserted or updated."""
    stmt = pg_insert(Material).values(
        [{"id": gen_uuid(), "name": n, "approved": True, "created_at": utcnow()} for n in SEED_MATERIALS]
    )
    result = await db.execute(
        stmt.on_conflict_do_update(
            index_elements=[text("lower(name)")],
            set_={"approved": True},
            where=Material.approved.is_(False),
        )
    )
    await db.commit()
    return int(result.rowcount or 0)  # type: ignore[attr-defined]  # CursorResult has rowcount; Result stub lacks it
