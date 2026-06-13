"""Helpers de timezone (Lima)."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import settings

LIMA_TZ = ZoneInfo(settings.app_timezone)


def utc_now() -> datetime:
    """Datetime aware en UTC."""
    return datetime.now(timezone.utc)


def lima_now() -> datetime:
    """Datetime aware en hora de Lima."""
    return datetime.now(LIMA_TZ)


def to_lima(dt: datetime) -> datetime:
    """Convierte cualquier datetime a hora de Lima.

    Si el dt es naive, asume UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(LIMA_TZ)


def fmt_lima(dt: datetime, fmt: str = "%d/%m/%Y %H:%M") -> str:
    """Formatea dt en hora Lima con formato dd/mm/aaaa hh:mm."""
    if not dt:
        return ""
    return to_lima(dt).strftime(fmt)


def fmt_lima_date(dt: datetime) -> str:
    return fmt_lima(dt, "%d/%m/%Y")


def fmt_lima_time(dt: datetime) -> str:
    return fmt_lima(dt, "%H:%M")
