"""Voice note -> transcript -> Topics summary (Groq faked at the SDK boundary), consent fields,
and the products catalog shared by Products Discussed and samples."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools import build_tools
from app.catalog import SEED_PRODUCTS, seed_products
from app.config import settings
from app.models import HCP, ChatMessage, Interaction, Product
from app.voice_notes import MAX_AUDIO_BYTES
from tests.conftest import ADMIN_AUTHZ, ADMIN_ID, test_engine

WAV = (Path(__file__).parent / "fixtures" / "voice_note_sample.wav").read_bytes()
WEBM_HEADER = b"\x1a\x45\xdf\xa3" + b"\x00" * 60
URL = "/api/voice-notes/summarize"


class FakeTranscriptions:
    def __init__(self, text: str = "Met Doctor Rao, discussed CardioMax pricing.", error: Exception | None = None):
        self.text, self.error = text, error
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, _self: Any = None, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return SimpleNamespace(text=self.text)


@pytest.fixture
def whisper(monkeypatch: pytest.MonkeyPatch):
    def install(**kw: Any) -> FakeTranscriptions:
        fake = FakeTranscriptions(**kw)
        from groq.resources.audio.transcriptions import AsyncTranscriptions

        monkeypatch.setattr(AsyncTranscriptions, "create", lambda self, **k: fake(**k))
        return fake

    return install


async def _post(client: httpx.AsyncClient, body: bytes, ctype: str = "audio/wav", consent: str | None = "true"):
    params = {"consent": consent} if consent is not None else {}
    return await client.post(URL, params=params, content=body, headers={"content-type": ctype})


# ------------------------------------------------------------ consent + limits
@pytest.mark.parametrize("consent", [None, "false"])
async def test_requires_consent_and_never_calls_groq(client: httpx.AsyncClient, whisper, consent) -> None:
    fake = whisper()
    resp = await _post(client, WAV, consent=consent)
    assert resp.status_code == 400 and "consent" in resp.json()["detail"]
    assert fake.calls == []


@pytest.mark.parametrize(
    ("body", "ctype"),
    [
        pytest.param(WAV, "text/plain", id="text-plain"),
        pytest.param(WAV, "video/mp4", id="video"),
        pytest.param(WAV, "", id="no-content-type"),
        pytest.param(b"not really audio", "audio/wav", id="wav-type-but-not-wav-bytes"),
        pytest.param(WAV, "audio/webm", id="wav-bytes-declared-webm"),
    ],
)
async def test_rejects_unsupported_or_mismatched_audio(client: httpx.AsyncClient, whisper, body, ctype) -> None:
    fake = whisper()
    resp = await _post(client, body, ctype=ctype)
    assert resp.status_code == 415, resp.text
    assert fake.calls == []


async def test_rejects_empty_audio(client: httpx.AsyncClient, whisper) -> None:
    fake = whisper()
    assert (await _post(client, b"")).status_code == 400
    assert fake.calls == []


async def test_rejects_oversized_audio(client: httpx.AsyncClient, whisper) -> None:
    fake = whisper()
    big = WAV[:44] + b"\x00" * (MAX_AUDIO_BYTES + 1 - 44)
    resp = await _post(client, big)
    assert resp.status_code == 413
    assert fake.calls == []


# ------------------------------------------------------------------ success
async def test_transcribes_and_summarizes_without_storing_anything(
    client: httpx.AsyncClient, whisper, fake_llm, db: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = whisper(text="Met Doctor Rao. Discussed CardioMax efficacy; she asked about pricing.")
    llm = fake_llm("Discussed CardioMax efficacy. HCP asked about pricing.")

    # "Never store audio": any temp-file use during the request fails the test.
    def no_temp_files(*_a: Any, **_kw: Any) -> Any:
        raise AssertionError("audio must not be written to a temporary file")

    for name in ("SpooledTemporaryFile", "NamedTemporaryFile", "TemporaryFile", "mkstemp"):
        monkeypatch.setattr(tempfile, name, no_temp_files)

    resp = await _post(client, WAV)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["transcript"] == "Met Doctor Rao. Discussed CardioMax efficacy; she asked about pricing."
    assert body["summary"] == "Discussed CardioMax efficacy. HCP asked about pricing."
    assert body["transcription_model"] == settings.groq_transcribe_model == "whisper-large-v3-turbo"
    assert body["consent_at"]

    [call] = fake.calls
    assert call["model"] == "whisper-large-v3-turbo"
    assert call["file"][0] == "voice-note.wav" and call["file"][1] == WAV
    assert llm.calls[0]["fast"] is True and "CardioMax" in llm.calls[0]["messages"]

    monkeypatch.undo()
    for model in (Interaction, ChatMessage):
        assert (await db.execute(select(func.count()).select_from(model))).scalar_one() == 0


async def test_webm_with_codecs_parameter_is_accepted(client: httpx.AsyncClient, whisper, fake_llm) -> None:
    fake = whisper()
    fake_llm("summary")
    resp = await _post(client, WEBM_HEADER, ctype="audio/webm;codecs=opus")
    assert resp.status_code == 200, resp.text
    assert fake.calls[0]["file"][0] == "voice-note.webm"


async def test_transcribe_model_is_env_driven(client: httpx.AsyncClient, whisper, fake_llm, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "groq_transcribe_model", "whisper-large-v3")
    fake = whisper()
    fake_llm("summary")
    body = (await _post(client, WAV)).json()
    assert fake.calls[0]["model"] == body["transcription_model"] == "whisper-large-v3"


async def test_no_speech_is_422(client: httpx.AsyncClient, whisper, fake_llm) -> None:
    whisper(text="   ")
    llm = fake_llm()
    resp = await _post(client, WAV)
    assert resp.status_code == 422
    assert llm.calls == []


async def test_groq_failure_is_502_without_echoing_audio(client: httpx.AsyncClient, whisper) -> None:
    whisper(error=RuntimeError("upstream exploded"))
    resp = await _post(client, WAV)
    assert resp.status_code == 502
    assert resp.json()["detail"] == "Transcription failed (RuntimeError)."


# ------------------------------------------------------------ consent fields
async def test_interaction_stores_voice_consent(client: httpx.AsyncClient, hcp: dict) -> None:
    body = (
        await client.post(
            "/api/interactions",
            json={"hcp_id": hcp["id"], "voice_note_consent": True, "voice_note_consent_at": "2026-11-08T10:00:00Z"},
        )
    ).json()
    assert body["voice_note_consent"] is True
    assert body["voice_note_consent_at"].startswith("2026-11-08T10:00:00")


async def test_consent_without_timestamp_gets_server_time(client: httpx.AsyncClient, hcp: dict) -> None:
    body = (await client.post("/api/interactions", json={"hcp_id": hcp["id"], "voice_note_consent": True})).json()
    assert body["voice_note_consent"] is True and body["voice_note_consent_at"]


async def test_consent_defaults_false_and_timestamp_needs_consent(client: httpx.AsyncClient, hcp: dict) -> None:
    body = (await client.post("/api/interactions", json={"hcp_id": hcp["id"]})).json()
    assert (body["voice_note_consent"], body["voice_note_consent_at"]) == (False, None)
    resp = await client.post(
        "/api/interactions", json={"hcp_id": hcp["id"], "voice_note_consent_at": "2026-11-08T10:00:00Z"}
    )
    assert resp.status_code == 422


# ------------------------------------------------------------ products catalog
async def test_seed_products_idempotent(db: AsyncSession) -> None:
    await db.execute(delete(Product))
    await db.commit()
    assert await seed_products(db) == len(SEED_PRODUCTS)
    assert await seed_products(db) == 0


async def _catalog_size(db: AsyncSession) -> int:
    return int((await db.execute(select(func.count()).select_from(Product))).scalar_one())


# ------------------------------------------------------- admin-only catalog
async def test_product_search_is_read_only(client: httpx.AsyncClient) -> None:
    assert [p["name"] for p in (await client.get("/api/products", params={"q": "onco"})).json()] == ["OncoBoost"]
    assert (await client.post("/api/products", json={"name": "NeuroCalm"})).status_code == 405


async def test_form_accepts_catalog_products_case_insensitively(client: httpx.AsyncClient, hcp: dict) -> None:
    body = (
        await client.post(
            "/api/interactions",
            json={
                "hcp_id": hcp["id"],
                "products_discussed": ["cardiomax", "OncoBoost"],
                "samples_dropped": [{"product": "oncoboost", "qty": 1}],
            },
        )
    ).json()
    assert body["products_discussed"] == ["CardioMax", "OncoBoost"]
    assert body["samples_dropped"] == [{"product": "OncoBoost", "qty": 1}]
    assert body["unmatched_products"] == []


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"products_discussed": ["CardioMax", "Brand New Rx"]}, id="products"),
        pytest.param({"samples_dropped": [{"product": "Brand New Rx", "qty": 1}]}, id="sample"),
        pytest.param({"products_discussed": ["cardiomx"]}, id="typo-is-not-guessed-for-the-form"),
    ],
)
async def test_form_rejects_unknown_product_and_never_adds_it(
    client: httpx.AsyncClient, hcp: dict, db: AsyncSession, payload: dict
) -> None:
    before = await _catalog_size(db)
    resp = await client.post("/api/interactions", json={"hcp_id": hcp["id"], **payload})
    assert resp.status_code == 422
    assert "Pick from the product list" in resp.json()["detail"]
    assert (await client.get("/api/interactions")).json() == []
    assert await _catalog_size(db) == before


async def test_form_patch_rejects_unknown_product(client: httpx.AsyncClient, hcp: dict, db: AsyncSession) -> None:
    created = (await client.post("/api/interactions", json={"hcp_id": hcp["id"], "products_discussed": ["CardioMax"]})).json()
    before = await _catalog_size(db)
    resp = await client.patch(f"/api/interactions/{created['id']}", json={"products_discussed": ["Mystery Pill"]})
    assert resp.status_code == 422 and "Mystery Pill" in resp.json()["detail"]
    again = (await client.get(f"/api/interactions/{created['id']}")).json()
    assert again["products_discussed"] == ["CardioMax"] and again["edit_history"] == []
    assert await _catalog_size(db) == before


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("cardiomx", "CardioMax"),  # typo
        ("CARDIOMAX", "CardioMax"),  # case
        ("Cardio Max", "CardioMax"),  # spacing
        ("cardio-max", "CardioMax"),  # punctuation
        ("oncobost", "OncoBoost"),
        ("glucobalanse", "GlucoBalance"),
        ("Cardio", None),  # too far from anything
        ("Zylotrex", None),
        ("", None),
    ],
)
def test_closest_product(typed: str, expected: str | None) -> None:
    from app.catalog import closest_product

    assert closest_product(typed, ["CardioMax", "CardioPlus", "OncoBoost", "OncoLife", "GlucoBalance"]) == expected


async def test_edit_tool_fuzzy_matches_and_accumulates_unmatched(db: AsyncSession) -> None:
    hcp = HCP(name="Dr. E")
    db.add(hcp)
    await db.commit()
    row = Interaction(owner_id=ADMIN_ID, hcp_id=hcp.id, unmatched_products=["Old Name"])
    db.add(row)
    await db.commit()
    before = await _catalog_size(db)
    tool = next(t for t in build_tools(db, ADMIN_AUTHZ) if t.name == "edit_interaction")
    await tool.coroutine(interaction_id=row.id, updates=json.dumps({"products_discussed": ["oncobost", "Newdrug"]}))  # type: ignore[misc]  # StructuredTool.coroutine is Optional; always set by build_tools
    await db.refresh(row)
    assert row.products_discussed == ["OncoBoost"]
    assert row.unmatched_products == ["Old Name", "Newdrug"]
    assert await _catalog_size(db) == before


async def test_new_interactions_default_to_no_unmatched_products(client: httpx.AsyncClient, hcp: dict) -> None:
    assert (await client.post("/api/interactions", json={"hcp_id": hcp["id"]})).json()["unmatched_products"] == []


async def test_admin_cli_adds_products_idempotently(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    from app import admin
    from tests.conftest import TestSessionLocal

    monkeypatch.setattr(admin, "AsyncSessionLocal", TestSessionLocal)
    assert await admin.add_product("  NeuroCalm ") == ("NeuroCalm", True)
    assert await admin.add_product("neurocalm") == ("NeuroCalm", False)
    assert "NeuroCalm" in await admin.list_products()


MIGRATION_006 = Path(__file__).resolve().parents[1] / "migrations" / "006_products_voice_consent.sql"


async def test_migration_006_backfills_products_and_adds_consent(_schema: None) -> None:
    sql = MIGRATION_006.read_text(encoding="utf-8")
    async with test_engine.connect() as conn:
        await conn.exec_driver_sql("DROP SCHEMA IF EXISTS legacy_006 CASCADE")
        await conn.exec_driver_sql("CREATE SCHEMA legacy_006")
        await conn.exec_driver_sql("SET search_path TO legacy_006")
        await conn.exec_driver_sql(
            "CREATE TABLE interactions (id varchar(36) primary key, products_discussed json, samples_dropped json)"
        )
        rows = [
            ("i1", ["CardioMax", " oncoboost "], [{"product": "CARDIOMAX", "qty": 1}]),
            ("i2", ["OncoBoost", ""], [{"product": "SampleOnly", "qty": 2}, "junk"]),
        ]
        for iid, products, samples in rows:
            await conn.exec_driver_sql(
                "INSERT INTO interactions VALUES ($1, $2::json, $3::json)", (iid, json.dumps(products), json.dumps(samples))
            )
        await conn.commit()
        raw = (await conn.get_raw_connection()).driver_connection
        assert raw is not None
        await raw.execute(sql)
        await raw.execute(sql)  # idempotent
        names = sorted(str(r[0]) for r in (await conn.exec_driver_sql("SELECT name FROM products")).all())
        assert [n.lower() for n in names] == ["cardiomax", "oncoboost", "sampleonly"]
        consent = (await conn.exec_driver_sql("SELECT DISTINCT voice_note_consent FROM interactions")).all()
        assert [tuple(r) for r in consent] == [(False,)]
        await conn.exec_driver_sql("SET search_path TO public")
        await conn.exec_driver_sql("DROP SCHEMA legacy_006 CASCADE")
        await conn.commit()
