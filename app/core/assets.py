"""Versionado automático de assets estáticos."""
import hashlib
from functools import lru_cache
from pathlib import Path

STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"


@lru_cache(maxsize=256)
def _file_hash(filepath: Path) -> str:
    """Hash corto del archivo (basado en mtime + size)."""
    if not filepath.exists():
        return "0"
    stat = filepath.stat()
    raw = f"{stat.st_mtime}_{stat.st_size}".encode()
    return hashlib.md5(raw).hexdigest()[:8]


def static_url(relative_path: str) -> str:
    """Devuelve /static/{path}?v={hash}.

    Uso en Jinja: {{ static_url('css/base.css') }}
    """
    relative_path = relative_path.lstrip("/")
    full_path = STATIC_DIR / relative_path
    h = _file_hash(full_path)
    return f"/static/{relative_path}?v={h}"
