from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import datasets, layers, locations
from app.config import get_settings
from app.services.dataset_registry import DATASETS_URL

settings = get_settings()

app = FastAPI(
    title=settings.name,
    version=settings.version,
    debug=settings.debug,
    description="Ruch gruntu z radaru satelitarnego (InSAR) przy infrastrukturze liniowej.",
)

# Allow a frontend running on a different port to call this API from a browser
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)
# Compress responses - location data packages are several MB of JSON
app.add_middleware(GZipMiddleware, minimum_size=1000)

app.include_router(datasets.router, prefix=settings.api_prefix)
app.include_router(layers.router, prefix=settings.api_prefix)
app.include_router(locations.router, prefix=settings.api_prefix)


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.name, "version": settings.version}


# Data files of the frontend datasets (PNG frames, GeoJSON, JSON) - plain static files.
if settings.datasets_dir.is_dir():
    app.mount(DATASETS_URL, StaticFiles(directory=settings.datasets_dir), name="datasets")

# The web page. Mounted LAST, so /api, /docs and /health are matched first.
if settings.frontend_dir.is_dir():
    app.mount("/", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
