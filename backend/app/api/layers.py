from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.api.auth import require_admin
from app.config import get_settings
from app.schemas.layer import Layer, LayerRole
from app.services.geojson_reader import InvalidGeoJSONError, inspect_geojson
from app.services.layer_registry import LayerRegistry, get_layer_registry

router = APIRouter(prefix="/layers", tags=["layers"])

# "Give me the layer registry" - FastAPI calls get_layer_registry() for us
Registry = Annotated[LayerRegistry, Depends(get_layer_registry)]


@router.get("", response_model=list[Layer])
def list_layers(registry: Registry, role: LayerRole | None = None) -> list[Layer]:
    """List all layers, optionally filtered by role."""
    return registry.list_layers(role)


@router.get("/{layer_id}", response_model=Layer)
def get_layer(layer_id: str, registry: Registry) -> Layer:
    """Get metadata of a single layer."""
    layer = registry.get_layer(layer_id)
    if layer is None:
        raise HTTPException(status_code=404, detail=f"Layer '{layer_id}' not found")
    return layer


@router.post("", response_model=Layer, status_code=201, dependencies=[Depends(require_admin)])
def upload_layer(
    registry: Registry,
    file: Annotated[UploadFile, File(description="GeoJSON FeatureCollection in WGS84 (EPSG:4326)")],
    name: Annotated[str, Form(min_length=1, max_length=100)],
    role: Annotated[LayerRole, Form()],
    description: Annotated[str | None, Form(max_length=1000)] = None,
    source: Annotated[str | None, Form(max_length=200)] = None,
) -> Layer:
    """Upload a new layer from a GeoJSON file."""
    max_mb = get_settings().max_upload_mb
    content = file.file.read()
    if len(content) > max_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File too large (max {max_mb} MB)")

    try:
        info = inspect_geojson(content)
    except InvalidGeoJSONError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    layer = Layer(
        name=name,
        role=role,
        description=description,
        source=source,
        geometry_kind=info.geometry_kind,
        feature_count=info.feature_count,
        bbox=info.bbox,
        file_name=file.filename or "upload.geojson",
    )
    return registry.add_layer(layer, content)


@router.delete("/{layer_id}", status_code=204, dependencies=[Depends(require_admin)])
def delete_layer(layer_id: str, registry: Registry) -> None:
    """Delete a layer together with its data file."""
    if not registry.delete_layer(layer_id):
        raise HTTPException(status_code=404, detail=f"Layer '{layer_id}' not found")

@router.get("/{layer_id}/data")
def get_layer_data(layer_id: str, registry: Registry) -> FileResponse:
    """Return the layer's data as GeoJSON (for drawing it on a map)."""
    if registry.get_layer(layer_id) is None:
        raise HTTPException(status_code=404, detail=f"Layer '{layer_id}' not found")
    path = registry.data_path(layer_id)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Data file for this layer is missing")
    return FileResponse(path, media_type="application/geo+json")