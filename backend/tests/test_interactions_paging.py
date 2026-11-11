"""Paged interaction history: limit/offset, X-Total-Count, owner filter, rep counts."""

from __future__ import annotations

import httpx

from tests.conftest import USERS


async def _log(c: httpx.AsyncClient, hcp_id: str, day: str) -> str:
    resp = await c.post("/api/interactions", json={"hcp_id": hcp_id, "interaction_date": day})
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def test_pages_are_newest_first_without_overlap(client: httpx.AsyncClient, hcp) -> None:
    # Several rows share a date, so the tie-break (created_at, id) must keep pages stable.
    days = ["2026-11-01", "2026-11-03", "2026-11-03", "2026-11-03", "2026-11-02", "2026-11-05", "2026-11-04"]
    for d in days:
        await _log(client, hcp["id"], d)

    seen: list[dict] = []
    for offset in (0, 3, 6):
        resp = await client.get("/api/interactions", params={"hcp_id": hcp["id"], "limit": 3, "offset": offset})
        assert resp.status_code == 200
        assert resp.headers["x-total-count"] == "7"
        seen += resp.json()
    assert len(seen) == 7 and len({i["id"] for i in seen}) == 7
    assert [i["interaction_date"] for i in seen] == sorted(days, reverse=True)
    past_end = await client.get("/api/interactions", params={"hcp_id": hcp["id"], "offset": 7})
    assert past_end.json() == [] and past_end.headers["x-total-count"] == "7"


async def test_default_and_max_page_size(client: httpx.AsyncClient, hcp) -> None:
    for _ in range(27):
        await _log(client, hcp["id"], "2026-11-01")
    resp = await client.get("/api/interactions", params={"hcp_id": hcp["id"]})
    assert len(resp.json()) == 25 and resp.headers["x-total-count"] == "27"
    for bad in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
        assert (await client.get("/api/interactions", params=bad)).status_code == 422


async def test_owner_filter_and_rep_counts(client_as, client: httpx.AsyncClient, hcp, make_hcp) -> None:
    a, b, m = await client_as("rep_a"), await client_as("rep_b"), await client_as("manager")
    other = await make_hcp(name="Dr. Elsewhere")
    a1 = await _log(a, hcp["id"], "2026-11-01")
    a2 = await _log(a, hcp["id"], "2026-11-02")
    await _log(b, hcp["id"], "2026-11-01")
    await _log(b, other["id"], "2026-11-01")

    resp = await m.get("/api/interactions", params={"hcp_id": hcp["id"], "owner_id": USERS["rep_a"].id})
    assert [i["id"] for i in resp.json()] == [a2, a1] and resp.headers["x-total-count"] == "2"

    reps = (await m.get("/api/interactions/reps", params={"hcp_id": hcp["id"]})).json()
    assert reps == [
        {"owner_id": USERS["rep_a"].id, "email": USERS["rep_a"].email, "count": 2},
        {"owner_id": USERS["rep_b"].id, "email": USERS["rep_b"].email, "count": 1},
    ]
    all_hcps = {r["email"]: r["count"] for r in (await m.get("/api/interactions/reps")).json()}
    assert all_hcps == {USERS["rep_a"].email: 2, USERS["rep_b"].email: 2}


async def test_rep_cannot_page_into_other_reps_rows(client_as, hcp) -> None:
    a, b = await client_as("rep_a"), await client_as("rep_b")
    await _log(b, hcp["id"], "2026-11-01")
    mine = await _log(a, hcp["id"], "2026-11-01")

    resp = await a.get("/api/interactions", params={"hcp_id": hcp["id"], "owner_id": USERS["rep_b"].id})
    assert resp.json() == [] and resp.headers["x-total-count"] == "0"
    resp = await a.get("/api/interactions", params={"hcp_id": hcp["id"]})
    assert [i["id"] for i in resp.json()] == [mine] and resp.headers["x-total-count"] == "1"
    reps = (await a.get("/api/interactions/reps", params={"hcp_id": hcp["id"]})).json()
    assert reps == [{"owner_id": USERS["rep_a"].id, "email": USERS["rep_a"].email, "count": 1}]


async def test_reps_requires_sign_in(client_as) -> None:
    anon = await client_as(None)
    assert (await anon.get("/api/interactions/reps")).status_code == 401


async def test_total_count_header_is_exposed_to_the_browser(client: httpx.AsyncClient) -> None:
    from app.config import settings

    origin = settings.allowed_origins[0]
    resp = await client.get("/api/interactions", headers={"Origin": origin})
    assert "x-total-count" in resp.headers.get("access-control-expose-headers", "").lower()
