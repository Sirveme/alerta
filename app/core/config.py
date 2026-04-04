"""
core/config.py — Configuración centralizada de la aplicación.

Se lee de variables de entorno con defaults para desarrollo local.
"""

import os


class Settings:
    PROJECT_NAME: str = "alerta.pe"
    VERSION: str = "0.2.0"

    # Base de datos — sin default a localhost para no enmascarar errores en produccion
    _raw_db_url: str = os.environ.get("DATABASE_URL", "")

    @property
    def DATABASE_URL(self) -> str:
        url = self._raw_db_url
        if not url:
            raise RuntimeError("DATABASE_URL no esta definida en las variables de entorno")
        # Railway usa postgres:// pero asyncpg necesita postgresql+asyncpg://
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        return url

    @property
    def DATABASE_URL_SYNC(self) -> str:
        url = self._raw_db_url
        if not url:
            raise RuntimeError("DATABASE_URL no esta definida en las variables de entorno")
        # Railway usa postgres:// pero SQLAlchemy sync necesita postgresql://
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url

    # CORS
    CORS_ORIGINS: list[str] = ["*"]

    # WebAuthn
    RP_ID: str = os.environ.get("RP_ID", "localhost")
    RP_NAME: str = "alerta.pe"
    RP_ORIGIN: str = os.environ.get("RP_ORIGIN", "http://localhost:8000")

    # OpenAI
    OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")

    # SUNAT
    SUNAT_API_URL: str = os.environ.get("SUNAT_API_URL", "https://api.sunat.gob.pe/v1")
    SUNAT_TOKEN_URL: str = os.environ.get("SUNAT_TOKEN_URL", "https://api.sunat.gob.pe/v1/oauth/token")
    SUNAT_CLIENT_ID: str = os.environ.get("SUNAT_CLIENT_ID", "")
    SUNAT_CLIENT_SECRET: str = os.environ.get("SUNAT_CLIENT_SECRET", "")

    # SUNAFIL_BASE_URL eliminada en sesión 7b — reemplazada por Buzón SOL de SUNAT

    # Redis
    REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")


settings = Settings()
