"""Admin-only endpoints: approve materials, manage products, manage reps (app users: rep, manager or admin role).
The CLI (python -m app.admin) remains for bootstrapping and scripting."""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools import apply_compliance
from app.authz import Authz, require_role
from app.config import settings
from app.catalog import clean_names
from app.database import get_db
from app.models import HCP, Interaction, Material, Product, User, gen_uuid, utcnow
from app.schemas import (
    MaterialApproval,
    MaterialOut,
    ProductCreate,
    ProductOut,
    UserCreate,
    UserOut,
    UserUpdate,
)
from app.security import hash_password

router = APIRouter(prefix="/api/admin", tags=["Admin"])
admin_only = require_role("admin")


# ------------------------------------------------------------------ materials
@router.get("/materials/pending", response_model=list[MaterialOut])
async def pending_materials(_: Authz = Depends(admin_only), db: AsyncSession = Depends(get_db)):
    stmt = select(Material).where(Material.approved.is_(False)).order_by(func.lower(Material.name))
    return (await db.execute(stmt)).scalars().all()


@router.post("/materials/{material_id}/approve", response_model=MaterialApproval)
async def approve_material(material_id: str, _: Authz = Depends(admin_only), db: AsyncSession = Depends(get_db)):
    """Approve a material and re-run compliance on every interaction that used it,
    so "Materials pending approval" flags it caused are cleared."""
    material = await db.get(Material, material_id)
    if material is None:
        raise HTTPException(404, "Material not found")
    material.approved = True
    await db.flush()
    rows = (
        await db.execute(
            select(Interaction).where(
                text(
                    "EXISTS (SELECT 1 FROM json_array_elements_text(interactions.materials_shared) AS m "
                    "WHERE lower(m) = lower(:name))"
                ).bindparams(name=material.name)
            )
        )
    ).scalars().all()
    cleared = 0
    for interaction in rows:
        was_flagged = interaction.compliance_flag
        hcp = await db.get(HCP, interaction.hcp_id)
        assert hcp is not None  # FK
        await apply_compliance(db, interaction, hcp)
        cleared += int(was_flagged and not interaction.compliance_flag)
    await db.commit()
    await db.refresh(material)
    return MaterialApproval(
        material=MaterialOut.model_validate(material), interactions_rechecked=len(rows), flags_cleared=cleared
    )


# ------------------------------------------------------------------ products
@router.post("/products", response_model=ProductOut, status_code=201)
async def add_product(payload: ProductCreate, _: Authz = Depends(admin_only), db: AsyncSession = Depends(get_db)):
    name = clean_names([payload.name])[0]
    result = await db.execute(
        pg_insert(Product).values(id=gen_uuid(), name=name, created_at=utcnow()).on_conflict_do_nothing()
    )
    if not result.rowcount:  # type: ignore[attr-defined]  # CursorResult has rowcount; Result stub lacks it
        raise HTTPException(409, f"Product {name!r} already exists")
    await db.commit()
    return (await db.execute(select(Product).where(Product.name == name))).scalar_one()


@router.patch("/products/{product_id}", response_model=ProductOut)
async def rename_product(
    product_id: str, payload: ProductCreate, _: Authz = Depends(admin_only), db: AsyncSession = Depends(get_db)
):
    """Rename a catalog product. Existing interactions keep the name they were saved with."""
    product = await db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    name = clean_names([payload.name])[0]
    clash = (
        await db.execute(select(Product).where(func.lower(Product.name) == name.lower(), Product.id != product_id))
    ).scalar_one_or_none()
    if clash:
        raise HTTPException(409, f"Product {name!r} already exists")
    product.name = name
    await db.commit()
    await db.refresh(product)
    return product


@router.delete("/products/{product_id}", status_code=204)
async def delete_product(product_id: str, _: Authz = Depends(admin_only), db: AsyncSession = Depends(get_db)):
    """Remove a product from the catalog (it can no longer be picked). Saved interactions are unchanged."""
    product = await db.get(Product, product_id)
    if product is None:
        raise HTTPException(404, "Product not found")
    await db.delete(product)
    await db.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------ users
DEMO_READ_ONLY = "User management is read-only in demo mode (the demo accounts can't be changed)."


def _users_writable() -> None:
    """In DEMO_MODE nobody can add users or change role, active flag or password,
    so the shared demo accounts stay usable for every visitor."""
    if settings.demo_mode:
        raise HTTPException(403, DEMO_READ_ONLY)

@router.get("/reps", response_model=list[UserOut])
async def list_users(_: Authz = Depends(admin_only), db: AsyncSession = Depends(get_db)):
    return (await db.execute(select(User).order_by(func.lower(User.email)))).scalars().all()


@router.post("/reps", response_model=UserOut, status_code=201)
async def create_user(payload: UserCreate, _: Authz = Depends(admin_only), db: AsyncSession = Depends(get_db)):
    _users_writable()
    exists = (await db.execute(select(User.id).where(func.lower(User.email) == payload.email.lower()))).first()
    if exists:
        raise HTTPException(409, "A rep with this email already exists")
    user = User(email=payload.email, password_hash=hash_password(payload.password), role=payload.role)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _active_admins(db: AsyncSession) -> int:
    stmt = select(func.count()).select_from(User).where(User.role == "admin", User.is_active.is_(True))
    return int((await db.execute(stmt)).scalar_one())


@router.patch("/reps/{user_id}", response_model=UserOut)
async def update_user(
    user_id: str, payload: UserUpdate, _: Authz = Depends(admin_only), db: AsyncSession = Depends(get_db)
):
    """Change role, (de)activate, or reset password. The last active admin can't be demoted or deactivated."""
    _users_writable()
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(404, "Rep not found")
    losing_admin = user.role == "admin" and user.is_active and (
        (payload.role is not None and payload.role != "admin") or payload.is_active is False
    )
    if losing_admin and await _active_admins(db) <= 1:
        raise HTTPException(409, "Can't demote or deactivate the last active admin")
    if payload.role is not None:
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.password is not None:
        user.password_hash = hash_password(payload.password)
    await db.commit()
    await db.refresh(user)
    return user
