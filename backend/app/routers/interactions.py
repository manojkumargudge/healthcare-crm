import json
from datetime import date as date_cls
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Interaction
from app.schemas import InteractionCreate, InteractionUpdate, InteractionOut
from app.agent.tools import apply_compliance, build_tools
from app.models import HCP

router = APIRouter(prefix="/api/interactions", tags=["Interactions"])


@router.get("", response_model=list[InteractionOut])
async def list_interactions(hcp_id: str | None = None, db: AsyncSession = Depends(get_db)):
    stmt = select(Interaction).order_by(Interaction.interaction_date.desc())
    if hcp_id:
        stmt = stmt.where(Interaction.hcp_id == hcp_id)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=InteractionOut, status_code=201)
async def create_interaction(payload: InteractionCreate, db: AsyncSession = Depends(get_db)):
    """Structured-form path. Doesn't require the LLM to extract fields since
    the rep filled them in directly, but still runs a compliance check on
    any samples dropped."""
    hcp = await db.get(HCP, payload.hcp_id)
    if not hcp:
        raise HTTPException(404, "HCP not found")

    data = payload.model_dump()
    if not data.get("interaction_date"):
        data["interaction_date"] = date_cls.today()
    interaction = Interaction(**data)

    # Compliance is evaluated before anything is written, so the row and its
    # compliance result land in one commit; if the check raises, nothing is saved.
    apply_compliance(interaction, hcp)

    db.add(interaction)
    await db.commit()
    await db.refresh(interaction)

    return interaction


@router.patch("/{interaction_id}", response_model=InteractionOut)
async def update_interaction(interaction_id: str, payload: InteractionUpdate, db: AsyncSession = Depends(get_db)):
    tools = build_tools(db)
    edit_tool = next(t for t in tools if t.name == "edit_interaction")
    updates = {k: v for k, v in payload.model_dump(exclude={"edited_by", "edit_reason"}).items() if v is not None}
    raw = await edit_tool.coroutine(  # type: ignore[misc]  # StructuredTool.coroutine is Optional; set for every tool in build_tools
        interaction_id=interaction_id,
        updates=json.dumps(updates),
        edit_reason=payload.edit_reason,
    )
    result = json.loads(raw)
    if "error" in result:
        raise HTTPException(404, result["error"])

    interaction = await db.get(Interaction, interaction_id)
    return interaction


@router.get("/{interaction_id}", response_model=InteractionOut)
async def get_interaction(interaction_id: str, db: AsyncSession = Depends(get_db)):
    interaction = await db.get(Interaction, interaction_id)
    if not interaction:
        raise HTTPException(404, "Interaction not found")
    return interaction
