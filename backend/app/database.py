from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import settings
from app.db_url import split_ssl

_url, _connect_args = split_ssl(settings.database_url)  # sslmode=... -> asyncpg ssl argument
engine = create_async_engine(
    _url,
    connect_args=_connect_args,
    # Hosted Postgres (e.g. Neon's free tier) suspends idle computes and drops their
    # connections: test a pooled connection before use and recycle old ones.
    pool_pre_ping=True,
    pool_recycle=300,
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    """Bring the schema up to date by applying every migration (each one is idempotent)."""
    from app.db_migrations import apply_all  # imports this module, so import lazily

    await apply_all(settings.database_url)
