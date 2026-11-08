"""Materials approval: starter/migrated materials are approved, rep-added ones are
"Pending approval" and flagged by the compliance check on every save path."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools import build_tools
from app.catalog import SEED_MATERIALS, seed_materials
from app.models import HCP, Interaction, Material
from tests.conftest import ADMIN_AUTHZ, test_engine


async def _call(db: AsyncSession, name: str, **kwargs: Any) -> dict:
    tool = next(t for t in build_tools(db, ADMIN_AUTHZ) if t.name == name)
    return json.loads(await tool.coroutine(**kwargs))  # type: ignore[misc]  # StructuredTool.coroutine is Optional; always set by build_tools


# ------------------------------------------------------------------ catalog
async def test_seeded_materials_are_approved(db: AsyncSession) -> None:
    await seed_materials(db)
    rows = (await db.execute(select(Material))).scalars().all()
    assert len(rows) == len(SEED_MATERIALS) and all(m.approved for m in rows)


async def test_rep_added_material_is_pending(client: httpx.AsyncClient) -> None:
    body = (await client.post("/api/materials", json={"name": "My own slide deck"})).json()
    assert body["approved"] is False


async def test_adding_an_existing_approved_material_keeps_it_approved(client: httpx.AsyncClient, db: AsyncSession) -> None:
    await seed_materials(db)
    resp = await client.post("/api/materials", json={"name": "cardiomax DOSING guide"})
    assert resp.status_code == 200 and resp.json() == {**resp.json(), "approved": True}


async def test_seeding_approves_a_starter_name_a_rep_added_first(client: httpx.AsyncClient, db: AsyncSession) -> None:
    await client.post("/api/materials", json={"name": "formulary coverage SHEET"})
    await seed_materials(db)
    rows = (await db.execute(select(Material).where(Material.name.ilike("formulary coverage sheet")))).scalars().all()
    assert len(rows) == 1 and rows[0].approved is True


async def test_search_filter_by_approval(client: httpx.AsyncClient, db: AsyncSession) -> None:
    await seed_materials(db)
    await client.post("/api/materials", json={"name": "Zeta rep handout"})
    pending = (await client.get("/api/materials", params={"approved": "false"})).json()
    assert [m["name"] for m in pending] == ["Zeta rep handout"]
    approved = (await client.get("/api/materials", params={"approved": "true", "limit": 100})).json()
    assert len(approved) == len(SEED_MATERIALS)


# --------------------------------------------------------------- compliance
async def test_form_create_with_pending_material_is_flagged(client: httpx.AsyncClient, hcp: dict, db: AsyncSession) -> None:
    await seed_materials(db)
    body = (
        await client.post(
            "/api/interactions",
            json={"hcp_id": hcp["id"], "materials_shared": ["CardioMax dosing guide", "Homemade comparison chart"]},
        )
    ).json()
    assert body["compliance_flag"] is True
    assert body["compliance_notes"] == "Materials pending approval: Homemade comparison chart"


async def test_form_create_with_only_approved_materials_is_clean(client: httpx.AsyncClient, hcp: dict, db: AsyncSession) -> None:
    await seed_materials(db)
    body = (await client.post("/api/interactions", json={"hcp_id": hcp["id"], "materials_shared": ["CardioMax dosing guide"]})).json()
    assert (body["compliance_flag"], body["compliance_notes"]) == (False, None)


async def test_sample_and_material_issues_are_combined(client: httpx.AsyncClient, make_hcp) -> None:
    ca = await make_hcp(name="Dr. CA", state="California")  # sample limit 2
    body = (
        await client.post(
            "/api/interactions",
            json={"hcp_id": ca["id"], "samples_dropped": [{"product": "X", "qty": 3}], "materials_shared": ["Rep flyer"]},
        )
    ).json()
    assert body["compliance_flag"] is True
    assert body["compliance_notes"] == (
        "X: 3 units exceeds the 2-unit limit for California; Materials pending approval: Rep flyer"
    )


async def test_patch_adding_pending_material_flags_and_removing_clears(
    client: httpx.AsyncClient, hcp: dict, db: AsyncSession
) -> None:
    await seed_materials(db)
    created = (await client.post("/api/interactions", json={"hcp_id": hcp["id"]})).json()
    flagged = (await client.patch(f"/api/interactions/{created['id']}", json={"materials_shared": ["Rep flyer"]})).json()
    assert (flagged["compliance_flag"], flagged["compliance_notes"]) == (True, "Materials pending approval: Rep flyer")
    cleared = (
        await client.patch(f"/api/interactions/{created['id']}", json={"materials_shared": ["Formulary coverage sheet"]})
    ).json()
    assert (cleared["compliance_flag"], cleared["compliance_notes"]) == (False, None)


async def test_chat_log_with_new_material_is_flagged(db: AsyncSession, fake_llm) -> None:
    hcp = HCP(name="Dr. Chat")
    db.add(hcp)
    await db.commit()
    await seed_materials(db)
    fake_llm(json.dumps({"summary": "s", "materials_shared": ["Formulary coverage sheet", "Napkin sketch"]}))
    out = await _call(db, "log_interaction", hcp_id=hcp.id, raw_text="notes")
    assert out["compliance_flag"] is True
    assert out["compliance_notes"] == "Materials pending approval: Napkin sketch"
    row = await db.get(Interaction, out["interaction_id"])
    assert row is not None and row.compliance_flag is True


async def test_approving_later_does_not_rewrite_old_flags(client: httpx.AsyncClient, hcp: dict, db: AsyncSession) -> None:
    """Documented limitation: flags are computed at save time."""
    created = (await client.post("/api/interactions", json={"hcp_id": hcp["id"], "materials_shared": ["Rep flyer"]})).json()
    async with test_engine.begin() as conn:
        await conn.exec_driver_sql("UPDATE materials SET approved = true WHERE name = 'Rep flyer'")
    again = (await client.get(f"/api/interactions/{created['id']}")).json()
    assert again["compliance_flag"] is True


# ------------------------------------------------------------- migration 004
MIGRATION_004 = Path(__file__).resolve().parents[1] / "migrations" / "004_materials_approval.sql"


async def test_migration_004_approves_existing_only_once(_schema: None) -> None:
    sql = MIGRATION_004.read_text(encoding="utf-8")
    async with test_engine.connect() as conn:
        await conn.exec_driver_sql("DROP SCHEMA IF EXISTS legacy_004 CASCADE")
        await conn.exec_driver_sql("CREATE SCHEMA legacy_004")
        await conn.exec_driver_sql("SET search_path TO legacy_004")
        # Shape after migration 003: no approved column, nullable columns.
        await conn.exec_driver_sql("CREATE TABLE materials (id varchar(36) primary key, name varchar(200) not null, created_at timestamptz)")
        await conn.exec_driver_sql("CREATE TABLE interactions (id varchar(36) primary key, attendees json)")
        await conn.exec_driver_sql("INSERT INTO materials VALUES ('m1', 'Seeded thing', now()), ('m2', 'Migrated tag', NULL)")
        await conn.exec_driver_sql("INSERT INTO interactions VALUES ('i1', NULL), ('i2', '[\"A\"]')")
        await conn.commit()

        raw = (await conn.get_raw_connection()).driver_connection
        assert raw is not None
        await raw.execute(sql)
        rows = sorted((str(r[0]), bool(r[1])) for r in (await conn.exec_driver_sql("SELECT id, approved FROM materials")).all())
        assert rows == [("m1", True), ("m2", True)]

        # A rep adds a material after the migration; re-running must not approve it.
        await conn.exec_driver_sql("INSERT INTO materials (id, name, created_at) VALUES ('m3', 'Rep added', now())")
        await conn.commit()
        await raw.execute(sql)
        approved = {str(r[0]): bool(r[1]) for r in (await conn.exec_driver_sql("SELECT id, approved FROM materials")).all()}
        assert approved == {"m1": True, "m2": True, "m3": False}

        nulls = (await conn.exec_driver_sql("SELECT count(*) FROM interactions WHERE attendees IS NULL")).scalar_one()
        assert nulls == 0
        result = await conn.exec_driver_sql(
            "SELECT table_name || '.' || column_name, is_nullable FROM information_schema.columns "
            "WHERE table_schema = 'legacy_004' AND column_name IN ('attendees', 'created_at')"
        )
        not_null = {str(r[0]): str(r[1]) for r in result.all()}
        assert not_null == {"interactions.attendees": "NO", "materials.created_at": "NO"}
        await conn.exec_driver_sql("SET search_path TO public")
        await conn.exec_driver_sql("DROP SCHEMA legacy_004 CASCADE")
        await conn.commit()
