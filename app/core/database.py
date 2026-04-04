"""
core/database.py — Engine y SessionLocal canonicos para toda la app.

Cualquier script o modulo que necesite acceso a BD debe importar de aqui:
    from app.core.database import SessionLocal, engine
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings

# Railway usa postgres:// pero SQLAlchemy necesita postgresql://
_url = settings.DATABASE_URL_SYNC
if _url.startswith("postgres://"):
    _url = _url.replace("postgres://", "postgresql://", 1)

engine = create_engine(_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
