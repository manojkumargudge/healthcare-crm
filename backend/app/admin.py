"""Admin commands (the product catalog is admin-only; there is no public write API).

    python -m app.admin list-products
    python -m app.admin add-product "NeuroCalm"      # idempotent, case-insensitive
    python -m app.admin seed-products                # insert the starter list
    python -m app.admin create-user EMAIL ROLE       # rep|manager|admin; password read from ADMIN_CLI_PASSWORD
    python -m app.admin list-users

Inside Docker: docker compose exec backend python -m app.admin add-product "NeuroCalm"
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.catalog import clean_names, seed_products
from app.database import AsyncSessionLocal
from app.models import Product, gen_uuid, utcnow


async def add_product(name: str) -> tuple[str, bool]:
    """Add a product; returns (catalog name, created)."""
    cleaned = clean_names([name])
    if not cleaned:
        raise SystemExit("product name must not be empty")
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            pg_insert(Product)
            .values(id=gen_uuid(), name=cleaned[0], created_at=utcnow())
            .on_conflict_do_nothing()
        )
        await db.commit()
        existing = (
            await db.execute(select(Product.name).where(func.lower(Product.name) == cleaned[0].lower()))
        ).scalar_one()
        return existing, bool(result.rowcount)  # type: ignore[attr-defined]  # CursorResult has rowcount; Result stub lacks it


async def list_products() -> list[str]:
    async with AsyncSessionLocal() as db:
        return list((await db.execute(select(Product.name).order_by(func.lower(Product.name)))).scalars().all())


async def create_user(email: str, role: str, password: str) -> str:
    from app.security import hash_password
    from app.models import User

    if role not in ("rep", "manager", "admin"):
        raise SystemExit("role must be rep, manager or admin")
    if len(password) < 12:
        raise SystemExit("password must be at least 12 characters")
    async with AsyncSessionLocal() as db:
        if (await db.execute(select(User.id).where(func.lower(User.email) == email.lower()))).first():
            raise SystemExit(f"user {email} already exists")
        user = User(email=email, password_hash=hash_password(password), role=role)
        db.add(user)
        await db.commit()
        return user.id


async def list_users() -> list[str]:
    from app.models import User

    async with AsyncSessionLocal() as db:
        rows = (await db.execute(select(User).order_by(func.lower(User.email)))).scalars().all()
        return [f"{u.email}\t{u.role}\t{'active' if u.is_active else 'inactive'}" for u in rows]


async def _main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "add-product" and len(argv) == 3:
        name, created = await add_product(argv[2])
        print(f"{'added' if created else 'already in catalog'}: {name}")
    elif cmd == "list-products":
        print("\n".join(await list_products()))
    elif cmd == "create-user" and len(argv) == 4:
        import os

        password = os.environ.get("ADMIN_CLI_PASSWORD", "")
        print(f"created {argv[2]} ({argv[3]}): {await create_user(argv[2], argv[3], password)}")
    elif cmd == "list-users":
        print("\n".join(await list_users()))
    elif cmd == "seed-products":
        async with AsyncSessionLocal() as db:
            print(f"inserted {await seed_products(db)} starter products")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv)))
