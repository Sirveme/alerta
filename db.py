"""
db.py — alerta.pe   (C:\\alertape\\db.py)
═══════════════════════════════════════════════════════════════════════
Conexión async a PostgreSQL (Railway) + session + creación de tablas.

ENV:
    DATABASE_URL = postgresql+asyncpg://user:pass@host:port/dbname
    (Railway da postgresql://...; reemplazar por postgresql+asyncpg://)

Uso:
    from db import get_session, crear_tablas
    await crear_tablas()                  # una vez (o usar migraciones)
    async with get_session() as session:
        ...
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    create_async_engine, async_sessionmaker, AsyncSession,
)

from models import Base

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _url() -> str:
    url = os.getenv("DATABASE_URL", "")
    if not url:
        raise RuntimeError(
            "DATABASE_URL no definida. Formato: "
            "postgresql+asyncpg://user:pass@host:port/dbname")
    # Railway entrega postgresql://; asyncpg necesita postgresql+asyncpg://
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


engine = create_async_engine(
    _url(),
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,   # evita conexiones muertas (Railway las cierra)
)

SessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False,
)


@asynccontextmanager
async def get_session():
    """Context manager de sesión async. Hace rollback en error."""
    async with SessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def crear_tablas() -> None:
    """Crea todas las tablas (si no existen). Usar una vez o en arranque."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def borrar_tablas() -> None:
    """DROP de todas las tablas. ¡Solo para desarrollo!"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


if __name__ == "__main__":
    import asyncio
    asyncio.run(crear_tablas())
    print("✓ Tablas creadas en la BD.")