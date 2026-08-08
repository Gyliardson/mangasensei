"""Async SQLAlchemy engine construction."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_database(database_url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=5,
    )
    return engine, async_sessionmaker(engine, expire_on_commit=False)
