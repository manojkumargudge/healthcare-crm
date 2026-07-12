"""Run with: python -m app.seed
Populates a handful of demo HCPs so the app isn't empty for your walkthrough video."""
import asyncio
from app.database import AsyncSessionLocal, init_db
from app.models import HCP

DEMO_HCPS = [
    dict(name="Dr. Anjali Rao", specialty="Cardiologist", institution="Manipal Hospital", state="Karnataka", tier="A"),
    dict(name="Dr. Vivek Sharma", specialty="Endocrinologist", institution="Apollo Clinic", state="California", tier="B"),
    dict(name="Dr. Emily Chen", specialty="Oncologist", institution="Stanford Health", state="Vermont", tier="A"),
]


async def seed():
    await init_db()
    async with AsyncSessionLocal() as db:
        for data in DEMO_HCPS:
            db.add(HCP(**data))
        await db.commit()
    print(f"Seeded {len(DEMO_HCPS)} demo HCPs.")


if __name__ == "__main__":
    asyncio.run(seed())
