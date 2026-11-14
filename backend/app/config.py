from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",      # also read from .env file (if it exists)
        env_prefix="APP_",    # field "name" <- env variable APP_NAME
        extra="ignore",       # unknown variables in .env don't raise errors
    )

    name: str = "Corridor Watch API"
    version: str = "0.2.0"
    debug: bool = False
    api_prefix: str = "/api/v1"
    data_dir: Path = Path("data")  # uploaded layers, locations and their registries live here
    frontend_dir: Path = Path("../frontend")  # web page served at "/" (skipped if the folder is missing)
    max_upload_mb: int = 50  # reject uploaded files bigger than this
    # Frontend addresses allowed to call the API from a browser (CORS).
    # Not needed when the page is served by this backend (same address).
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]


@lru_cache
def get_settings() -> Settings:
    """Return a single shared Settings instance (loaded once)."""
    return Settings()
