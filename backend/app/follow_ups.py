"""AI-suggested follow-ups for an interaction.

Suggestions are stored as FollowUp rows with status "Suggested". The rep
accepts one by moving it to "Pending" (the same status the agent's
schedule_follow_up tool uses) or dismisses it ("Dismissed"). Regenerating
marks older unaccepted suggestions "Superseded" instead of deleting them, so
a suggestion still on someone's screen can always be accepted.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.llm import ainvoke_resilient
from app.agent.tools import _extract_json
from app.catalog import display_name
from app.models import HCP, FollowUp, Interaction, utcnow

logger = logging.getLogger("hcp_crm.follow_ups")

SUGGESTED = "Suggested"
PENDING = "Pending"
DISMISSED = "Dismissed"
SUPERSEDED = "Superseded"
MAX_SUGGESTIONS = 3


class _Suggestion(BaseModel):
    reason: str = Field(min_length=3, max_length=300)
    days_from_now: int = Field(ge=0, le=90)


async def list_follow_ups(db: AsyncSession, interaction_id: str) -> list[FollowUp]:
    rows = await db.execute(
        select(FollowUp).where(FollowUp.interaction_id == interaction_id).order_by(FollowUp.created_at, FollowUp.id)
    )
    return list(rows.scalars().all())


async def suggest_follow_ups(db: AsyncSession, interaction: Interaction, hcp: HCP) -> list[FollowUp]:
    """Ask the main model for up to 3 follow-ups and store them as "Suggested".

    Earlier, still-unaccepted suggestions become "Superseded" (never deleted);
    if the model returns nothing usable they are left as they are. Invalid items
    in the LLM output are skipped.
    """
    prompt = f"""You are assisting a pharmaceutical field rep after an interaction with an HCP.
HCP: {display_name(hcp.name)}, {hcp.specialty or "specialty unknown"}, tier {hcp.tier}.
Interaction ({interaction.interaction_type}, {interaction.interaction_date}):
- Products discussed: {", ".join(interaction.products_discussed or []) or "none"}
- Topics: {", ".join(interaction.key_topics or []) or "none"}
- Materials shared: {", ".join(interaction.materials_shared or []) or "none"}
- Attendees: {", ".join(interaction.attendees or []) or "none"}
- Sentiment: {interaction.hcp_sentiment or "unknown"}
- Topics discussed / notes: {interaction.raw_notes or interaction.ai_summary or "none"}
- Outcomes: {interaction.outcomes or "none"}
- Rep's planned follow-up actions: {interaction.follow_up_actions or "none"}

Suggest up to {MAX_SUGGESTIONS} concrete follow-up actions for the rep. Return ONLY JSON:
{{"follow_ups": [{{"reason": "short imperative action, e.g. 'Send OncoBoost Phase III PDF'", "days_from_now": integer 0-90}}]}}"""
    response, _model = await ainvoke_resilient(prompt, temperature=0.3)
    raw = _extract_json(response.content).get("follow_ups", [])

    suggestions: list[_Suggestion] = []
    for item in raw if isinstance(raw, list) else []:
        try:
            suggestions.append(_Suggestion.model_validate(item))
        except ValidationError:
            logger.warning("skipping invalid follow-up suggestion %r", item)
        if len(suggestions) >= MAX_SUGGESTIONS:
            break

    if not suggestions:
        logger.warning("no usable follow-up suggestions for interaction %s; keeping existing ones", interaction.id)
        return []
    await db.execute(
        update(FollowUp)
        .where(FollowUp.interaction_id == interaction.id, FollowUp.status == SUGGESTED)
        .values(status=SUPERSEDED)
    )
    today, now = date.today(), utcnow()
    rows = [
        FollowUp(
            hcp_id=interaction.hcp_id,
            interaction_id=interaction.id,
            due_date=today + timedelta(days=s.days_from_now),
            reason=s.reason.strip(),
            status=SUGGESTED,
            # Distinct, increasing timestamps keep the model's order when listed
            # (coarse clocks can otherwise give identical created_at values).
            created_at=now + timedelta(microseconds=i),
        )
        for i, s in enumerate(suggestions)
    ]
    db.add_all(rows)
    await db.commit()
    for r in rows:
        await db.refresh(r)
    return rows
