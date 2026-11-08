from typing import Optional

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import resolve_materials
from app.authz import get_authz
from app.database import get_db
from app.models import Material
from app.schemas import MaterialCreate, MaterialOut

# Any signed-in rep can search and propose materials (pending until an admin approves).
router = APIRouter(prefix="/api/materials", tags=["Materials"], dependencies=[Depends(get_authz)])


@router.get("", response_model=list[MaterialOut])
async def search_materials(
    q: str = Query("", max_length=200),
    approved: Optional[bool] = Query(None, description="Filter by approval status"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Catalog search (case-insensitive substring); empty q lists the first `limit` alphabetically."""
    stmt = select(Material).order_by(func.lower(Material.name)).limit(limit)
    if q.strip():
        stmt = stmt.where(Material.name.ilike(f"%{q.strip()}%"))
    if approved is not None:
        stmt = stmt.where(Material.approved.is_(approved))
    return (await db.execute(stmt)).scalars().all()


@router.post("", response_model=MaterialOut, status_code=201)
async def add_material(payload: MaterialCreate, response: Response, db: AsyncSession = Depends(get_db)):
    """Add a material to the catalog as unapproved ("Pending approval").
    If it already exists (case-insensitive), returns it unchanged with 200."""
    existing = (
        await db.execute(select(Material).where(func.lower(Material.name) == payload.name.lower()))
    ).scalar_one_or_none()
    if existing:
        response.status_code = 200
        return existing
    [name] = await resolve_materials(db, [payload.name])
    await db.commit()
    return (await db.execute(select(Material).where(Material.name == name))).scalar_one()
