"""
LangGraph Agent Tools for the HCP CRM module.

Each tool is a discrete capability the agent can invoke. Tools are built
per-request via `build_tools(db)` so each has access to the current async
DB session (kept simple for this assignment; a production system would use
a proper unit-of-work / repository layer instead of a closure).

Tools implemented (6, the required minimum is 5, two of which -- Log
Interaction and Edit Interaction -- are mandatory per the task brief):

  1. log_interaction        (mandatory) - captures a new HCP interaction,
                              using the LLM to turn unstructured chat/notes
                              into structured CRM fields.
  2. edit_interaction        (mandatory) - modifies a previously logged
                              interaction and keeps an audit trail.
  3. get_hcp_context         - fetches an HCP's profile + recent interaction
                              history, used by the agent for grounding.
  4. check_sample_compliance - validates sample drops against a simple
                              state-level compliance ruleset (e.g. US
                              Sunshine Act / state sample limits).
  5. schedule_follow_up      - creates a follow-up task/reminder tied to
                              an HCP / interaction.
  6. suggest_next_best_action - LLM-generated recommendation for what the
                              rep should do next with this HCP.
"""
import json
from datetime import date, datetime, timedelta
from typing import Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HCP, Interaction, FollowUp
from app.agent.llm import ainvoke_resilient

# ---------------------------------------------------------------------------
# Simplified compliance ruleset (state -> max samples per product per visit).
# In a real system this would be pulled from a regulatory/compliance service.
# ---------------------------------------------------------------------------
STATE_SAMPLE_LIMITS = {
    "default": 4,
    "california": 2,
    "vermont": 0,  # Vermont restricts pharma sample/gift programs heavily
    "minnesota": 2,
}


def _extract_json(text: str) -> dict:
    """Best-effort extraction of a JSON object from an LLM response,
    stripping markdown code fences if present."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.lower().startswith("json"):
            cleaned = cleaned[4:]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(cleaned[start:end + 1])
            except json.JSONDecodeError:
                pass
    return {}


def build_tools(db: AsyncSession) -> list[StructuredTool]:

    # ---- 1. LOG INTERACTION -------------------------------------------------
    class LogInteractionArgs(BaseModel):
        hcp_id: str = Field(description="ID of the HCP this interaction is with")
        raw_text: str = Field(description="Free-text notes or chat transcript describing what happened in the interaction")
        interaction_type: str = Field(default="Visit", description="One of Visit, Call, Email, Sample Drop, Conference")

    async def log_interaction(hcp_id: str, raw_text: str, interaction_type: str = "Visit") -> str:
        hcp = await db.get(HCP, hcp_id)
        if not hcp:
            return json.dumps({"error": f"No HCP found with id {hcp_id}"})

        extraction_prompt = f"""You are a pharmaceutical CRM data-extraction assistant.
Read the field rep's notes below about a visit with Dr. {hcp.name} ({hcp.specialty or "unknown specialty"}).
Extract structured data and return ONLY valid JSON, no prose, no markdown fences, matching this schema:
{{
  "summary": "2-3 sentence professional summary of the interaction",
  "products_discussed": ["list", "of", "product names mentioned"],
  "samples_dropped": [{{"product": "name", "qty": integer}}],
  "materials_shared": ["list of any leave-behinds, brochures, studies mentioned"],
  "key_topics": ["short topic tags, e.g. 'efficacy data', 'side effects', 'pricing'"],
  "hcp_sentiment": "Positive | Neutral | Negative",
  "sentiment_score": float between -1.0 and 1.0
}}

Notes:
\"\"\"{raw_text}\"\"\""""
        response, _model_used = await ainvoke_resilient(extraction_prompt, temperature=0.1)
        data = _extract_json(response.content)

        interaction = Interaction(
            hcp_id=hcp_id,
            interaction_type=interaction_type,
            channel="chat",
            interaction_date=date.today(),
            products_discussed=data.get("products_discussed", []),
            samples_dropped=data.get("samples_dropped", []),
            materials_shared=data.get("materials_shared", []),
            key_topics=data.get("key_topics", []),
            hcp_sentiment=data.get("hcp_sentiment"),
            sentiment_score=data.get("sentiment_score"),
            raw_notes=raw_text,
            ai_summary=data.get("summary"),
        )
        db.add(interaction)
        await db.commit()
        await db.refresh(interaction)

        return json.dumps({
            "status": "logged",
            "interaction_id": interaction.id,
            "summary": interaction.ai_summary,
            "products_discussed": interaction.products_discussed,
            "samples_dropped": interaction.samples_dropped,
            "hcp_sentiment": interaction.hcp_sentiment,
        })

    # ---- 2. EDIT INTERACTION -------------------------------------------------
    class EditInteractionArgs(BaseModel):
        interaction_id: str = Field(description="ID of the interaction to edit")
        updates: str = Field(description="JSON string of fields to update, e.g. '{\"hcp_sentiment\": \"Positive\"}'")
        edit_reason: Optional[str] = Field(default=None, description="Why this edit is being made")

    async def edit_interaction(interaction_id: str, updates: str, edit_reason: Optional[str] = None) -> str:
        interaction = await db.get(Interaction, interaction_id)
        if not interaction:
            return json.dumps({"error": f"No interaction found with id {interaction_id}"})

        try:
            update_dict = json.loads(updates) if isinstance(updates, str) else updates
        except json.JSONDecodeError:
            return json.dumps({"error": "updates must be a valid JSON object string"})

        allowed_fields = {
            "interaction_type", "products_discussed", "samples_dropped",
            "materials_shared", "key_topics", "raw_notes", "hcp_sentiment",
        }
        before = {}
        changed = {}
        for field, value in update_dict.items():
            if field in allowed_fields:
                before[field] = getattr(interaction, field)
                setattr(interaction, field, value)
                changed[field] = value

        history = list(interaction.edit_history or [])
        history.append({
            "edited_at": datetime.utcnow().isoformat(),
            "reason": edit_reason,
            "before": before,
            "after": changed,
        })
        interaction.edit_history = history
        interaction.updated_at = datetime.utcnow()

        await db.commit()
        await db.refresh(interaction)

        return json.dumps({
            "status": "updated",
            "interaction_id": interaction.id,
            "changed_fields": list(changed.keys()),
        })

    # ---- 3. GET HCP CONTEXT --------------------------------------------------
    class GetHCPContextArgs(BaseModel):
        hcp_id: str = Field(description="ID of the HCP to look up")

    async def get_hcp_context(hcp_id: str) -> str:
        hcp = await db.get(HCP, hcp_id)
        if not hcp:
            return json.dumps({"error": f"No HCP found with id {hcp_id}"})

        result = await db.execute(
            select(Interaction)
            .where(Interaction.hcp_id == hcp_id)
            .order_by(Interaction.interaction_date.desc())
            .limit(5)
        )
        recent = result.scalars().all()

        return json.dumps({
            "hcp": {
                "id": hcp.id, "name": hcp.name, "specialty": hcp.specialty,
                "institution": hcp.institution, "state": hcp.state, "tier": hcp.tier,
            },
            "recent_interactions": [
                {
                    "date": str(i.interaction_date),
                    "type": i.interaction_type,
                    "summary": i.ai_summary,
                    "products_discussed": i.products_discussed,
                    "sentiment": i.hcp_sentiment,
                } for i in recent
            ],
        })

    # ---- 4. CHECK SAMPLE COMPLIANCE ------------------------------------------
    class ComplianceArgs(BaseModel):
        hcp_id: str = Field(description="ID of the HCP the samples were dropped for")
        samples_dropped: str = Field(description='JSON string list, e.g. \'[{"product": "DrugX", "qty": 3}]\'')

    async def check_sample_compliance(hcp_id: str, samples_dropped: str) -> str:
        hcp = await db.get(HCP, hcp_id)
        if not hcp:
            return json.dumps({"error": f"No HCP found with id {hcp_id}"})
        try:
            samples = json.loads(samples_dropped) if isinstance(samples_dropped, str) else samples_dropped
        except json.JSONDecodeError:
            return json.dumps({"error": "samples_dropped must be a valid JSON list string"})

        state_key = (hcp.state or "default").strip().lower()
        limit = STATE_SAMPLE_LIMITS.get(state_key, STATE_SAMPLE_LIMITS["default"])

        violations = []
        for s in samples:
            qty = s.get("qty", 0)
            if qty > limit:
                violations.append(
                    f"{s.get('product', 'Unknown product')}: {qty} units exceeds the "
                    f"{limit}-unit limit for {hcp.state or 'this state'}"
                )

        flagged = len(violations) > 0
        notes = "; ".join(violations) if violations else "No compliance issues detected."

        return json.dumps({
            "compliance_flag": flagged,
            "compliance_notes": notes,
            "state_limit_applied": limit,
        })

    # ---- 5. SCHEDULE FOLLOW-UP -----------------------------------------------
    class ScheduleFollowUpArgs(BaseModel):
        hcp_id: str = Field(description="ID of the HCP to follow up with")
        reason: str = Field(description="Why a follow-up is needed")
        days_from_now: int = Field(default=14, description="How many days from today the follow-up should be due")
        interaction_id: Optional[str] = Field(default=None, description="Related interaction ID, if any")

    async def schedule_follow_up(hcp_id: str, reason: str, days_from_now: int = 14,
                                  interaction_id: Optional[str] = None) -> str:
        hcp = await db.get(HCP, hcp_id)
        if not hcp:
            return json.dumps({"error": f"No HCP found with id {hcp_id}"})

        follow_up = FollowUp(
            hcp_id=hcp_id,
            interaction_id=interaction_id,
            due_date=date.today() + timedelta(days=days_from_now),
            reason=reason,
        )
        db.add(follow_up)
        await db.commit()
        await db.refresh(follow_up)

        return json.dumps({
            "status": "scheduled",
            "follow_up_id": follow_up.id,
            "due_date": str(follow_up.due_date),
            "reason": follow_up.reason,
        })

    # ---- 6. SUGGEST NEXT BEST ACTION -----------------------------------------
    class NextActionArgs(BaseModel):
        hcp_id: str = Field(description="ID of the HCP to generate a recommendation for")

    async def suggest_next_best_action(hcp_id: str) -> str:
        hcp = await db.get(HCP, hcp_id)
        if not hcp:
            return json.dumps({"error": f"No HCP found with id {hcp_id}"})

        result = await db.execute(
            select(Interaction)
            .where(Interaction.hcp_id == hcp_id)
            .order_by(Interaction.interaction_date.desc())
            .limit(3)
        )
        recent = result.scalars().all()
        history_text = "\n".join(
            f"- {i.interaction_date} ({i.interaction_type}): {i.ai_summary or i.raw_notes}"
            for i in recent
        ) or "No prior interactions on file."

        prompt = f"""You are a pharmaceutical sales strategy assistant.
HCP: Dr. {hcp.name}, {hcp.specialty or "specialty unknown"}, tier {hcp.tier}.
Recent interaction history:
{history_text}

In 2-3 sentences, recommend the single next best action the field rep should take
with this HCP (e.g. what to discuss, what materials to bring, timing). Be concrete
and specific to what's in the history. Return plain text, no JSON."""
        response, _model_used = await ainvoke_resilient(prompt, temperature=0.4, heavy=True)
        return json.dumps({"hcp_id": hcp_id, "next_best_action": response.content.strip()})

    return [
        StructuredTool.from_function(
            coroutine=log_interaction,
            name="log_interaction",
            description="Log a new HCP interaction from free-text notes or a chat transcript. Uses the LLM to extract structured fields (products discussed, samples dropped, sentiment, summary) and saves them to the CRM.",
            args_schema=LogInteractionArgs,
        ),
        StructuredTool.from_function(
            coroutine=edit_interaction,
            name="edit_interaction",
            description="Edit fields of a previously logged interaction. Keeps a before/after audit trail.",
            args_schema=EditInteractionArgs,
        ),
        StructuredTool.from_function(
            coroutine=get_hcp_context,
            name="get_hcp_context",
            description="Fetch an HCP's profile and their 5 most recent interactions, for grounding/context before logging or recommending.",
            args_schema=GetHCPContextArgs,
        ),
        StructuredTool.from_function(
            coroutine=check_sample_compliance,
            name="check_sample_compliance",
            description="Check dropped drug samples against state-level compliance limits (e.g. Sunshine Act / state sample caps) and flag violations.",
            args_schema=ComplianceArgs,
        ),
        StructuredTool.from_function(
            coroutine=schedule_follow_up,
            name="schedule_follow_up",
            description="Create a follow-up reminder/task for a given HCP, due a number of days from today.",
            args_schema=ScheduleFollowUpArgs,
        ),
        StructuredTool.from_function(
            coroutine=suggest_next_best_action,
            name="suggest_next_best_action",
            description="Generate an LLM recommendation for the single next best action the rep should take with this HCP, based on interaction history.",
            args_schema=NextActionArgs,
        ),
    ]
