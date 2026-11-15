from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import ValidationError

from app.api.auth import require_admin
from app.config import get_settings
from app.schemas.location import Location, LocationCreate, LocationUpdate
from app.services.location_package import InvalidPackageError, parse_package
from app.services.location_registry import LocationExistsError, LocationRegistry, get_location_registry

router = APIRouter(prefix="/locations", tags=["locations"])

Registry = Annotated[LocationRegistry, Depends(get_location_registry)]


def _get_or_404(registry: LocationRegistry, location_id: str) -> Location:
    location = registry.get_location(location_id)
    if location is None:
        raise HTTPException(status_code=404, detail=f"Location '{location_id}' not found")
    return location


@router.get("", response_model=list[Location])
def list_locations(registry: Registry) -> list[Location]:
    """List all locations (ready ones first)."""
    return registry.list_locations()


@router.get("/{location_id}", response_model=Location)
def get_location(location_id: str, registry: Registry) -> Location:
    """Get metadata of a single location."""
    return _get_or_404(registry, location_id)


@router.post("", response_model=Location, status_code=201, dependencies=[Depends(require_admin)])
def create_location(data: LocationCreate, registry: Registry) -> Location:
    """Create a new location (without data - it shows as 'coming soon')."""
    try:
        return registry.create_location(Location(**data.model_dump()))
    except LocationExistsError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


@router.patch("/{location_id}", response_model=Location, dependencies=[Depends(require_admin)])
def update_location(location_id: str, changes: LocationUpdate, registry: Registry) -> Location:
    """Change selected metadata fields (send only the fields that change)."""
    _get_or_404(registry, location_id)
    try:
        return registry.update_location(location_id, changes.model_dump(exclude_unset=True))
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors(include_url=False)) from e


@router.put("/{location_id}/data", response_model=Location, dependencies=[Depends(require_admin)])
def upload_location_data(
    location_id: str,
    registry: Registry,
    file: Annotated[UploadFile, File(description="data.js or data.json from build_location.py")],
) -> Location:
    """Upload or replace the data package of a location."""
    _get_or_404(registry, location_id)
    max_mb = get_settings().max_upload_mb
    content = file.file.read()
    if len(content) > max_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"File too large (max {max_mb} MB)")
    try:
        package = parse_package(content, location_id)
    except InvalidPackageError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return registry.save_data(location_id, package)


@router.get("/{location_id}/data")
def get_location_data(location_id: str, registry: Registry) -> FileResponse:
    """Return the data package (what the web page draws)."""
    location = _get_or_404(registry, location_id)
    path = registry.data_path(location_id)
    if not location.ready or not path.exists():
        raise HTTPException(status_code=404, detail="No data package uploaded for this location yet")
    return FileResponse(path, media_type="application/json")


@router.delete("/{location_id}", status_code=204, dependencies=[Depends(require_admin)])
def delete_location(location_id: str, registry: Registry) -> None:
    """Delete a location together with its data package."""
    if not registry.delete_location(location_id):
        raise HTTPException(status_code=404, detail=f"Location '{location_id}' not found")
