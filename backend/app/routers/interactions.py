import json
from collections.abc import Sequence
from datetime import date as date_cls

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools import apply_compliance, build_tools
from app.authz import Authz, get_authz
from app.catalog import (
    UnknownProductsError,
    catalog_samples,
    require_catalog_products,
    resolve_attendees,
    resolve_materials,
)
from app.database import get_db
from app.follow_ups import list_follow_ups, suggest_follow_ups
from app.models import HCP, Interaction, User, utcnow
from app.ratelimit import llm_rate_limit
from app.schemas import FollowUpOut, InteractionCreate, InteractionOut, InteractionUpdate, RepCount

router = APIRouter(prefix="/api/interactions", tags=["Interactions"])


async def interactions_out(db: AsyncSession, interactions: Sequence[Interaction]) -> list[InteractionOut]:
    """Serialize interactions with each owner's email (one query for all of them)."""
    owner_ids = {i.owner_id for i in interactions}
    emails: dict[str, str] = {}
    if owner_ids:
        rows = await db.execute(select(User.id, User.email).where(User.id.in_(owner_ids)))
        emails = {uid: email for uid, email in rows.all()}
    return [
        InteractionOut.model_validate(i).model_copy(update={"owner_email": emails.get(i.owner_id)})
        for i in interactions
    ]


PAGE_SIZE_DEFAULT = 25
PAGE_SIZE_MAX = 100


@router.get("", response_model=list[InteractionOut])
async def list_interactions(
    response: Response,
    hcp_id: str | None = None,
    owner_id: str | None = Query(None, description="Only this rep's interactions (managers/admins)"),
    limit: int = Query(PAGE_SIZE_DEFAULT, ge=1, le=PAGE_SIZE_MAX),
    offset: int = Query(0, ge=0),
    authz: Authz = Depends(get_authz),
    db: AsyncSession = Depends(get_db),
):
    """Newest first, one page at a time; the total matching count is in X-Total-Count.
    Reps see their own interactions; managers and admins see everyone's."""
    stmt = authz.read_filter(select(Interaction), Interaction)
    if hcp_id:
        stmt = stmt.where(Interaction.hcp_id == hcp_id)
    if owner_id:
        stmt = stmt.where(Interaction.owner_id == owner_id)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    response.headers["X-Total-Count"] = str(total)
    # created_at and id break ties so pages never overlap or skip rows on the same date.
    page = stmt.order_by(
        Interaction.interaction_date.desc(), Interaction.created_at.desc(), Interaction.id.desc()
    ).limit(limit).offset(offset)
    return await interactions_out(db, (await db.execute(page)).scalars().all())


@router.get("/reps", response_model=list[RepCount])
async def list_interaction_reps(
    hcp_id: str | None = None, authz: Authz = Depends(get_authz), db: AsyncSession = Depends(get_db)
):
    """Reps with visible interactions (optionally for one HCP) and how many each, for the rep filter."""
    stmt = authz.read_filter(
        select(User.id, User.email, func.count(Interaction.id)).join(User, User.id == Interaction.owner_id),
        Interaction,
    )
    if hcp_id:
        stmt = stmt.where(Interaction.hcp_id == hcp_id)
    rows = await db.execute(stmt.group_by(User.id, User.email).order_by(func.lower(User.email)))
    return [RepCount(owner_id=uid, email=email, count=n) for uid, email, n in rows.all()]


@router.post("", response_model=InteractionOut, status_code=201)
async def create_interaction(
    payload: InteractionCreate, authz: Authz = Depends(get_authz), db: AsyncSession = Depends(get_db)
):
    """Structured-form path, owned by the calling rep. No LLM involved, but the
    compliance check still runs before the single commit."""
    hcp = await db.get(HCP, payload.hcp_id)
    if not hcp:
        raise HTTPException(404, "HCP not found")

    data = payload.model_dump()
    if not data.get("interaction_date"):
        data["interaction_date"] = date_cls.today()
    data["attendees"] = await resolve_attendees(db, data["attendees"])
    data["materials_shared"] = await resolve_materials(db, data["materials_shared"])
    # The product catalog is admin-only: the form must pick from it.
    try:
        data["products_discussed"] = await require_catalog_products(db, data["products_discussed"])
        data["samples_dropped"], _, _ = await catalog_samples(db, data["samples_dropped"], strict=True)
    except UnknownProductsError as exc:
        raise HTTPException(422, str(exc)) from exc
    if data["voice_note_consent"] and data["voice_note_consent_at"] is None:
        data["voice_note_consent_at"] = utcnow()
    # Form-set sentiment label is authoritative; there is no LLM score for it.
    interaction = Interaction(**data, owner_id=authz.user.id, sentiment_score=None)

    await apply_compliance(db, interaction, hcp)
    db.add(interaction)
    await db.commit()
    await db.refresh(interaction)
    return (await interactions_out(db, [interaction]))[0]


async def _visible(db: AsyncSession, authz: Authz, interaction_id: str) -> Interaction:
    interaction = await db.get(Interaction, interaction_id)
    authz.ensure_read(interaction)
    assert interaction is not None
    return interaction


@router.get("/{interaction_id}", response_model=InteractionOut)
async def get_interaction(interaction_id: str, authz: Authz = Depends(get_authz), db: AsyncSession = Depends(get_db)):
    return (await interactions_out(db, [await _visible(db, authz, interaction_id)]))[0]


@router.patch("/{interaction_id}", response_model=InteractionOut)
async def update_interaction(
    interaction_id: str, payload: InteractionUpdate, authz: Authz = Depends(get_authz), db: AsyncSession = Depends(get_db)
):
    interaction = await db.get(Interaction, interaction_id)
    authz.ensure_write(interaction)
    updates = {k: v for k, v in payload.model_dump(exclude={"edited_by", "edit_reason"}).items() if v is not None}
    # Form edits pick products from the admin-only catalog (the tool's fuzzy matching is for chat).
    try:
        if "products_discussed" in updates:
            updates["products_discussed"] = await require_catalog_products(db, updates["products_discussed"])
        if "samples_dropped" in updates:
            updates["samples_dropped"], _, _ = await catalog_samples(db, updates["samples_dropped"], strict=True)
    except UnknownProductsError as exc:
        raise HTTPException(422, str(exc)) from exc
    edit_tool = next(t for t in build_tools(db, authz) if t.name == "edit_interaction")
    raw = await edit_tool.coroutine(  # type: ignore[misc]  # StructuredTool.coroutine is Optional; set for every tool in build_tools
        interaction_id=interaction_id,
        updates=json.dumps(updates),
        edit_reason=payload.edit_reason,
    )
    result = json.loads(raw)
    if "error" in result:
        raise HTTPException(404, result["error"])
    updated = await db.get(Interaction, interaction_id)
    assert updated is not None  # the tool just edited it
    return (await interactions_out(db, [updated]))[0]


@router.get("/{interaction_id}/follow-ups", response_model=list[FollowUpOut])
async def get_follow_ups(interaction_id: str, authz: Authz = Depends(get_authz), db: AsyncSession = Depends(get_db)):
    """All follow-ups linked to the interaction: AI suggestions and accepted/scheduled ones."""
    await _visible(db, authz, interaction_id)
    return await list_follow_ups(db, interaction_id)


@router.post("/{interaction_id}/follow-ups/suggestions", response_model=list[FollowUpOut])
async def create_follow_up_suggestions(
    interaction_id: str, authz: Authz = Depends(llm_rate_limit), db: AsyncSession = Depends(get_db)
):
    """Generate up to 3 AI follow-up suggestions (status "Suggested") for the caller's interaction."""
    interaction = await db.get(Interaction, interaction_id)
    authz.ensure_write(interaction)
    assert interaction is not None
    hcp = await db.get(HCP, interaction.hcp_id)
    assert hcp is not None  # FK
    try:
        return await suggest_follow_ups(db, interaction, hcp)
    except Exception as exc:  # LLM/network failure: report, don't 500 with a traceback
        await db.rollback()
        raise HTTPException(502, f"Could not generate follow-up suggestions: {type(exc).__name__}") from exc
