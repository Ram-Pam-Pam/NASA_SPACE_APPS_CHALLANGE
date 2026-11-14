from fastapi import APIRouter

from app.config import get_settings
from app.schemas.dataset import Dataset
from app.services.dataset_registry import list_datasets

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get("", response_model=list[Dataset])
def get_datasets() -> list[Dataset]:
    """List ready-made frontend data folders (one per location) and where their files are served."""
    return list_datasets(get_settings().datasets_dir)
