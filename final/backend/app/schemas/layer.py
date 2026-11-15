from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, Field


class LayerRole(str, Enum):
    """What the layer represents in the risk analysis."""

    infrastructure = "infrastructure"  # pipelines, power lines, water pipes
    hazard = "hazard"                  # landslide inventory, susceptibility zones
    deformation = "deformation"        # InSAR ground motion (velocity, displacement)
    context = "context"                # anything else: DEM, land cover, boundaries


class GeometryKind(str, Enum):
    """Type of data stored in the layer."""

    point = "point"
    line = "line"
    polygon = "polygon"
    raster = "raster"


class LayerBase(BaseModel):
    """Fields shared by all layer models."""

    name: str = Field(min_length=1, max_length=100)
    role: LayerRole
    description: str | None = Field(default=None, max_length=1000)
    source: str | None = Field(default=None, description="Data source, e.g. 'EGMS', 'OpenStreetMap'")


class LayerCreate(LayerBase):
    """Data provided by the user when adding a new layer."""


class Layer(LayerBase):
    """Full layer metadata, as stored and returned by the API."""

    id: str = Field(default_factory=lambda: uuid4().hex)
    geometry_kind: GeometryKind
    file_name: str
    feature_count: int | None = None
    # Bounding box: (min_lon, min_lat, max_lon, max_lat) - used to zoom the map to the layer
    bbox: tuple[float, float, float, float] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))