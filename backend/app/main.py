from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.schemas import AppMeta
from app.agent.llm import close_groq_client, init_groq_client
from app.catalog import ensure_seed_admin, seed_materials, seed_products
from app.database import AsyncSessionLocal, engine, init_db
from app.routers import admin, auth, chat, follow_ups, hcp, interactions, materials, products, voice_notes


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    if settings.run_migrations_on_startup:
        await init_db()  # applies backend/migrations/*.sql (idempotent, serialized by an advisory lock)
    async with AsyncSessionLocal() as session:
        await ensure_seed_admin(session)
        await seed_materials(session)
        await seed_products(session)
    init_groq_client()
    try:
        yield
    finally:
        await close_groq_client()
        await engine.dispose()


app = FastAPI(title="AI-First HCP CRM", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,  # FRONTEND_ORIGIN + CORS_ORIGINS
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(hcp.router)
app.include_router(interactions.router)
app.include_router(chat.router)
app.include_router(materials.router)
app.include_router(follow_ups.router)
app.include_router(products.router)
app.include_router(voice_notes.router)


@app.get("/api/meta", response_model=AppMeta)
async def meta() -> AppMeta:
    """Public (no sign-in): what the UI needs before login, e.g. to show the demo banner."""
    return AppMeta(demo_mode=settings.demo_mode, llm_daily_cap=settings.llm_daily_cap)


@app.get("/")
async def root():
    return {"status": "ok", "service": "AI-First HCP CRM API"}


# Liveness only (no database query), so uptime pings keep the web service awake
# without waking a suspended database. HEAD is for monitors that ping with HEAD.
@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {"status": "healthy"}
