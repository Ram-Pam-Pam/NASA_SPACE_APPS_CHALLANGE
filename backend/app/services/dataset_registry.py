import json
from pathlib import Path
from urllib.parse import quote

from app.schemas.dataset import Dataset

# Datasets are served as static files under this URL prefix (see app/main.py)
DATASETS_URL = "/datasets"


def list_datasets(root_dir: Path) -> list[Dataset]:
    """Find frontend data folders: <root_dir>/<folder>/data/meta.json, one per location.

    The folders are produced by the analysis team (scripts/export_web.py) or, for 3D models,
    by backend/scripts/build_scene3d.py (meta.json "kind": "scene3d"). They are only read
    here - nothing is computed or copied.
    """
    datasets: list[Dataset] = []
    if not root_dir.is_dir():
        return datasets
    for meta_path in sorted(root_dir.glob("*/data/meta.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue  # a broken folder must not hide the others
        folder = meta_path.parent.parent.name
        datasets.append(
            Dataset(
                id=str(meta.get("aoi") or folder),
                name=str(meta.get("name") or folder),
                base_url=f"{DATASETS_URL}/{quote(folder)}/data/",
                kind=str(meta.get("kind") or "insar"),
            )
        )
    return datasets
