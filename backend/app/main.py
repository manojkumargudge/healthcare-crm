from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db
from app.routers import hcp, interactions, chat

app = FastAPI(title="AI-First HCP CRM", version="1.0.0")

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


@app.on_event("startup")
async def on_startup():
    await init_db()


@app.get("/")
async def root():
    return {"status": "ok", "service": "AI-First HCP CRM API"}


@app.get("/health")
async def health():
    return {"status": "healthy"}
