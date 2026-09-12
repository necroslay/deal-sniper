"""
SQLAlchemy 2.0 async session management for the valuation engine.
"""

from __future__ import annotations

import os
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Default: local PostgreSQL. Override via env var.
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/resale_engine",
)

_engine = create_async_engine(DATABASE_URL, echo=False, pool_pre_ping=True)
_session_factory = async_sessionmaker(_engine, class_=AsyncSession, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency — yields an async DB session."""
    async with _session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


def create_session() -> AsyncSession:
    """Create a standalone session (for scripts/Celery tasks)."""
    return _session_factory()


async def init_db() -> None:
    """Run raw SQL migrations to bootstrap the schema."""
    from pathlib import Path

    sql_path = Path(__file__).resolve().parent / "migrations.sql"
    if not sql_path.exists():
        raise FileNotFoundError(f"Migration file not found: {sql_path}")

    sql = sql_path.read_text(encoding="utf-8")
    async with _engine.begin() as conn:
        await conn.execute("COMMIT")  # close any open txn for CREATE DATABASE
        await conn.run_sync(lambda sync_conn: sync_conn.exec_driver_sql(sql))

    import logging
    logging.getLogger(__name__).info("Database schema initialised.")