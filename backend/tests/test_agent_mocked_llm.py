"""LLM-dependent paths, with Groq fully faked (no network)."""

import json
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent import llm as llm_module
from app.agent.tools import _extract_json, build_tools
from app.models import HCP, Interaction
from tests.conftest import GroqCalledError


# ------------------------------------------------------- guard sanity ------
async def test_real_groq_is_blocked() -> None:
    with pytest.raises(GroqCalledError):
        await llm_module.get_llm().ainvoke("hi")


# ------------------------------------------------- ainvoke_resilient --------
class _FakeChatGroq:
    """Records which model names were tried; fails per a scripted map."""

    tried: list[str] = []
    failures: dict[str, Exception] = {}

    def __init__(self, *, api_key: str, model: str, temperature: float) -> None:
        self.model = model

    def bind_tools(self, tools: Any) -> "_FakeChatGroq":
        return self

    async def ainvoke(self, messages: Any) -> AIMessage:
        _FakeChatGroq.tried.append(self.model)
        if self.model in _FakeChatGroq.failures:
            raise _FakeChatGroq.failures[self.model]
        return AIMessage(content=f"from {self.model}")


@pytest.fixture
def fake_chatgroq(monkeypatch: pytest.MonkeyPatch) -> type[_FakeChatGroq]:
    _FakeChatGroq.tried = []
    _FakeChatGroq.failures = {}
    monkeypatch.setattr(llm_module, "ChatGroq", _FakeChatGroq)
    return _FakeChatGroq


async def test_resilient_uses_primary_when_available(fake_chatgroq: type[_FakeChatGroq]) -> None:
    resp, model = await llm_module.ainvoke_resilient("hi")
    assert model == llm_module.PRIMARY_CHAIN[0]
    assert fake_chatgroq.tried == [model]
    assert resp.content == f"from {model}"


async def test_resilient_falls_through_on_decommissioned(fake_chatgroq: type[_FakeChatGroq]) -> None:
    first, second = llm_module.PRIMARY_CHAIN[:2]
    fake_chatgroq.failures = {first: RuntimeError("The model `x` has been decommissioned")}
    _resp, model = await llm_module.ainvoke_resilient("hi")
    assert model == second
    assert fake_chatgroq.tried == [first, second]


async def test_resilient_heavy_chain(fake_chatgroq: type[_FakeChatGroq]) -> None:
    _resp, model = await llm_module.ainvoke_resilient("hi", heavy=True)
    assert model == llm_module.HEAVY_CHAIN[0]


async def test_resilient_does_not_retry_other_errors(fake_chatgroq: type[_FakeChatGroq]) -> None:
    first = llm_module.PRIMARY_CHAIN[0]
    fake_chatgroq.failures = {first: RuntimeError("401 invalid api key")}
    with pytest.raises(RuntimeError, match="invalid api key"):
        await llm_module.ainvoke_resilient("hi")
    assert fake_chatgroq.tried == [first]


async def test_resilient_raises_last_error_when_chain_exhausted(fake_chatgroq: type[_FakeChatGroq]) -> None:
    chain = llm_module._dedupe(llm_module.PRIMARY_CHAIN)
    fake_chatgroq.failures = {m: RuntimeError(f"{m} model_not_found") for m in chain}
    with pytest.raises(RuntimeError, match=f"{chain[-1]} model_not_found"):
        await llm_module.ainvoke_resilient("hi")
    assert fake_chatgroq.tried == chain


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
    tool = next(t for t in build_tools(db) if t.name == "log_interaction")
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
    tool = next(t for t in build_tools(db) if t.name == "log_interaction")
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
    tool = next(t for t in build_tools(db) if t.name == "log_interaction")
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
    tool = next(t for t in build_tools(db) if t.name == "log_interaction")
    out = json.loads(await tool.coroutine(hcp_id=hcp.id, raw_text="notes"))  # type: ignore[misc]  # StructuredTool.coroutine is Optional; always set by build_tools
    assert out["samples_dropped"] == []


async def test_edit_interaction_tool_reruns_compliance(db: AsyncSession) -> None:
    hcp = HCP(name="Dr. VT", state="Vermont")
    db.add(hcp)
    await db.commit()
    row = Interaction(hcp_id=hcp.id, samples_dropped=[], compliance_flag=False)
    db.add(row)
    await db.commit()

    tool = next(t for t in build_tools(db) if t.name == "edit_interaction")
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
    row = Interaction(hcp_id=hcp.id, samples_dropped=[{"product": "X", "qty": 1}], compliance_flag=True, compliance_notes="n")
    db.add(row)
    await db.commit()

    tool = next(t for t in build_tools(db) if t.name == "edit_interaction")
    out = json.loads(await tool.coroutine(interaction_id=row.id, updates=json.dumps({"raw_notes": "x"})))  # type: ignore[misc]  # StructuredTool.coroutine is Optional; always set by build_tools
    assert "compliance_flag" not in out
    await db.refresh(row)
    assert (row.compliance_flag, row.compliance_notes) == (True, "n")
