from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",      # czytaj też z pliku .env (jeśli istnieje)
        env_prefix="APP_",    # pole "name" <- zmienna APP_NAME
        extra="ignore",       # nieznane zmienne w .env nie wywalają błędu
    )

    name: str = "SAR Infrastructure Risk API"
    version: str = "0.1.0"
    debug: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()