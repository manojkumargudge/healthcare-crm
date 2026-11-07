"""Interaction create / patch / get through the HTTP API (structured-form path)."""

import uuid
from datetime import date
from typing import Any

import httpx
import pytest


async def _create(client: httpx.AsyncClient, **body: Any) -> httpx.Response:
    return await client.post("/api/interactions", json=body)


# ---------------------------------------------------------------- create ----
async def test_create_minimal_interaction_defaults(client: httpx.AsyncClient, hcp: dict) -> None:
    resp = await _create(client, hcp_id=hcp["id"])
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["hcp_id"] == hcp["id"]
    assert body["interaction_type"] == "Visit"
    assert body["channel"] == "form"
    assert body["interaction_date"] == date.today().isoformat()
    assert body["products_discussed"] == []
    assert body["samples_dropped"] == []
    assert body["compliance_flag"] is False
    assert body["compliance_notes"] is None  # compliance only runs when samples were dropped
    assert body["edit_history"] == []


async def test_create_full_interaction_roundtrip(client: httpx.AsyncClient, hcp: dict) -> None:
    payload = {
        "hcp_id": hcp["id"],
        "interaction_type": "Call",
        "interaction_date": "2026-01-15",
        "products_discussed": ["CardioMax"],
        "samples_dropped": [{"product": "CardioMax", "qty": 2}],
        "materials_shared": ["Phase III brochure"],
        "key_topics": ["efficacy", "pricing"],
        "raw_notes": "Discussed efficacy data.",
    }
    created = (await client.post("/api/interactions", json=payload)).json()

    fetched = await client.get(f"/api/interactions/{created['id']}")
    assert fetched.status_code == 200
    body = fetched.json()
    for key, value in payload.items():
        assert body[key] == value
    # Karnataka -> default limit (4); 2 units is fine.
    assert body["compliance_flag"] is False
    assert body["compliance_notes"] == "No compliance issues detected."


async def test_create_runs_compliance_and_flags_over_limit(client: httpx.AsyncClient, make_hcp) -> None:
    ca = await make_hcp(name="Dr. CA", state="California")  # limit 2
    resp = await _create(client, hcp_id=ca["id"], samples_dropped=[{"product": "DrugX", "qty": 3}])
    assert resp.status_code == 201
    body = resp.json()
    assert body["compliance_flag"] is True
    assert "DrugX: 3 units exceeds the 2-unit limit for California" in body["compliance_notes"]


async def test_create_unknown_hcp_404(client: httpx.AsyncClient) -> None:
    resp = await _create(client, hcp_id=str(uuid.uuid4()))
    assert resp.status_code == 404
    assert resp.json()["detail"] == "HCP not found"


async def test_create_requires_hcp_id(client: httpx.AsyncClient, hcp: dict) -> None:
    resp = await client.post("/api/interactions", json={"interaction_type": "Visit"})
    assert resp.status_code == 422


async def test_list_filters_by_hcp(client: httpx.AsyncClient, make_hcp) -> None:
    a = await make_hcp(name="Dr. A")
    b = await make_hcp(name="Dr. B")
    await _create(client, hcp_id=a["id"], interaction_date="2026-01-01")
    await _create(client, hcp_id=a["id"], interaction_date="2026-03-01")
    await _create(client, hcp_id=b["id"])

    resp = await client.get("/api/interactions", params={"hcp_id": a["id"]})
    assert resp.status_code == 200
    rows = resp.json()
    assert {r["hcp_id"] for r in rows} == {a["id"]}
    assert [r["interaction_date"] for r in rows] == ["2026-03-01", "2026-01-01"]  # newest first

    assert len((await client.get("/api/interactions")).json()) == 3


# ------------------------------------------------------------------- get ----
async def test_get_unknown_interaction_404(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"/api/interactions/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "Interaction not found"


# ----------------------------------------------------------------- patch ----
async def test_patch_updates_fields_and_records_audit_trail(client: httpx.AsyncClient, hcp: dict) -> None:
    created = (await _create(client, hcp_id=hcp["id"], products_discussed=["A"])).json()

    resp = await client.patch(
        f"/api/interactions/{created['id']}",
        json={"products_discussed": ["A", "B"], "hcp_sentiment": "Positive", "edit_reason": "typo"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["products_discussed"] == ["A", "B"]
    assert body["hcp_sentiment"] == "Positive"

    assert len(body["edit_history"]) == 1
    entry = body["edit_history"][0]
    assert entry["reason"] == "typo"
    assert entry["before"] == {"products_discussed": ["A"], "hcp_sentiment": None}
    assert entry["after"] == {"products_discussed": ["A", "B"], "hcp_sentiment": "Positive"}

    # Persisted, not just echoed.
    again = (await client.get(f"/api/interactions/{created['id']}")).json()
    assert again["products_discussed"] == ["A", "B"]
    assert len(again["edit_history"]) == 1


async def test_patch_omitted_fields_are_untouched(client: httpx.AsyncClient, hcp: dict) -> None:
    created = (await _create(client, hcp_id=hcp["id"], key_topics=["pricing"], raw_notes="orig")).json()
    body = (await client.patch(f"/api/interactions/{created['id']}", json={"raw_notes": "new"})).json()
    assert body["raw_notes"] == "new"
    assert body["key_topics"] == ["pricing"]
    assert body["edit_history"][0]["before"] == {"raw_notes": "orig"}


async def test_patch_twice_appends_history(client: httpx.AsyncClient, hcp: dict) -> None:
    created = (await _create(client, hcp_id=hcp["id"])).json()
    await client.patch(f"/api/interactions/{created['id']}", json={"interaction_type": "Call"})
    body = (await client.patch(f"/api/interactions/{created['id']}", json={"interaction_type": "Email"})).json()
    assert body["interaction_type"] == "Email"
    assert [e["after"] for e in body["edit_history"]] == [{"interaction_type": "Call"}, {"interaction_type": "Email"}]


async def test_patch_unknown_interaction_404(client: httpx.AsyncClient) -> None:
    resp = await client.patch(f"/api/interactions/{uuid.uuid4()}", json={"raw_notes": "x"})
    assert resp.status_code == 404


# --------------------------------------- sample validation (was BUG-1) ----
async def test_create_with_string_qty_does_not_500(client: httpx.AsyncClient, hcp: dict) -> None:
    """Integer-valued strings are coerced (same rule the LLM path uses), not a 500."""
    resp = await _create(client, hcp_id=hcp["id"], samples_dropped=[{"product": "X", "qty": "3"}])
    assert resp.status_code == 201, resp.text
    assert resp.json()["samples_dropped"] == [{"product": "X", "qty": 3}]


async def test_create_with_null_qty_does_not_500(client: httpx.AsyncClient, hcp: dict) -> None:
    resp = await _create(client, hcp_id=hcp["id"], samples_dropped=[{"product": "X", "qty": None}])
    assert resp.status_code == 422, resp.text


BAD_SAMPLES = [
    pytest.param({"product": "X", "qty": "abc"}, id="non-numeric-qty"),
    pytest.param({"product": "X", "qty": -1}, id="negative-qty"),
    pytest.param({"product": "X", "qty": 2.5}, id="fractional-qty"),
    pytest.param({"product": "X", "qty": True}, id="bool-qty"),
    pytest.param({"product": "X"}, id="missing-qty"),
    pytest.param({"product": "   ", "qty": 1}, id="blank-product"),
    pytest.param({"qty": 1}, id="missing-product"),
]


@pytest.mark.parametrize("sample", BAD_SAMPLES)
async def test_create_rejects_bad_sample_with_422_and_saves_nothing(
    client: httpx.AsyncClient, hcp: dict, sample: dict
) -> None:
    resp = await _create(client, hcp_id=hcp["id"], samples_dropped=[sample])
    assert resp.status_code == 422, resp.text
    assert (await client.get("/api/interactions")).json() == []


@pytest.mark.parametrize("sample", BAD_SAMPLES)
async def test_patch_rejects_bad_sample_with_422(client: httpx.AsyncClient, hcp: dict, sample: dict) -> None:
    created = (await _create(client, hcp_id=hcp["id"], samples_dropped=[{"product": "A", "qty": 1}])).json()
    resp = await client.patch(f"/api/interactions/{created['id']}", json={"samples_dropped": [sample]})
    assert resp.status_code == 422, resp.text
    again = (await client.get(f"/api/interactions/{created['id']}")).json()
    assert again["samples_dropped"] == [{"product": "A", "qty": 1}]
    assert again["edit_history"] == []


async def test_create_strips_product_whitespace(client: httpx.AsyncClient, hcp: dict) -> None:
    resp = await _create(client, hcp_id=hcp["id"], samples_dropped=[{"product": "  CardioMax ", "qty": 0}])
    assert resp.status_code == 201
    assert resp.json()["samples_dropped"] == [{"product": "CardioMax", "qty": 0}]


# ------------------------------------------- create is one transaction ----
async def test_failed_compliance_on_create_leaves_no_row(
    client: httpx.AsyncClient, hcp: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_a: Any, **_kw: Any) -> None:
        raise RuntimeError("compliance service exploded")

    monkeypatch.setattr("app.routers.interactions.apply_compliance", _boom)
    with pytest.raises(RuntimeError, match="compliance service exploded"):
        await _create(client, hcp_id=hcp["id"], samples_dropped=[{"product": "A", "qty": 1}])

    monkeypatch.undo()
    assert (await client.get("/api/interactions")).json() == []


# -------------------------------------- PATCH re-runs compliance (BUG-2) ----
async def test_patch_samples_reruns_compliance(client: httpx.AsyncClient, make_hcp) -> None:
    vt = await make_hcp(name="Dr. VT", state="Vermont")  # limit 0
    created = (await _create(client, hcp_id=vt["id"])).json()
    assert created["compliance_flag"] is False

    body = (
        await client.patch(
            f"/api/interactions/{created['id']}",
            json={"samples_dropped": [{"product": "DrugX", "qty": 5}]},
        )
    ).json()
    assert body["samples_dropped"] == [{"product": "DrugX", "qty": 5}]
    assert body["compliance_flag"] is True
    assert body["compliance_notes"] == "DrugX: 5 units exceeds the 0-unit limit for Vermont"


async def test_patch_samples_can_clear_a_flag(client: httpx.AsyncClient, make_hcp) -> None:
    ca = await make_hcp(name="Dr. CA", state="California")  # limit 2
    created = (await _create(client, hcp_id=ca["id"], samples_dropped=[{"product": "A", "qty": 5}])).json()
    assert created["compliance_flag"] is True

    body = (await client.patch(f"/api/interactions/{created['id']}", json={"samples_dropped": [{"product": "A", "qty": 2}]})).json()
    assert body["compliance_flag"] is False
    assert body["compliance_notes"] == "No compliance issues detected."

    body = (await client.patch(f"/api/interactions/{created['id']}", json={"samples_dropped": []})).json()
    assert body["samples_dropped"] == []
    assert body["compliance_flag"] is False
    assert body["compliance_notes"] is None  # same as a sample-free create


async def test_patch_without_samples_leaves_compliance_alone(client: httpx.AsyncClient, make_hcp) -> None:
    ca = await make_hcp(name="Dr. CA", state="California")
    created = (await _create(client, hcp_id=ca["id"], samples_dropped=[{"product": "A", "qty": 5}])).json()
    body = (await client.patch(f"/api/interactions/{created['id']}", json={"raw_notes": "x"})).json()
    assert body["compliance_flag"] is True
    assert body["compliance_notes"] == created["compliance_notes"]
