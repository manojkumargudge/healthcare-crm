"""LLM-dependent paths, with Groq fully faked (no network)."""

import json
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import llm as llm_module
from app.agent.tools import _extract_json, build_tools
from sqlalchemy import select

from app.models import HCP, ChatMessage, Interaction
from tests.conftest import ADMIN_AUTHZ, ADMIN_ID, GroqCalledError


# ------------------------------------------------------- guard sanity ------
async def test_real_groq_is_blocked() -> None:
    with pytest.raises(GroqCalledError):
        await llm_module.get_llm().ainvoke("hi")


# ------------------------------------------------- ainvoke_resilient --------
class _FakeChatGroq:
    """Records which model names were tried; fails per a scripted map."""

    tried: list[str] = []
    failures: dict[str, Exception] = {}

    def __init__(self, *, model: str, **_kwargs: Any) -> None:
        self.model = model

    def bind_tools(self, tools: Any) -> "_FakeChatGroq":
        return self

    async def ainvoke(self, messages: Any) -> AIMessage:
        _FakeChatGroq.tried.append(self.model)
        if self.model in _FakeChatGroq.failures:
            raise _FakeChatGroq.failures[self.model]
        return AIMessage(content=f"from {self.model}")


MAIN, FAST = "main-model-x", "fast-model-y"


@pytest.fixture
def fake_chatgroq(monkeypatch: pytest.MonkeyPatch) -> type[_FakeChatGroq]:
    _FakeChatGroq.tried = []
    _FakeChatGroq.failures = {}
    monkeypatch.setattr(llm_module, "ChatGroq", _FakeChatGroq)
    # Pin the configured models so a developer's local .env can't change results.
    monkeypatch.setattr(llm_module.settings, "groq_model", MAIN)
    monkeypatch.setattr(llm_module.settings, "groq_model_fast", FAST)
    return _FakeChatGroq


def test_model_chain_order(fake_chatgroq: type[_FakeChatGroq]) -> None:
    assert llm_module.model_chain() == [MAIN, FAST]
    assert llm_module.model_chain(fast=True) == [FAST, MAIN]


def test_model_chain_dedupes_when_both_settings_match(
    fake_chatgroq: type[_FakeChatGroq], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_module.settings, "groq_model_fast", MAIN)
    assert llm_module.model_chain() == [MAIN]


async def test_resilient_uses_main_model_by_default(fake_chatgroq: type[_FakeChatGroq]) -> None:
    resp, model = await llm_module.ainvoke_resilient("hi")
    assert model == MAIN
    assert fake_chatgroq.tried == [MAIN]
    assert resp.content == f"from {MAIN}"


async def test_resilient_fast_uses_fast_model(fake_chatgroq: type[_FakeChatGroq]) -> None:
    _resp, model = await llm_module.ainvoke_resilient("hi", fast=True)
    assert model == FAST
    assert fake_chatgroq.tried == [FAST]


async def test_resilient_falls_back_to_other_model_on_decommissioned(
    fake_chatgroq: type[_FakeChatGroq], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("WARNING", logger="hcp_crm.agent.llm")
    fake_chatgroq.failures = {MAIN: RuntimeError("The model `main-model-x` has been decommissioned")}
    _resp, model = await llm_module.ainvoke_resilient("hi")
    assert model == FAST
    assert fake_chatgroq.tried == [MAIN, FAST]
    [record] = [r for r in caplog.records if r.name == "hcp_crm.agent.llm"]
    assert record.levelname == "WARNING"
    assert record.getMessage() == (
        f"Groq model fallback: {MAIN} -> {FAST} (reason: The model `main-model-x` has been decommissioned)"
    )


async def test_fallback_warning_when_no_model_left(
    fake_chatgroq: type[_FakeChatGroq], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("WARNING", logger="hcp_crm.agent.llm")
    fake_chatgroq.failures = {m: RuntimeError("model_not_found") for m in (MAIN, FAST)}
    with pytest.raises(RuntimeError):
        await llm_module.ainvoke_resilient("hi")
    assert [r.getMessage() for r in caplog.records if r.name == "hcp_crm.agent.llm"] == [
        f"Groq model fallback: {MAIN} -> {FAST} (reason: model_not_found)",
        f"Groq model fallback: {FAST} -> none left (reason: model_not_found)",
    ]


async def test_resilient_does_not_retry_other_errors(fake_chatgroq: type[_FakeChatGroq]) -> None:
    fake_chatgroq.failures = {MAIN: RuntimeError("401 invalid api key")}
    with pytest.raises(RuntimeError, match="invalid api key"):
        await llm_module.ainvoke_resilient("hi")
    assert fake_chatgroq.tried == [MAIN]


async def test_resilient_raises_last_error_when_both_models_gone(fake_chatgroq: type[_FakeChatGroq]) -> None:
    fake_chatgroq.failures = {m: RuntimeError(f"{m} model_not_found") for m in (MAIN, FAST)}
    with pytest.raises(RuntimeError, match=f"{FAST} model_not_found"):
        await llm_module.ainvoke_resilient("hi")
    assert fake_chatgroq.tried == [MAIN, FAST]


async def test_extraction_uses_fast_model_and_recommendation_uses_main(db: AsyncSession, fake_llm) -> None:
    hcp = HCP(name="Dr. Route")
    db.add(hcp)
    await db.commit()
    fake = fake_llm(json.dumps({"summary": "s"}), "Bring the new study.")
    tools = {t.name: t for t in build_tools(db, ADMIN_AUTHZ)}
    await tools["log_interaction"].coroutine(hcp_id=hcp.id, raw_text="notes")  # type: ignore[misc]  # StructuredTool.coroutine is Optional; always set by build_tools
    await tools["suggest_next_best_action"].coroutine(hcp_id=hcp.id)  # type: ignore[misc]  # StructuredTool.coroutine is Optional; always set by build_tools
    assert [c["fast"] for c in fake.calls] == [True, False]


# ------------------------------------------------------- _extract_json ------
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('Sure! Here you go: {"a": 1} hope that helps', {"a": 1}),
        ("no json here", {}),
    ],
)
def test_extract_json(text: str, expected: dict) -> None:
    assert _extract_json(text) == expected


# ------------------------------------------- log_interaction tool (DB) ------
async def test_log_interaction_tool_persists_extracted_fields(db: AsyncSession, fake_llm) -> None:
    hcp = HCP(name="Dr. Rao", specialty="Cardiologist", state="Karnataka")
    db.add(hcp)
    await db.commit()

    fake = fake_llm(
        json.dumps(
            {
                "summary": "Met Dr. Rao re CardioMax.",
                "products_discussed": ["CardioMax"],
                "samples_dropped": [{"product": "CardioMax", "qty": 3}],
                "materials_shared": [],
                "key_topics": ["pricing"],
                "hcp_sentiment": "Positive",
                "sentiment_score": 0.7,
            }
        )
    )
    tool = next(t for t in build_tools(db, ADMIN_AUTHZ) if t.name == "log_interaction")
    out = json.loads(await tool.coroutine(hcp_id=hcp.id, raw_text="Met Dr Rao, 3 samples"))  # type: ignore[misc]  # StructuredTool.coroutine is Optional; always set by build_tools

    assert out["status"] == "logged"
    assert len(fake.calls) == 1
    assert "Dr. Rao" in fake.calls[0]["messages"]

    row = await db.get(Interaction, out["interaction_id"])
    assert row is not None
    assert row.channel == "chat"
    assert row.ai_summary == "Met Dr. Rao re CardioMax."
    assert row.samples_dropped == [{"product": "CardioMax", "qty": 3}]
    assert row.sentiment_score == 0.7
    assert row.raw_notes == "Met Dr Rao, 3 samples"


async def test_log_interaction_tool_unknown_hcp_skips_llm(db: AsyncSession, fake_llm) -> None:
    fake = fake_llm()
    tool = next(t for t in build_tools(db, ADMIN_AUTHZ) if t.name == "log_interaction")
    out = json.loads(await tool.coroutine(hcp_id="missing", raw_text="x"))  # type: ignore[misc]  # StructuredTool.coroutine is Optional; always set by build_tools
    assert out == {"error": "No HCP found with id missing"}
    assert fake.calls == []


# ------------------------------------------------- chat turn end-to-end -----
async def test_chat_turn_with_tool_call(client: httpx.AsyncClient, hcp: dict, fake_llm) -> None:
    extraction = json.dumps({"summary": "Quick call.", "hcp_sentiment": "Neutral"})
    fake = fake_llm(
        # 1) agent decides to call log_interaction
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "log_interaction",
                    "args": {"hcp_id": hcp["id"], "raw_text": "quick call", "interaction_type": "Call"},
                    "id": "call_1",
                }
            ],
        ),
        # 2) log_interaction's own extraction LLM call
        extraction,
        # 3) agent's final reply
        AIMessage(content="Logged your call."),
    )

    resp = await client.post(
        "/api/chat/turn", json={"session_id": "s-1", "message": "quick call", "hcp_id": hcp["id"]}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["reply"] == "Logged your call."
    assert body["tool_calls"] == [
        {"tool": "log_interaction", "args": {"hcp_id": hcp["id"], "raw_text": "quick call", "interaction_type": "Call"}}
    ]
    assert body["interaction"]["interaction_type"] == "Call"
    assert body["interaction"]["ai_summary"] == "Quick call."
    assert len(fake.calls) == 3

    history = (await client.get("/api/chat/s-1/history")).json()
    assert [h["role"] for h in history] == ["user", "tool", "assistant"]
    assert history[0]["content"] == "quick call"  # stored without the injected HCP instructions


async def test_log_interaction_tool_coerces_and_skips_bad_llm_samples(
    db: AsyncSession, fake_llm, caplog: pytest.LogCaptureFixture
) -> None:
    hcp = HCP(name="Dr. Rao", state="Karnataka")
    db.add(hcp)
    await db.commit()
    fake_llm(
        json.dumps(
            {
                "summary": "s",
                "samples_dropped": [
                    {"product": "Good", "qty": "3"},  # coerced -> 3
                    {"product": "NoQty", "qty": None},  # skipped
                    {"product": "", "qty": 1},  # skipped
                    {"product": "Neg", "qty": -2},  # skipped
                    "garbage",  # skipped
                ],
            }
        )
    )
    caplog.set_level("WARNING", logger="hcp_crm.samples")
    tool = next(t for t in build_tools(db, ADMIN_AUTHZ) if t.name == "log_interaction")
    out = json.loads(await tool.coroutine(hcp_id=hcp.id, raw_text="notes"))  # type: ignore[misc]  # StructuredTool.coroutine is Optional; always set by build_tools

    assert out["samples_dropped"] == [{"product": "Good", "qty": 3}]
    row = await db.get(Interaction, out["interaction_id"])
    assert row is not None and row.samples_dropped == [{"product": "Good", "qty": 3}]
    skipped = [r for r in caplog.records if "log_interaction: skipping invalid sample" in r.message]
    assert len(skipped) == 4


async def test_log_interaction_tool_tolerates_non_list_samples(db: AsyncSession, fake_llm) -> None:
    hcp = HCP(name="Dr. Rao")
    db.add(hcp)
    await db.commit()
    fake_llm(json.dumps({"summary": "s", "samples_dropped": "three boxes"}))
    tool = next(t for t in build_tools(db, ADMIN_AUTHZ) if t.name == "log_interaction")
    out = json.loads(await tool.coroutine(hcp_id=hcp.id, raw_text="notes"))  # type: ignore[misc]  # StructuredTool.coroutine is Optional; always set by build_tools
    assert out["samples_dropped"] == []


async def test_edit_interaction_tool_reruns_compliance(db: AsyncSession) -> None:
    hcp = HCP(name="Dr. VT", state="Vermont")
    db.add(hcp)
    await db.commit()
    row = Interaction(owner_id=ADMIN_ID, hcp_id=hcp.id, samples_dropped=[], compliance_flag=False)
    db.add(row)
    await db.commit()

    tool = next(t for t in build_tools(db, ADMIN_AUTHZ) if t.name == "edit_interaction")
    out = json.loads(
        await tool.coroutine(  # type: ignore[misc]  # StructuredTool.coroutine is Optional; always set by build_tools
            interaction_id=row.id,
            updates=json.dumps({"samples_dropped": [{"product": "X", "qty": "2"}, {"product": "Bad", "qty": "?"}]}),
        )
    )
    assert out["changed_fields"] == ["samples_dropped"]
    assert out["compliance_flag"] is True
    assert out["compliance_notes"] == "X: 2 units exceeds the 0-unit limit for Vermont"

    await db.refresh(row)
    assert row.samples_dropped == [{"product": "X", "qty": 2}]  # bad item skipped, string coerced
    assert row.compliance_flag is True


async def test_edit_interaction_tool_without_samples_skips_compliance(db: AsyncSession) -> None:
    hcp = HCP(name="Dr. VT", state="Vermont")
    db.add(hcp)
    await db.commit()
    row = Interaction(owner_id=ADMIN_ID, hcp_id=hcp.id, samples_dropped=[{"product": "X", "qty": 1}], compliance_flag=True, compliance_notes="n")
    db.add(row)
    await db.commit()

    tool = next(t for t in build_tools(db, ADMIN_AUTHZ) if t.name == "edit_interaction")
    out = json.loads(await tool.coroutine(interaction_id=row.id, updates=json.dumps({"raw_notes": "x"})))  # type: ignore[misc]  # StructuredTool.coroutine is Optional; always set by build_tools
    assert "compliance_flag" not in out
    await db.refresh(row)
    assert (row.compliance_flag, row.compliance_notes) == (True, "n")


# ------------------------------------------------ shared Groq client --------
@pytest.fixture
async def fresh_groq_client():
    await llm_module.close_groq_client()
    yield
    await llm_module.close_groq_client()


async def test_one_shared_groq_client_across_calls(fresh_groq_client: None) -> None:
    a = llm_module._chat("m1", 0.1)
    b = llm_module._chat("m2", 0.5)
    assert llm_module._clients is not None
    sync_client, async_client = llm_module._clients
    assert a.async_client is b.async_client is async_client.chat.completions
    assert a.client is b.client is sync_client.chat.completions


async def test_init_is_idempotent_and_close_releases_client(fresh_groq_client: None) -> None:
    llm_module.init_groq_client()
    first = llm_module._clients
    llm_module.init_groq_client()
    assert llm_module._clients is first and first is not None
    sync_client, async_client = first
    await llm_module.close_groq_client()
    assert llm_module._clients is None
    assert async_client.is_closed() and sync_client.is_closed()
    await llm_module.close_groq_client()  # second close is a no-op


async def test_lifespan_creates_and_closes_shared_client(
    fresh_groq_client: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import main as main_module

    async def noop() -> None:
        return None

    monkeypatch.setattr(main_module, "init_db", noop)
    monkeypatch.setattr(main_module, "seed_materials", lambda _s: noop())
    async with main_module.app.router.lifespan_context(main_module.app):
        assert llm_module._clients is not None
        _sync, async_client = llm_module._clients
    assert llm_module._clients is None
    assert async_client.is_closed()


# --------------------------------------------- chat turns: no duplicates ----
async def test_three_turns_store_one_log_interaction_row(
    client: httpx.AsyncClient, hcp: dict, db: AsyncSession, fake_llm
) -> None:
    """Regression: every turn used to re-save all earlier tool messages and
    report the whole session's tool calls."""
    fake_llm(
        # turn 1: tool call -> extraction -> final reply
        AIMessage(
            content="",
            tool_calls=[
                {"name": "log_interaction", "args": {"hcp_id": hcp["id"], "raw_text": "visit"}, "id": "call_log_1"}
            ],
        ),
        json.dumps({"summary": "Visit logged."}),
        AIMessage(content="Logged it."),
        # turns 2 and 3: plain replies
        AIMessage(content="Anything else?"),
        AIMessage(content="Done."),
    )
    bodies = []
    for msg in ("met the doctor", "thanks", "bye"):
        resp = await client.post(
            "/api/chat/turn", json={"session_id": "dupes-1", "message": msg, "hcp_id": hcp["id"]}
        )
        assert resp.status_code == 200, resp.text
        bodies.append(resp.json())

    assert [len(b["tool_calls"]) for b in bodies] == [1, 0, 0]
    assert bodies[0]["tool_calls"][0]["tool"] == "log_interaction"
    assert bodies[0]["interaction"] is not None
    assert bodies[1]["interaction"] is None and bodies[2]["interaction"] is None  # not re-reported
    assert [b["reply"] for b in bodies] == ["Logged it.", "Anything else?", "Done."]

    rows = (await db.execute(select(ChatMessage).where(ChatMessage.session_id == "dupes-1"))).scalars().all()
    tool_rows = [r for r in rows if r.role == "tool"]
    assert [(r.tool_name, r.tool_call_id) for r in tool_rows] == [("log_interaction", "call_log_1")]
    assert sorted(r.role for r in rows) == ["assistant"] * 3 + ["tool"] + ["user"] * 3
    assert len((await db.execute(select(Interaction))).scalars().all()) == 1


async def test_tool_calls_in_later_turn_are_reported_alone(client: httpx.AsyncClient, hcp: dict, fake_llm) -> None:
    fake_llm(
        AIMessage(content="", tool_calls=[{"name": "get_hcp_context", "args": {"hcp_id": hcp["id"]}, "id": "c1"}]),
        AIMessage(content="Here is the context."),
        AIMessage(content="", tool_calls=[{"name": "get_hcp_context", "args": {"hcp_id": hcp["id"]}, "id": "c2"}]),
        AIMessage(content="Same context again."),
    )
    turn = {"session_id": "dupes-2", "hcp_id": hcp["id"]}
    first = (await client.post("/api/chat/turn", json={**turn, "message": "ctx"})).json()
    second = (await client.post("/api/chat/turn", json={**turn, "message": "ctx again"})).json()
    expected = [{"tool": "get_hcp_context", "args": {"hcp_id": hcp["id"]}}]
    assert first["tool_calls"] == expected
    assert second["tool_calls"] == expected
    history = (await client.get("/api/chat/dupes-2/history")).json()
    assert [h["role"] for h in history].count("tool") == 2  # one per real call, not 1 + 2


# ------------------------------------------- sentiment label vs score -------
async def test_edit_tool_nulls_score_when_sentiment_label_changes(db: AsyncSession) -> None:
    hcp = HCP(name="Dr. S")
    db.add(hcp)
    await db.commit()
    row = Interaction(owner_id=ADMIN_ID, hcp_id=hcp.id, hcp_sentiment="Positive", sentiment_score=0.6)
    db.add(row)
    await db.commit()

    tool = next(t for t in build_tools(db, ADMIN_AUTHZ) if t.name == "edit_interaction")
    out = json.loads(
        await tool.coroutine(interaction_id=row.id, updates=json.dumps({"hcp_sentiment": "Neutral"}))  # type: ignore[misc]  # StructuredTool.coroutine is Optional; always set by build_tools
    )
    assert sorted(out["changed_fields"]) == ["hcp_sentiment", "sentiment_score"]
    await db.refresh(row)
    assert (row.hcp_sentiment, row.sentiment_score) == ("Neutral", None)
    assert row.edit_history[-1]["before"] == {"hcp_sentiment": "Positive", "sentiment_score": 0.6}
    assert row.edit_history[-1]["after"] == {"hcp_sentiment": "Neutral", "sentiment_score": None}
