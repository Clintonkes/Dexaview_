"""
app/db/session.py
-----------------
Async SQLAlchemy session factory for MySQL (via aiomysql).

Usage in route handlers:
    async def my_route(db: AsyncSession = Depends(get_db)):
        result = await db.execute(select(User))
        ...

The `create_tables` coroutine is called once at application startup (see
main.py lifespan) to create any tables that do not yet exist in the database.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from api.core.config import settings


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

# pool_pre_ping=True reconnects automatically if the MySQL server drops idle
# connections (common in cloud-hosted databases with short idle timeouts)
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,          # set True for SQL debug logging
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

# Session factory – use as a context manager in route handlers
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,  # prevents "DetachedInstanceError" after commit
)


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    """
    All ORM models inherit from this class so SQLAlchemy can discover them
    and create the corresponding tables automatically.
    """
    pass


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------

async def get_db():
    """
    FastAPI dependency that provides a single AsyncSession per HTTP request
    and automatically commits or rolls back on exit.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Startup helper
# ---------------------------------------------------------------------------

async def create_tables():
    """
    Creates all tables defined by ORM models that inherit from `Base`.
    Safe to call on every startup – SQLAlchemy uses CREATE TABLE IF NOT EXISTS.
    """
    # Import models here to ensure they are registered on Base.metadata
    import api.models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
