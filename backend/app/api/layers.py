from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.schemas.layer import Layer, LayerRole
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