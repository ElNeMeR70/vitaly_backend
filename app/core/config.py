"""Configuración de la aplicación leída del entorno (.env)."""

from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Dict, Optional

class Settings(BaseSettings):
    """Application settings defined dynamically."""
    SUPABASE_URL: str
    SUPABASE_KEY: str
    # Clave service_role para settings y la RPC interna de baja propia.
    # Si falta, settings usa defaults y la baja responde 500 sin escrituras.
    SUPABASE_SERVICE_KEY: Optional[str] = None
    SECRET_KEY: str = "default-insecure-secret-key"  # Override in production
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    API_V1_STR: str = "/api/v1"

    # Configuración de ImageKit.io para almacenamiento de archivos y fotos de perfil
    IMAGEKIT_PUBLIC_KEY: Optional[str] = None
    IMAGEKIT_PRIVATE_KEY: Optional[str] = None
    IMAGEKIT_URL_ENDPOINT: Optional[str] = None
    # Per-request timeout (seconds) and retry count for ImageKit API calls.
    IMAGEKIT_TIMEOUT_SECONDS: float = 15.0
    IMAGEKIT_MAX_RETRIES: int = 1


    # Classroom level limits per category_id.
    # Keys are the category_id values stored in the DB:
    #   1 = Preescolar  (niveles 1-3)
    #   2 = Primaria    (niveles 1-6)
    #   3 = Bachillerato (niveles 1-5)
    CLASSROOM_LEVEL_LIMITS: Dict[int, int] = {
        1: 3,   # Preescolar
        2: 6,   # Primaria
        3: 5,   # Bachillerato
    }

    class Config:
        env_file = ".env"

@lru_cache()
def get_settings() -> Settings:
    """Devuelve la configuración de la aplicación, cacheada para todo el proceso.

    El decorador ``lru_cache`` garantiza que el ``.env`` se lea una sola vez y que
    todos los módulos compartan la misma instancia de :class:`Settings`.

    Returns:
        Settings: Configuración validada de la aplicación.

    Raises:
        pydantic_settings.SettingsError: Si falta alguna variable obligatoria
            (``SUPABASE_URL``, ``SUPABASE_KEY``) en el entorno o el ``.env``.
    """
    return Settings()
