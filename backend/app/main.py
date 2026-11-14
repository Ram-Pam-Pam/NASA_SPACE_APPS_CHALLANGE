from fastapi import FastAPI

from app.api import layers
from app.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.name,
    version=settings.version,
    debug=settings.debug,
    description="Ocena zagrożenia infrastruktury liniowej osuwiskami na podstawie danych SAR.",
)

app.include_router(layers.router, prefix=settings.api_prefix)


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.name, "version": settings.version}