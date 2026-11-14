from fastapi import FastAPI

from app.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.name,
    version=settings.version,
    debug=settings.debug,
    description="Ocena zagrożenia infrastruktury liniowej osuwiskami na podstawie danych SAR.",
)


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.name, "version": settings.version}