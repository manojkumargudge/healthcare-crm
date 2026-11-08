from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import get_authz
from app.database import get_db
from app.models import HCP
from app.schemas import HCPCreate, HCPOut

# HCPs (doctors) are shared company data: every signed-in rep can read and add them.
router = APIRouter(prefix="/api/hcps", tags=["HCPs"], dependencies=[Depends(get_authz)])


@router.get("", response_model=list[HCPOut])
async def list_hcps(q: str = Query("", max_length=200), db: AsyncSession = Depends(get_db)):
    """All HCPs by name; with `q`, a case-insensitive search over name, specialty and institution."""
    stmt = select(HCP).order_by(HCP.name)
    if q.strip():
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(HCP.name.ilike(like), HCP.specialty.ilike(like), HCP.institution.ilike(like)))
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("", response_model=HCPOut, status_code=201)
async def create_hcp(payload: HCPCreate, db: AsyncSession = Depends(get_db)):
    hcp = HCP(**payload.model_dump())
    db.add(hcp)
    await db.commit()
    await db.refresh(hcp)
    return hcp


@router.get("/{hcp_id}", response_model=HCPOut)
async def get_hcp(hcp_id: str, db: AsyncSession = Depends(get_db)):
    hcp = await db.get(HCP, hcp_id)
    if not hcp:
        raise HTTPException(404, "HCP not found")
    return hcp
