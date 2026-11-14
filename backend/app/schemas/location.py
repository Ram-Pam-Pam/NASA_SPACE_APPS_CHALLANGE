from datetime import datetime, timezone

from pydantic import BaseModel, Field

# Location id is used in URLs (#portuguese_bend_california) and as a folder name,
# so only lowercase letters, digits and underscores are allowed.
LOCATION_ID_PATTERN = r"^[a-z0-9_]{3,64}$"

# Map view as [[south_lat, west_lon], [north_lat, east_lon]] - Leaflet's order (lat first!)
MapView = tuple[tuple[float, float], tuple[float, float]]


class LocationBase(BaseModel):
    """Fields shared by all location models."""

    name: str = Field(min_length=1, max_length=100, examples=["Portuguese Bend"])
    region: str | None = Field(default=None, max_length=100, examples=["Kalifornia, USA"])
    hazard: str | None = Field(default=None, max_length=100, examples=["Kompleks osuwiskowy"])
    summary: str | None = Field(default=None, max_length=2000)
    order: int = Field(default=99, description="Position on the location list (lower = higher)")
    note: str | None = Field(default=None, max_length=500, description="Shown when data is not ready yet")
    view: MapView | None = Field(default=None, description="Initial map view [[S, W], [N, E]]")


class LocationCreate(LocationBase):
    """Data provided by the user when adding a new location."""

    id: str = Field(pattern=LOCATION_ID_PATTERN, examples=["portuguese_bend_california"])


class LocationUpdate(BaseModel):
    """Fields that can be changed after creation. All optional - send only what changes."""

    name: str | None = Field(default=None, min_length=1, max_length=100)
    region: str | None = Field(default=None, max_length=100)
    hazard: str | None = Field(default=None, max_length=100)
    summary: str | None = Field(default=None, max_length=2000)
    order: int | None = None
    note: str | None = Field(default=None, max_length=500)
    view: MapView | None = None


class Location(LocationBase):
    """Full location metadata, as stored and returned by the API."""

    id: str = Field(pattern=LOCATION_ID_PATTERN)
    ready: bool = False  # True once a data package has been uploaded
    data_size_bytes: int | None = None
    data_updated_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
