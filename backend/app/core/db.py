"""Асинхронный доступ к PostgreSQL."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=settings.db_echo,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_pool_max_overflow,
    pool_pre_ping=True,
    pool_recycle=1800,
)

SessionFactory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

# Воркеры берут соединение на задачу и держат недолго -> пул не нужен
worker_engine = create_async_engine(settings.database_url, echo=settings.db_echo, poolclass=NullPool)
WorkerSessionFactory = async_sessionmaker(worker_engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI-зависимость: одна сессия на запрос."""
    async with SessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


@contextlib.asynccontextmanager
async def worker_session() -> AsyncIterator[AsyncSession]:
    """Сессия для фоновых задач."""
    async with WorkerSessionFactory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_database() -> tuple[bool, str]:
    from sqlalchemy import text

    try:
        async with engine.connect() as conn:
            version = (await conn.execute(text("select version()"))).scalar_one()
        return True, str(version).split(",")[0]
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
