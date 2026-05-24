"""
E-Huzanya Gateway — Database Session
=====================================

Async SQLAlchemy session factory for SQLite.
Swap DATABASE_URL in config.py to switch to PostgreSQL.
"""

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from app.core.config import DATABASE_URL
from app.db.models import Base


engine = create_async_engine(
    DATABASE_URL,
    echo=False,  # Set True for SQL query logging during debug
    connect_args={"check_same_thread": False},  # Required for SQLite
)

async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def init_db():
    """Create all tables. Called once at application startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncSession:
    """Dependency injection for FastAPI route handlers."""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
