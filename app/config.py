"""Configuración central de Alerta.pe (pydantic-settings)."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # === BASE DE DATOS ===
    database_url: str = Field(default="", description="postgresql+asyncpg://...")
    database_url_sync: str = Field(default="", description="postgresql:// para Alembic")

    # === SEGURIDAD ===
    fernet_key: str = Field(default="", description="Clave para encriptar credenciales SOL")
    jwt_secret: str = Field(default="cambiar_en_produccion", description="Clave JWT")
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 15
    jwt_refresh_token_expire_days: int = 7

    # === SUNAT (mantener para compatibilidad con prototipo) ===
    sunat_tipo_usuario: int = 2
    sunat_ruc: str = ""
    sunat_dni: str = ""
    sunat_usuario: str = ""
    sunat_clave: str = ""
    sunat_timeout_segundos: int = 60  # Compatibilidad: aún se usa en algunos lugares
    sunat_reintentos: int = 2

    # === APLICACIÓN ===
    app_name: str = "Alerta.pe"
    app_env: str = "development"
    app_base_url: str = "http://localhost:8000"
    app_port: int = 8000
    debug: bool = True

    # === SUPER ADMIN ===
    super_admin_email: str = "info@perusistemas.pro"
    super_admin_password: str = ""

    # === TIMEZONE ===
    app_timezone: str = "America/Lima"

    # === API externa: validación RUC ===
    apis_net_pe_token: str = Field(default="", description="Token apis.net.pe para validar RUC")

    # === WEB PUSH ===
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_claim_email: str = "info@perusistemas.pro"

    @property
    def j_username(self) -> str:
        """Compatibilidad con prototipo: username para login SUNAT."""
        if self.sunat_tipo_usuario == 2:
            return self.sunat_dni
        return self.sunat_usuario


settings = Settings()