from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import layers
from app.config import get_settings

settings = get_settings()

app = FastAPI(
    title=settings.name,
    version=settings.version,
    debug=settings.debug,
    description="Ocena zagrożenia infrastruktury liniowej osuwiskami na podstawie danych SAR.",
)

# Allow the frontend (running on a different port) to call this API from a browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(layers.router, prefix=settings.api_prefix)


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.name, "version": settings.version}