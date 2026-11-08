from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.agent.llm import close_groq_client, init_groq_client
from app.catalog import seed_materials, seed_products
from app.database import AsyncSessionLocal, engine, init_db
from app.routers import chat, follow_ups, hcp, interactions, materials, products, voice_notes



@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    async with AsyncSessionLocal() as session:
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
    allow_origins=[settings.frontend_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(hcp.router)
app.include_router(interactions.router)
app.include_router(chat.router)
app.include_router(materials.router)
app.include_router(follow_ups.router)
app.include_router(products.router)
app.include_router(voice_notes.router)


@app.get("/")
async def root():
    return {"status": "ok", "service": "AI-First HCP CRM API"}


@app.get("/health")
async def health():
    return {"status": "healthy"}
