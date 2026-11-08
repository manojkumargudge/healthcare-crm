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
from datetime import date, timedelta
from typing import Any, Optional

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import Authz
from app.models import HCP, Interaction, FollowUp, utcnow
from app.agent.llm import ainvoke_resilient
from app.samples import STATE_SAMPLE_LIMITS, coerce_samples, evaluate_sample_compliance
from app.catalog import (
    catalog_products,
    catalog_samples,
    product_choices,
    clean_names,
    display_name,
    normalize_sentiment,
    resolve_attendees,
    resolve_materials,
    match_products,
    unapproved_materials,
)

__all__ = ["STATE_SAMPLE_LIMITS", "build_tools", "apply_compliance"]


async def apply_compliance(db: AsyncSession, interaction: Interaction, hcp: HCP) -> None:
    """Set compliance_flag/notes from samples and materials (no commit).

    Flags sample quantities over the state limit and materials whose catalog
    entry is not yet approved. No samples and no pending materials -> not
    flagged and no notes, matching what create has always stored.
    """
    issues: list[str] = []
    checked = False
    if interaction.samples_dropped:
        checked = True
        result = evaluate_sample_compliance(hcp.state, interaction.samples_dropped)
        if result["compliance_flag"]:
            issues.append(result["compliance_notes"])
    pending = await unapproved_materials(db, interaction.materials_shared)
    if pending:
        issues.append(f"Materials pending approval: {', '.join(pending)}")
    interaction.compliance_flag = bool(issues)
    if issues:
        interaction.compliance_notes = "; ".join(issues)
    elif checked:
        interaction.compliance_notes = "No compliance issues detected."
    else:
        interaction.compliance_notes = None  # type: ignore[assignment]  # column is nullable=True but annotated Mapped[str]


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


class SampleArg(BaseModel):
    product: str = Field(description="Catalog product name")
    qty: int = Field(ge=0, description="Number of units")


def _matches(field: str, fuzzy: list[dict[str, str]]) -> list[dict[str, str]]:
    return [{"field": field, **m} for m in fuzzy]


def build_tools(db: AsyncSession, authz: Authz) -> list[StructuredTool]:
    """Tools bound to one DB session and one caller. Every tool applies the caller's
    ownership rules (app/authz.py), exactly like the HTTP routes."""
    user = authz.user

    # ---- 1. LOG INTERACTION -------------------------------------------------
    class LogInteractionArgs(BaseModel):
        hcp_id: str = Field(description="ID of the HCP this interaction is with")
        raw_text: str = Field(description="Free-text notes or chat transcript describing what happened in the interaction")
        interaction_type: str = Field(default="Visit", description="One of Meeting, Visit, Call, Email, Sample Drop, Conference")
        attendees: Optional[list[str]] = Field(
            default=None, description="Names of other people present (colleagues, other HCPs), if the rep mentioned any"
        )
        materials_shared: Optional[list[str]] = Field(
            default=None, description="Materials/leave-behinds the rep shared, if mentioned"
        )
        samples: Optional[list[SampleArg]] = Field(
            default=None,
            description="Samples left, with catalog product names. Use this after the rep has picked "
            "products for samples that needed a choice; it replaces the samples extracted from raw_text.",
        )

    async def log_interaction(
        hcp_id: str,
        raw_text: str,
        interaction_type: str = "Visit",
        attendees: Optional[list[str]] = None,
        materials_shared: Optional[list[str]] = None,
        samples: Optional[list[Any]] = None,
    ) -> str:
        hcp = await db.get(HCP, hcp_id)
        if not hcp:
            return json.dumps({"error": f"No HCP found with id {hcp_id}"})

        extraction_prompt = f"""You are a pharmaceutical CRM data-extraction assistant.
Read the field rep's notes below about a visit with {display_name(hcp.name)} ({hcp.specialty or "unknown specialty"}).
Extract structured data and return ONLY valid JSON, no prose, no markdown fences, matching this schema:
{{
  "summary": "2-3 sentence professional summary of the interaction",
  "products_discussed": ["list", "of", "product names mentioned"],
  "samples_dropped": [{{"product": "name", "qty": integer}}],
  "materials_shared": ["list of any leave-behinds, brochures, studies mentioned"],
  "key_topics": ["short topic tags, e.g. 'efficacy data', 'side effects', 'pricing'"],
  "attendees": ["names of other people present besides the rep and {display_name(hcp.name)}"],
  "outcomes": "key outcomes or agreements, one or two sentences, or null",
  "follow_up_actions": "next steps or tasks the rep mentioned, or null",
  "hcp_sentiment": "Positive | Neutral | Negative",
  "sentiment_score": float between -1.0 and 1.0
}}

Notes:
\"\"\"{raw_text}\"\"\""""
        response, _model_used = await ainvoke_resilient(extraction_prompt, temperature=0.1, fast=True)
        data = _extract_json(response.content)
        try:
            sentiment = normalize_sentiment(data.get("hcp_sentiment"))
        except ValueError:
            sentiment = None
        # Explicit tool arguments first, then anything the extraction found.
        attendee_names = await resolve_attendees(db, list(attendees or []) + list(data.get("attendees") or []))
        material_names = await resolve_materials(
            db, list(materials_shared or []) + list(data.get("materials_shared") or [])
        )
        # Samples must be catalog products. Anything that can't be matched is never
        # saved: nothing is logged yet, and the agent asks the rep to pick.
        raw_samples = (
            [s.model_dump() if isinstance(s, BaseModel) else s for s in samples]
            if samples is not None
            else data.get("samples_dropped", [])
        )
        sample_rows, unknown_samples, sample_fuzzy = await catalog_samples(
            db, coerce_samples(raw_samples, source="log_interaction"), strict=False
        )
        if unknown_samples:
            await db.rollback()
            catalog = await catalog_products(db)
            return json.dumps({
                "status": "needs_product_choice",
                "saved": False,
                "unknown_sample_products": unknown_samples,
                "choices": {n: product_choices(n, catalog) for n in unknown_samples},
                "message": "Nothing was saved. Ask the rep which catalog product each of these samples is "
                "(offer the choices), then call log_interaction again with the same notes and the full "
                "`samples` list using catalog names.",
            })
        # Products discussed: closest catalog product; unmatched names are kept for review.
        product_names, unmatched, product_fuzzy = await match_products(db, data.get("products_discussed") or [])

        interaction = Interaction(
            hcp_id=hcp_id,
            owner_id=user.id,
            interaction_type=interaction_type,
            channel="chat",
            interaction_date=date.today(),
            products_discussed=product_names,
            unmatched_products=unmatched,
            product_matches=_matches("products_discussed", product_fuzzy) + _matches("samples_dropped", sample_fuzzy),
            samples_dropped=sample_rows,
            materials_shared=material_names,
            key_topics=data.get("key_topics", []),
            attendees=attendee_names,
            hcp_sentiment=sentiment,
            sentiment_score=data.get("sentiment_score") if sentiment else None,
            raw_notes=raw_text,
            outcomes=data.get("outcomes") if isinstance(data.get("outcomes"), str) else None,
            follow_up_actions=data.get("follow_up_actions") if isinstance(data.get("follow_up_actions"), str) else None,
            ai_summary=data.get("summary"),
        )
        await apply_compliance(db, interaction, hcp)
        db.add(interaction)
        await db.commit()
        await db.refresh(interaction)

        return json.dumps({
            "status": "logged",
            "interaction_id": interaction.id,
            "summary": interaction.ai_summary,
            "products_discussed": interaction.products_discussed,
            "samples_dropped": interaction.samples_dropped,
            "materials_shared": interaction.materials_shared,
            "unmatched_products": interaction.unmatched_products,
            "product_matches": interaction.product_matches,
            "attendees": interaction.attendees,
            "compliance_flag": interaction.compliance_flag,
            "compliance_notes": interaction.compliance_notes,
            "hcp_sentiment": interaction.hcp_sentiment,
        })

    # ---- 2. EDIT INTERACTION -------------------------------------------------
    class EditInteractionArgs(BaseModel):
        interaction_id: str = Field(description="ID of the interaction to edit")
        updates: str = Field(
            description="JSON string of fields to update, e.g. '{\"hcp_sentiment\": \"Positive\"}'. Editable: "
            "interaction_type, products_discussed, samples_dropped, materials_shared, key_topics, raw_notes, "
            "hcp_sentiment (Positive/Neutral/Negative), attendees (list of names), outcomes, follow_up_actions"
        )
        edit_reason: Optional[str] = Field(default=None, description="Why this edit is being made")

    async def edit_interaction(interaction_id: str, updates: str, edit_reason: Optional[str] = None) -> str:
        interaction = await db.get(Interaction, interaction_id)
        if not authz.can_read(interaction):
            return json.dumps({"error": f"No interaction found with id {interaction_id}"})
        if not authz.can_write(interaction):
            return json.dumps({"error": "Not allowed to change this interaction (it belongs to another rep)."})
        assert interaction is not None  # can_read() above rejects None

        try:
            update_dict = json.loads(updates) if isinstance(updates, str) else updates
        except json.JSONDecodeError:
            return json.dumps({"error": "updates must be a valid JSON object string"})

        allowed_fields = {
            "interaction_type", "products_discussed", "samples_dropped",
            "materials_shared", "key_topics", "raw_notes", "hcp_sentiment", "attendees",
            "outcomes", "follow_up_actions", "unmatched_products", "product_matches",
        }
        update_dict.pop("unmatched_products", None)  # derived, not directly editable
        update_dict.pop("product_matches", None)
        matches = list(interaction.product_matches or [])
        if "samples_dropped" in update_dict:
            rows, unknown, fuzzy = await catalog_samples(
                db, coerce_samples(update_dict["samples_dropped"], source="edit_interaction"), strict=False
            )
            if unknown:
                await db.rollback()
                catalog = await catalog_products(db)
                return json.dumps({
                    "status": "needs_product_choice",
                    "saved": False,
                    "unknown_sample_products": unknown,
                    "choices": {n: product_choices(n, catalog) for n in unknown},
                    "message": "Nothing was changed. Ask the rep to pick catalog products for these samples.",
                })
            update_dict["samples_dropped"] = rows
            matches = [m for m in matches if m.get("field") != "samples_dropped"] + _matches("samples_dropped", fuzzy)
        if "products_discussed" in update_dict:
            update_dict["products_discussed"], miss, fuzzy = await match_products(db, update_dict["products_discussed"])
            matches = [m for m in matches if m.get("field") != "products_discussed"] + _matches("products_discussed", fuzzy)
            if miss:
                update_dict["unmatched_products"] = clean_names(list(interaction.unmatched_products or []) + miss)
        if matches != list(interaction.product_matches or []):
            update_dict["product_matches"] = matches
        if "attendees" in update_dict:
            update_dict["attendees"] = await resolve_attendees(db, update_dict["attendees"])
        if "materials_shared" in update_dict:
            update_dict["materials_shared"] = await resolve_materials(db, update_dict["materials_shared"])
        if "hcp_sentiment" in update_dict:
            try:
                update_dict["hcp_sentiment"] = normalize_sentiment(update_dict["hcp_sentiment"])
            except ValueError as exc:
                await db.rollback()
                return json.dumps({"error": str(exc)})

        before = {}
        changed = {}
        for field, value in update_dict.items():
            if field in allowed_fields:
                before[field] = getattr(interaction, field)
                setattr(interaction, field, value)
                changed[field] = value

        # sentiment_score was produced by the LLM for the *old* label; once the
        # label changes it no longer describes anything, so clear it (and keep
        # the old value in the audit entry) rather than leave a contradiction.
        if (
            "hcp_sentiment" in changed
            and before["hcp_sentiment"] != changed["hcp_sentiment"]
            and interaction.sentiment_score is not None
        ):
            before["sentiment_score"] = interaction.sentiment_score
            interaction.sentiment_score = None  # type: ignore[assignment]  # column is nullable=True but annotated Mapped[float]
            changed["sentiment_score"] = None

        if "samples_dropped" in changed or "materials_shared" in changed:
            hcp = await db.get(HCP, interaction.hcp_id)
            if hcp is None:  # FK makes this unreachable; fail without saving rather than guess
                await db.rollback()
                return json.dumps({"error": f"No HCP found with id {interaction.hcp_id}"})
            await apply_compliance(db, interaction, hcp)

        history = list(interaction.edit_history or [])
        history.append({
            "edited_at": utcnow().isoformat(),
            "reason": edit_reason,
            "before": before,
            "after": changed,
        })
        interaction.edit_history = history
        interaction.updated_at = utcnow()

        await db.commit()
        await db.refresh(interaction)

        result: dict = {
            "status": "updated",
            "interaction_id": interaction.id,
            "changed_fields": list(changed.keys()),
        }
        if "samples_dropped" in changed or "materials_shared" in changed:
            result["compliance_flag"] = interaction.compliance_flag
            result["compliance_notes"] = interaction.compliance_notes
        return json.dumps(result)

    # ---- 3. GET HCP CONTEXT --------------------------------------------------
    class GetHCPContextArgs(BaseModel):
        hcp_id: str = Field(description="ID of the HCP to look up")

    async def get_hcp_context(hcp_id: str) -> str:
        hcp = await db.get(HCP, hcp_id)
        if not hcp:
            return json.dumps({"error": f"No HCP found with id {hcp_id}"})

        result = await db.execute(
            authz.read_filter(select(Interaction), Interaction)
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
                    "attendees": i.attendees or [],
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

        if not isinstance(samples, list):
            return json.dumps({"error": "samples_dropped must be a valid JSON list string"})
        # Items are checked individually: a malformed qty is skipped with a
        # warning instead of crashing; a missing product still reports as
        # "Unknown product" so the violation isn't lost.
        return json.dumps(evaluate_sample_compliance(hcp.state, [s for s in samples if isinstance(s, dict)]))

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

        if interaction_id:
            linked = await db.get(Interaction, interaction_id)
            if not authz.can_read(linked):
                return json.dumps({"error": f"No interaction found with id {interaction_id}"})
            if not authz.can_write(linked):
                return json.dumps({"error": "Not allowed to add follow-ups to another rep's interaction."})
            assert linked is not None
            if linked.hcp_id != hcp_id:
                return json.dumps({"error": "That interaction is with a different HCP."})
        follow_up = FollowUp(
            hcp_id=hcp_id,
            owner_id=user.id,
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
            authz.read_filter(select(Interaction), Interaction)
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
HCP: {display_name(hcp.name)}, {hcp.specialty or "specialty unknown"}, tier {hcp.tier}.
Recent interaction history:
{history_text}

In 2-3 sentences, recommend the single next best action the field rep should take
with this HCP (e.g. what to discuss, what materials to bring, timing). Be concrete
and specific to what's in the history. Return plain text, no JSON."""
        response, _model_used = await ainvoke_resilient(prompt, temperature=0.4)
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
