from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",      # also read from .env file (if it exists)
        env_prefix="APP_",    # field "name" <- env variable APP_NAME
        extra="ignore",       # unknown variables in .env don't raise errors
    )

    name: str = "SAR Infrastructure Risk API"
    version: str = "0.1.0"
    debug: bool = False
    api_prefix: str = "/api/v1"
    data_dir: Path = Path("data")  # uploaded layers and the layer registry live here
    max_upload_mb: int = 50  # reject uploaded files bigger than this


@lru_cache
def get_settings() -> Settings:
    """Return a single shared Settings instance (loaded once)."""
    return Settings()