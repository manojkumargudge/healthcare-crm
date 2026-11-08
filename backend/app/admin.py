"""Admin commands (the product catalog is admin-only; there is no public write API).

    python -m app.admin list-products
    python -m app.admin add-product "NeuroCalm"      # idempotent, case-insensitive
    python -m app.admin seed-products                # insert the starter list

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


async def _main(argv: list[str]) -> int:
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "add-product" and len(argv) == 3:
        name, created = await add_product(argv[2])
        print(f"{'added' if created else 'already in catalog'}: {name}")
    elif cmd == "list-products":
        print("\n".join(await list_products()))
    elif cmd == "seed-products":
        async with AsyncSessionLocal() as db:
            print(f"inserted {await seed_products(db)} starter products")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main(sys.argv)))
