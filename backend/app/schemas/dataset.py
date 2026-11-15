from pydantic import BaseModel, Field


class Dataset(BaseModel):
    """A ready-made frontend data folder (PNG frames, GeoJSON layers, meta.json) for one location."""

    id: str = Field(description="Location id, taken from meta.json 'aoi'", examples=["portuguese_bend_california"])
    name: str = Field(examples=["Portuguese Bend"])
    base_url: str = Field(description="URL prefix of the data files", examples=["/datasets/dla_frontendu_portuguese_bend/data/"])
    kind: str = Field(
        default="insar",
        description="What the folder holds: 'insar' (map with the time series, index.html) or 'scene3d' (3D model, view3d.html)",
        examples=["insar", "scene3d"],
    )
    ready: bool = Field(
        default=True,
        description="False for a placeholder location (meta.json 'empty': true): listed, but without data yet",
    )
