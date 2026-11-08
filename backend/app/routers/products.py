from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.authz import get_authz
from app.database import get_db
from app.models import Product
from app.schemas import ProductOut

router = APIRouter(prefix="/api/products", tags=["Products"], dependencies=[Depends(get_authz)])


@router.get("", response_model=list[ProductOut])
async def search_products(
    q: str = Query("", max_length=200),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Product catalog search (case-insensitive substring), alphabetical.

    Read-only here; admins manage it via /api/admin/products or `python -m app.admin`.
    """
    stmt = select(Product).order_by(func.lower(Product.name)).limit(limit)
    if q.strip():
        stmt = stmt.where(Product.name.ilike(f"%{q.strip()}%"))
    return (await db.execute(stmt)).scalars().all()
