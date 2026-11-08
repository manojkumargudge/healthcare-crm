"""Brief gaps: form sentiment, attendees, materials catalog, AI-suggested follow-ups."""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools import build_tools
from app.catalog import SEED_MATERIALS, seed_materials
from app.models import HCP, FollowUp, Interaction, Material
from tests.conftest import test_engine

def _tool(db: AsyncSession, name: str):
    return next(t for t in build_tools(db) if t.name == name)


async def _call(db: AsyncSession, name: str, **kwargs: Any) -> dict:
    return json.loads(await _tool(db, name).coroutine(**kwargs))  # type: ignore[misc]  # StructuredTool.coroutine is Optional; always set by build_tools


# =============================================================== 1. sentiment
async def test_form_sentiment_is_stored_and_score_is_null(client: httpx.AsyncClient, hcp: dict) -> None:
    body = (await client.post("/api/interactions", json={"hcp_id": hcp["id"], "hcp_sentiment": "Negative"})).json()
    assert (body["hcp_sentiment"], body["sentiment_score"]) == ("Negative", None)


async def test_form_sentiment_is_optional(client: httpx.AsyncClient, hcp: dict) -> None:
    body = (await client.post("/api/interactions", json={"hcp_id": hcp["id"]})).json()
    assert body["hcp_sentiment"] is None


@pytest.mark.parametrize("label", ["Happy", "positive", "", 1])
async def test_form_rejects_unknown_sentiment(client: httpx.AsyncClient, hcp: dict, label: Any) -> None:
    resp = await client.post("/api/interactions", json={"hcp_id": hcp["id"], "hcp_sentiment": label})
    assert resp.status_code == 422


async def test_patch_rejects_unknown_sentiment(client: httpx.AsyncClient, hcp: dict) -> None:
    created = (await client.post("/api/interactions", json={"hcp_id": hcp["id"]})).json()
    resp = await client.patch(f"/api/interactions/{created['id']}", json={"hcp_sentiment": "Ecstatic"})
    assert resp.status_code == 422


async def test_manual_label_wins_over_ai_label(client: httpx.AsyncClient, hcp: dict, db: AsyncSession) -> None:
    row = Interaction(hcp_id=hcp["id"], channel="chat", hcp_sentiment="Positive", sentiment_score=0.9)
    db.add(row)
    await db.commit()
    body = (await client.patch(f"/api/interactions/{row.id}", json={"hcp_sentiment": "Negative"})).json()
    assert (body["hcp_sentiment"], body["sentiment_score"]) == ("Negative", None)


async def test_edit_tool_normalizes_label_case_and_rejects_unknown(db: AsyncSession) -> None:
    hcp = HCP(name="Dr. Case")
    db.add(hcp)
    await db.commit()
    row = Interaction(hcp_id=hcp.id)
    db.add(row)
    await db.commit()

    out = await _call(db, "edit_interaction", interaction_id=row.id, updates=json.dumps({"hcp_sentiment": "neutral"}))
    assert out["status"] == "updated"
    await db.refresh(row)
    assert row.hcp_sentiment == "Neutral"

    out = await _call(db, "edit_interaction", interaction_id=row.id, updates=json.dumps({"hcp_sentiment": "meh"}))
    assert out == {"error": "hcp_sentiment must be one of Positive, Neutral, Negative"}
    await db.refresh(row)
    assert row.hcp_sentiment == "Neutral"


async def test_log_tool_drops_invalid_ai_sentiment_and_its_score(db: AsyncSession, fake_llm) -> None:
    hcp = HCP(name="Dr. Odd")
    db.add(hcp)
    await db.commit()
    fake_llm(json.dumps({"summary": "s", "hcp_sentiment": "Enthusiastic", "sentiment_score": 0.9}))
    out = await _call(db, "log_interaction", hcp_id=hcp.id, raw_text="notes")
    row = await db.get(Interaction, out["interaction_id"])
    assert row is not None and (row.hcp_sentiment, row.sentiment_score) == (None, None)


# =============================================================== 2. attendees
async def test_create_with_attendees_dedupes_and_uses_hcp_names(
    client: httpx.AsyncClient, hcp: dict, make_hcp
) -> None:
    await make_hcp(name="Dr. Priya Nair")
    body = (
        await client.post(
            "/api/interactions",
            json={"hcp_id": hcp["id"], "attendees": ["dr. priya nair", "  Nurse  Kim ", "nurse kim", "Dr. PRIYA NAIR"]},
        )
    ).json()
    assert body["attendees"] == ["Dr. Priya Nair", "Nurse Kim"]


@pytest.mark.parametrize("attendees", [[""], ["   "], [123], ["x" * 201]])
async def test_create_rejects_bad_attendees(client: httpx.AsyncClient, hcp: dict, attendees: list) -> None:
    resp = await client.post("/api/interactions", json={"hcp_id": hcp["id"], "attendees": attendees})
    assert resp.status_code == 422


async def test_patch_replaces_attendees_with_audit(client: httpx.AsyncClient, hcp: dict) -> None:
    created = (await client.post("/api/interactions", json={"hcp_id": hcp["id"], "attendees": ["A"]})).json()
    body = (await client.patch(f"/api/interactions/{created['id']}", json={"attendees": ["B", "C"]})).json()
    assert body["attendees"] == ["B", "C"]
    assert body["edit_history"][0]["before"] == {"attendees": ["A"]}
    assert body["edit_history"][0]["after"] == {"attendees": ["B", "C"]}


async def test_hcp_search_for_attendee_picker(client: httpx.AsyncClient, make_hcp) -> None:
    await make_hcp(name="Dr. Anjali Rao", specialty="Cardiologist", institution="Manipal Hospital")
    await make_hcp(name="Dr. Vivek Sharma", specialty="Endocrinologist", institution="Apollo Clinic")
    names = lambda r: [h["name"] for h in r.json()]  # noqa: E731
    assert names(await client.get("/api/hcps", params={"q": "rao"})) == ["Dr. Anjali Rao"]
    assert names(await client.get("/api/hcps", params={"q": "apollo"})) == ["Dr. Vivek Sharma"]
    assert names(await client.get("/api/hcps", params={"q": "CARDIO"})) == ["Dr. Anjali Rao"]
    assert len((await client.get("/api/hcps")).json()) == 2  # no q: unchanged behaviour


async def test_log_tool_merges_explicit_and_extracted_attendees(db: AsyncSession, fake_llm) -> None:
    hcp = HCP(name="Dr. Main")
    db.add_all([hcp, HCP(name="Dr. Colleague")])
    await db.commit()
    fake_llm(json.dumps({"summary": "s", "attendees": ["dr. colleague", "Pharmacist Lee"]}))
    out = await _call(db, "log_interaction", hcp_id=hcp.id, raw_text="notes", attendees=["Nurse Kim", "Pharmacist lee"])
    assert out["attendees"] == ["Nurse Kim", "Pharmacist lee", "Dr. Colleague"]


async def test_edit_tool_sets_attendees(db: AsyncSession) -> None:
    hcp = HCP(name="Dr. E")
    db.add(hcp)
    await db.commit()
    row = Interaction(hcp_id=hcp.id, attendees=[])
    db.add(row)
    await db.commit()
    out = await _call(db, "edit_interaction", interaction_id=row.id, updates=json.dumps({"attendees": ["dr. e", "Nurse Kim"]}))
    assert out["changed_fields"] == ["attendees"]
    await db.refresh(row)
    assert row.attendees == ["Dr. E", "Nurse Kim"]


# =============================================================== 3. materials
async def test_seed_materials_is_idempotent(db: AsyncSession) -> None:
    assert await seed_materials(db) == len(SEED_MATERIALS)
    assert await seed_materials(db) == 0
    assert (await db.execute(select(func.count()).select_from(Material))).scalar_one() == len(SEED_MATERIALS)


async def test_material_search(client: httpx.AsyncClient, db: AsyncSession) -> None:
    await seed_materials(db)
    names = [m["name"] for m in (await client.get("/api/materials", params={"q": "cardiomax"})).json()]
    assert names == ["CardioMax dosing guide", "CardioMax patient brochure", "CardioMax Phase III study reprint"]
    assert len((await client.get("/api/materials", params={"limit": 5})).json()) == 5


async def test_add_material_and_duplicate_is_case_insensitive(client: httpx.AsyncClient) -> None:
    first = await client.post("/api/materials", json={"name": "  KOL webinar recording "})
    assert first.status_code == 201 and first.json()["name"] == "KOL webinar recording"
    again = await client.post("/api/materials", json={"name": "kol WEBINAR recording"})
    assert again.status_code == 200 and again.json() == first.json()
    assert (await client.post("/api/materials", json={"name": "  "})).status_code == 422


async def test_interaction_materials_use_catalog_names_and_extend_catalog(
    client: httpx.AsyncClient, hcp: dict, db: AsyncSession
) -> None:
    await seed_materials(db)
    body = (
        await client.post(
            "/api/interactions",
            json={"hcp_id": hcp["id"], "materials_shared": ["cardiomax dosing GUIDE", "Brand-new leave-behind"]},
        )
    ).json()
    assert body["materials_shared"] == ["CardioMax dosing guide", "Brand-new leave-behind"]
    found = (await client.get("/api/materials", params={"q": "brand-new"})).json()
    assert [m["name"] for m in found] == ["Brand-new leave-behind"]


async def test_log_tool_resolves_materials(db: AsyncSession, fake_llm) -> None:
    await seed_materials(db)
    hcp = HCP(name="Dr. M")
    db.add(hcp)
    await db.commit()
    fake_llm(json.dumps({"summary": "s", "materials_shared": ["oncoboost phase iii efficacy pdf"]}))
    out = await _call(db, "log_interaction", hcp_id=hcp.id, raw_text="notes", materials_shared=["Formulary coverage sheet"])
    assert out["materials_shared"] == ["Formulary coverage sheet", "OncoBoost Phase III efficacy PDF"]


MIGRATION_003 = Path(__file__).resolve().parents[1] / "migrations" / "003_attendees_materials.sql"


async def test_migration_003_adds_attendees_and_migrates_material_tags(_schema: None) -> None:
    sql = MIGRATION_003.read_text(encoding="utf-8")
    async with test_engine.connect() as conn:
        await conn.exec_driver_sql("DROP SCHEMA IF EXISTS legacy_003 CASCADE")
        await conn.exec_driver_sql("CREATE SCHEMA legacy_003")
        await conn.exec_driver_sql("SET search_path TO legacy_003")
        await conn.exec_driver_sql("CREATE TABLE interactions (id varchar(36) primary key, materials_shared json)")
        for iid, tags in [
            ("i1", ["Phase III reprint", " dosing card "]),
            ("i2", ["phase iii REPRINT", "", "Coverage sheet"]),
            ("i3", []),
            ("i4", None),
        ]:
            await conn.exec_driver_sql("INSERT INTO interactions VALUES ($1, $2::json)", (iid, json.dumps(tags)))
        await conn.commit()

        raw = (await conn.get_raw_connection()).driver_connection
        assert raw is not None
        await raw.execute(sql)
        await raw.execute(sql)  # idempotent

        names = sorted(r[0] for r in (await conn.exec_driver_sql("SELECT name FROM materials")).all())
        assert names == ["Coverage sheet", "Phase III reprint", "dosing card"]
        attendees = {r[0] for r in (await conn.exec_driver_sql("SELECT attendees::text FROM interactions")).all()}
        assert attendees == {"[]"}
        await conn.exec_driver_sql("SET search_path TO public")
        await conn.exec_driver_sql("DROP SCHEMA legacy_003 CASCADE")
        await conn.commit()


# =============================================================== 4. follow-ups
async def _interaction(client: httpx.AsyncClient, hcp: dict) -> dict:
    return (
        await client.post(
            "/api/interactions",
            json={"hcp_id": hcp["id"], "products_discussed": ["OncoBoost"], "key_topics": ["efficacy"]},
        )
    ).json()


async def test_follow_ups_empty_and_404(client: httpx.AsyncClient, hcp: dict) -> None:
    inter = await _interaction(client, hcp)
    assert (await client.get(f"/api/interactions/{inter['id']}/follow-ups")).json() == []
    assert (await client.get(f"/api/interactions/{uuid.uuid4()}/follow-ups")).status_code == 404
    assert (await client.post(f"/api/interactions/{uuid.uuid4()}/follow-ups/suggestions")).status_code == 404


async def test_suggestions_are_stored_validated_and_capped(client: httpx.AsyncClient, hcp: dict, fake_llm) -> None:
    inter = await _interaction(client, hcp)
    fake = fake_llm(
        json.dumps(
            {
                "follow_ups": [
                    {"reason": "Schedule follow-up meeting", "days_from_now": 14},
                    {"reason": "Send OncoBoost Phase III PDF", "days_from_now": 2},
                    {"reason": "x", "days_from_now": 3},  # too short -> skipped
                    {"reason": "Bad days", "days_from_now": 400},  # out of range -> skipped
                    {"reason": "Add to advisory board invite list", "days_from_now": 7},
                    {"reason": "Fourth valid one", "days_from_now": 1},  # over the cap of 3
                ]
            }
        )
    )
    resp = await client.post(f"/api/interactions/{inter['id']}/follow-ups/suggestions")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert [r["reason"] for r in rows] == [
        "Schedule follow-up meeting",
        "Send OncoBoost Phase III PDF",
        "Add to advisory board invite list",
    ]
    assert {r["status"] for r in rows} == {"Suggested"}
    assert rows[1]["due_date"] == (date.today() + timedelta(days=2)).isoformat()
    assert fake.calls[0]["fast"] is False  # main model
    assert "OncoBoost" in fake.calls[0]["messages"]
    listed = (await client.get(f"/api/interactions/{inter['id']}/follow-ups")).json()
    assert [r["id"] for r in listed] == [r["id"] for r in rows]


async def test_click_to_add_accepts_suggestion_and_regenerate_keeps_it(
    client: httpx.AsyncClient, hcp: dict, fake_llm
) -> None:
    inter = await _interaction(client, hcp)
    fake_llm(
        json.dumps({"follow_ups": [{"reason": "Call back about pricing", "days_from_now": 3},
                                   {"reason": "Send dosing guide", "days_from_now": 1}]}),
        json.dumps({"follow_ups": [{"reason": "Invite to webinar", "days_from_now": 10}]}),
    )
    first = (await client.post(f"/api/interactions/{inter['id']}/follow-ups/suggestions")).json()
    accepted = (await client.patch(f"/api/follow-ups/{first[0]['id']}", json={"status": "Pending"})).json()
    assert accepted["status"] == "Pending"

    await client.post(f"/api/interactions/{inter['id']}/follow-ups/suggestions")
    listed = (await client.get(f"/api/interactions/{inter['id']}/follow-ups")).json()
    assert sorted((r["reason"], r["status"]) for r in listed) == [
        ("Call back about pricing", "Pending"),  # accepted: kept
        ("Invite to webinar", "Suggested"),  # fresh suggestion
        ("Send dosing guide", "Superseded"),  # old unaccepted one: superseded, not deleted
    ]


async def test_stale_suggestion_can_still_be_accepted_after_regenerate(
    client: httpx.AsyncClient, hcp: dict, fake_llm
) -> None:
    """Regression: two concurrent generations (e.g. React StrictMode) deleted the batch
    on screen, so clicking a suggestion returned 404."""
    inter = await _interaction(client, hcp)
    fake_llm(
        json.dumps({"follow_ups": [{"reason": "First batch item", "days_from_now": 1}]}),
        json.dumps({"follow_ups": [{"reason": "Second batch item", "days_from_now": 2}]}),
    )
    [stale] = (await client.post(f"/api/interactions/{inter['id']}/follow-ups/suggestions")).json()
    await client.post(f"/api/interactions/{inter['id']}/follow-ups/suggestions")
    resp = await client.patch(f"/api/follow-ups/{stale['id']}", json={"status": "Pending"})
    assert resp.status_code == 200 and resp.json()["status"] == "Pending"


async def test_empty_generation_keeps_existing_suggestions(client: httpx.AsyncClient, hcp: dict, fake_llm) -> None:
    inter = await _interaction(client, hcp)
    fake_llm(
        json.dumps({"follow_ups": [{"reason": "Keep me", "days_from_now": 1}]}),
        "Sorry, I can't help with that.",  # no JSON at all
    )
    await client.post(f"/api/interactions/{inter['id']}/follow-ups/suggestions")
    assert (await client.post(f"/api/interactions/{inter['id']}/follow-ups/suggestions")).json() == []
    listed = (await client.get(f"/api/interactions/{inter['id']}/follow-ups")).json()
    assert [(r["reason"], r["status"]) for r in listed] == [("Keep me", "Suggested")]


async def test_dismiss_and_invalid_status(client: httpx.AsyncClient, hcp: dict, fake_llm) -> None:
    inter = await _interaction(client, hcp)
    fake_llm(json.dumps({"follow_ups": [{"reason": "Send study", "days_from_now": 1}]}))
    [s] = (await client.post(f"/api/interactions/{inter['id']}/follow-ups/suggestions")).json()
    assert (await client.patch(f"/api/follow-ups/{s['id']}", json={"status": "Dismissed"})).json()["status"] == "Dismissed"
    assert (await client.patch(f"/api/follow-ups/{s['id']}", json={"status": "Suggested"})).status_code == 422
    assert (await client.patch(f"/api/follow-ups/{uuid.uuid4()}", json={"status": "Pending"})).status_code == 404


async def test_llm_failure_returns_502_and_stores_nothing(
    client: httpx.AsyncClient, hcp: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    inter = await _interaction(client, hcp)

    async def boom(*_a: Any, **_kw: Any) -> Any:
        raise RuntimeError("groq down")

    monkeypatch.setattr("app.follow_ups.ainvoke_resilient", boom)
    resp = await client.post(f"/api/interactions/{inter['id']}/follow-ups/suggestions")
    assert resp.status_code == 502
    assert (await client.get(f"/api/interactions/{inter['id']}/follow-ups")).json() == []


async def test_agent_scheduled_follow_up_appears_in_list(client: httpx.AsyncClient, hcp: dict, db: AsyncSession) -> None:
    inter = await _interaction(client, hcp)
    await _call(db, "schedule_follow_up", hcp_id=hcp["id"], reason="Answer pricing question", days_from_now=7,
                interaction_id=inter["id"])
    listed = (await client.get(f"/api/interactions/{inter['id']}/follow-ups")).json()
    assert [(r["reason"], r["status"]) for r in listed] == [("Answer pricing question", "Pending")]


async def test_chat_log_with_attendees_and_materials(client: httpx.AsyncClient, hcp: dict, db: AsyncSession, fake_llm) -> None:
    await seed_materials(db)
    fake_llm(
        AIMessage(
            content="",
            tool_calls=[{
                "name": "log_interaction",
                "args": {"hcp_id": hcp["id"], "raw_text": "met with nurse kim", "attendees": ["Nurse Kim"],
                         "materials_shared": ["cardiomax patient brochure"]},
                "id": "c1",
            }],
        ),
        json.dumps({"summary": "s"}),
        AIMessage(content="Logged."),
    )
    body = (await client.post("/api/chat/turn", json={"session_id": "bg-1", "message": "m", "hcp_id": hcp["id"]})).json()
    assert body["interaction"]["attendees"] == ["Nurse Kim"]
    assert body["interaction"]["materials_shared"] == ["CardioMax patient brochure"]
    _ = datetime.now(timezone.utc), FollowUp  # keep imports used for readability of the module header


# ======================================================== mockup form fields
async def test_create_with_time_outcomes_and_follow_up_actions(client: httpx.AsyncClient, hcp: dict) -> None:
    body = (
        await client.post(
            "/api/interactions",
            json={
                "hcp_id": hcp["id"],
                "interaction_type": "Meeting",
                "interaction_date": "2025-04-19",
                "interaction_time": "19:36",
                "raw_notes": "Discussed OncoBoost efficacy",
                "outcomes": "Agreed to trial with 3 patients",
                "follow_up_actions": "Send Phase III PDF",
            },
        )
    ).json()
    assert body["interaction_type"] == "Meeting"
    assert body["interaction_time"] == "19:36:00"
    assert body["outcomes"] == "Agreed to trial with 3 patients"
    assert body["follow_up_actions"] == "Send Phase III PDF"


async def test_time_is_optional_and_validated(client: httpx.AsyncClient, hcp: dict) -> None:
    assert (await client.post("/api/interactions", json={"hcp_id": hcp["id"]})).json()["interaction_time"] is None
    resp = await client.post("/api/interactions", json={"hcp_id": hcp["id"], "interaction_time": "25:99"})
    assert resp.status_code == 422


async def test_patch_outcomes_is_audited(client: httpx.AsyncClient, hcp: dict) -> None:
    created = (await client.post("/api/interactions", json={"hcp_id": hcp["id"], "outcomes": "old"})).json()
    body = (await client.patch(f"/api/interactions/{created['id']}", json={"outcomes": "new"})).json()
    assert body["outcomes"] == "new"
    assert body["edit_history"][0]["before"] == {"outcomes": "old"}


async def test_log_tool_extracts_outcomes_and_actions(db: AsyncSession, fake_llm) -> None:
    hcp = HCP(name="Dr. O")
    db.add(hcp)
    await db.commit()
    fake_llm(json.dumps({"summary": "s", "outcomes": "Will trial it", "follow_up_actions": ["not a string"]}))
    out = await _call(db, "log_interaction", hcp_id=hcp.id, raw_text="notes")
    row = await db.get(Interaction, out["interaction_id"])
    assert row is not None
    assert (row.outcomes, row.follow_up_actions) == ("Will trial it", None)
