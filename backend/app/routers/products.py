from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.catalog import resolve_products
from app.database import get_db
from app.models import Product
from app.schemas import ProductCreate, ProductOut

router = APIRouter(prefix="/api/products", tags=["Products"])


@router.get("", response_model=list[ProductOut])
async def search_products(
    q: str = Query("", max_length=200),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """Product catalog search (case-insensitive substring), alphabetical."""
    stmt = select(Product).order_by(func.lower(Product.name)).limit(limit)
    if q.strip():
        stmt = stmt.where(Product.name.ilike(f"%{q.strip()}%"))
    return (await db.execute(stmt)).scalars().all()


@router.post("", response_model=ProductOut, status_code=201)
async def add_product(payload: ProductCreate, response: Response, db: AsyncSession = Depends(get_db)):
    """Add a product to the catalog; an existing name (case-insensitive) is returned with 200."""
    existing = (
        await db.execute(select(Product).where(func.lower(Product.name) == payload.name.lower()))
    ).scalar_one_or_none()
    if existing:
        response.status_code = 200
        return existing
    [name] = await resolve_products(db, [payload.name])
    await db.commit()
    return (await db.execute(select(Product).where(Product.name == name))).scalar_one()
