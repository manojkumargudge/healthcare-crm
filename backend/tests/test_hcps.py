"""HCP endpoints. The API currently exposes create / list / get only (no update or delete)."""

import uuid

import httpx


async def test_create_hcp_returns_201_with_defaults(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/hcps", json={"name": "Dr. Anjali Rao"})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["name"] == "Dr. Anjali Rao"
    assert body["tier"] == "B"
    assert body["specialty"] is None
    uuid.UUID(body["id"])  # server-generated UUID
    assert body["created_at"]


async def test_create_hcp_persists_all_fields(client: httpx.AsyncClient) -> None:
    payload = {
        "name": "Dr. Emily Chen",
        "specialty": "Oncologist",
        "institution": "Stanford Health",
        "email": "emily@example.org",
        "phone": "+1-555-0100",
        "state": "Vermont",
        "tier": "A",
    }
    created = (await client.post("/api/hcps", json=payload)).json()

    fetched = await client.get(f"/api/hcps/{created['id']}")
    assert fetched.status_code == 200
    for key, value in payload.items():
        assert fetched.json()[key] == value


async def test_create_hcp_requires_name(client: httpx.AsyncClient) -> None:
    resp = await client.post("/api/hcps", json={"specialty": "Cardiology"})
    assert resp.status_code == 422


async def test_list_hcps_sorted_by_name(client: httpx.AsyncClient) -> None:
    for name in ["Dr. Zed", "Dr. Alpha", "Dr. Mid"]:
        await client.post("/api/hcps", json={"name": name})

    resp = await client.get("/api/hcps")
    assert resp.status_code == 200
    assert [h["name"] for h in resp.json()] == ["Dr. Alpha", "Dr. Mid", "Dr. Zed"]


async def test_list_hcps_empty(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/hcps")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_get_unknown_hcp_404(client: httpx.AsyncClient) -> None:
    resp = await client.get(f"/api/hcps/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == "HCP not found"
