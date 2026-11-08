"""Live tests against the real Groq API. Skipped unless RUN_LIVE_GROQ=1.

Run (never in CI):
    RUN_LIVE_GROQ=1 LIVE_GROQ_API_KEY=<key> TEST_DATABASE_URL=... pytest -m live -v

Models default to the app defaults; override with LIVE_GROQ_MODEL /
LIVE_GROQ_MODEL_FAST. LLM output is non-deterministic, so assertions check
which tools ran and what landed in the database, not exact wording.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest
from langchain_groq import ChatGroq
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import llm as llm_module
from app.config import Settings
from app.models import ChatMessage, FollowUp, Interaction

pytestmark = pytest.mark.live


@pytest.fixture(autouse=True)
def _real_groq(monkeypatch: pytest.MonkeyPatch) -> None:
    key = os.environ.get("LIVE_GROQ_API_KEY", "")
    if not key:
        pytest.fail("LIVE_GROQ_API_KEY is not set")
    monkeypatch.setattr(llm_module.settings, "groq_api_key", key)
    monkeypatch.setattr(
        llm_module.settings,
        "groq_model",
        os.environ.get("LIVE_GROQ_MODEL", Settings.model_fields["groq_model"].default),
    )
    monkeypatch.setattr(
        llm_module.settings,
        "groq_model_fast",
        os.environ.get("LIVE_GROQ_MODEL_FAST", Settings.model_fields["groq_model_fast"].default),
    )


class _Chat:
    """Drives /api/chat/turn and returns only the tools called in *this* turn."""

    def __init__(self, client: httpx.AsyncClient, hcp_id: str, session_id: str) -> None:
        self.client, self.hcp_id, self.session_id = client, hcp_id, session_id
        self._seen = 0

    async def turn(self, message: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        resp = await self.client.post(
            "/api/chat/turn",
            json={"session_id": self.session_id, "message": message, "hcp_id": self.hcp_id},
            timeout=120,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # The endpoint reports tool calls for the whole session; slice off earlier turns.
        new_calls = body["tool_calls"][self._seen :]
        self._seen = len(body["tool_calls"])
        return body, new_calls


def _names(calls: list[dict[str, Any]]) -> list[str]:
    return [c["tool"] for c in calls]


async def test_live_agent_runs_every_tool(client: httpx.AsyncClient, make_hcp, db: AsyncSession) -> None:
    hcp = await make_hcp(name="Dr. Live Test", specialty="Cardiologist", state="California", tier="A")
    chat = _Chat(client, hcp["id"], "live-session-1")

    # 1. log_interaction
    body, calls = await chat.turn(
        "Just met the doctor. We discussed CardioMax efficacy data, I left 2 samples of CardioMax, "
        "she was positive but asked about pricing. Please log this visit."
    )
    assert "log_interaction" in _names(calls), calls
    assert body["interaction"] is not None, body
    interaction_id = body["interaction"]["id"]
    row = await db.get(Interaction, interaction_id)
    assert row is not None and row.channel == "chat" and row.hcp_id == hcp["id"]
    assert row.raw_notes and row.ai_summary

    # 2. edit_interaction
    _body, calls = await chat.turn(
        f"Correction for interaction {interaction_id}: her sentiment was Neutral, not Positive. Please update it."
    )
    assert "edit_interaction" in _names(calls), calls
    await db.refresh(row)
    assert row.hcp_sentiment == "Neutral"
    assert len(row.edit_history) >= 1

    # 3. get_hcp_context
    _body, calls = await chat.turn("Pull up this HCP's profile and recent interaction history.")
    assert "get_hcp_context" in _names(calls), calls

    # 4. check_sample_compliance (California limit is 2)
    _body, calls = await chat.turn(
        "Run a sample compliance check for this HCP: I also dropped 3 units of CardioMax. Is that within limits?"
    )
    assert "check_sample_compliance" in _names(calls), calls

    # 5. schedule_follow_up
    _body, calls = await chat.turn(
        f"Schedule a follow-up with this HCP in 7 days to answer her pricing question, linked to interaction "
        f"{interaction_id}."
    )
    assert "schedule_follow_up" in _names(calls), calls
    follow_ups = (await db.execute(select(FollowUp).where(FollowUp.hcp_id == hcp["id"]))).scalars().all()
    assert len(follow_ups) >= 1
    assert all(f.reason for f in follow_ups)

    # 6. suggest_next_best_action
    body, calls = await chat.turn("What is the next best action I should take with this HCP?")
    assert "suggest_next_best_action" in _names(calls), calls
    assert body["reply"]

    msgs = (await db.execute(select(func.count()).select_from(ChatMessage))).scalar_one()
    assert msgs >= 12  # at least a user + assistant row per turn


@pytest.mark.parametrize("model", ["gemma2-9b-it", "llama-3.3-70b-versatile"])
async def test_live_brief_models_are_decommissioned(model: str) -> None:
    """Documents that the assignment brief's models no longer work on Groq."""
    llm = ChatGroq(api_key=os.environ["LIVE_GROQ_API_KEY"], model=model, temperature=0)
    with pytest.raises(Exception) as exc:
        await llm.ainvoke("ping")
    assert any(h in str(exc.value).lower() for h in llm_module._DECOMMISSION_HINTS), str(exc.value)[:300]
