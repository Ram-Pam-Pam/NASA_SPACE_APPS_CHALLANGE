import json
import shutil
import threading
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.schemas.location import Location


class LocationExistsError(ValueError):
    """Raised when creating a location with an id that is already taken."""


class LocationRegistry:
    """Stores locations on disk: metadata in locations.json, data packages in <id>/data.json.

    Same idea as LayerRegistry - temporary file storage behind a small set of
    public methods, so it can later be replaced by a database.
    """

    def __init__(self, root_dir: Path):
        self.root_dir = root_dir
        self.index_path = root_dir / "locations.json"
        self._lock = threading.Lock()
        self._locations: dict[str, Location] = {}
        self._loaded_mtime: int | None = None
        self._load()

    def _load(self) -> None:
        if not self.index_path.exists():
            return
        raw = json.loads(self.index_path.read_text(encoding="utf-8"))
        self._locations = {item["id"]: Location.model_validate(item) for item in raw}
        self._loaded_mtime = self.index_path.stat().st_mtime_ns

    def _refresh_if_changed(self) -> None:
        """Reload the index if another process (e.g. the import script) changed it."""
        if self.index_path.exists() and self.index_path.stat().st_mtime_ns != self._loaded_mtime:
            with self._lock:
                self._load()

    def _save(self) -> None:
        self.root_dir.mkdir(parents=True, exist_ok=True)
        data = [loc.model_dump(mode="json") for loc in self._locations.values()]
        _write_atomic(self.index_path, json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"))
        self._loaded_mtime = self.index_path.stat().st_mtime_ns

    def data_path(self, location_id: str) -> Path:
        """Where the data package of a location is stored."""
        return self.root_dir / location_id / "data.json"

    def list_locations(self) -> list[Location]:
        """Ready locations first, then by 'order', then by name (same as build_location.py)."""
        self._refresh_if_changed()
        return sorted(self._locations.values(), key=lambda loc: (not loc.ready, loc.order, loc.name))

    def get_location(self, location_id: str) -> Location | None:
        self._refresh_if_changed()
        return self._locations.get(location_id)

    def create_location(self, location: Location) -> Location:
        with self._lock:
            if location.id in self._locations:
                raise LocationExistsError(f"Location '{location.id}' already exists")
            self._locations[location.id] = location
            self._save()
        return location

    def update_location(self, location_id: str, changes: dict[str, Any]) -> Location:
        """Apply changes to metadata. The result is validated as a whole Location."""
        with self._lock:
            current = self._locations[location_id]
            updated = Location.model_validate({**current.model_dump(), **changes})
            self._locations[location_id] = updated
            self._save()
        return updated

    def save_data(self, location_id: str, content: bytes) -> Location:
        """Store (or replace) the data package and mark the location as ready."""
        with self._lock:
            location = self._locations[location_id]
            _write_atomic(self.data_path(location_id), content)
            updated = location.model_copy(update={
                "ready": True,
                "data_size_bytes": len(content),
                "data_updated_at": datetime.now(timezone.utc),
            })
            self._locations[location_id] = updated
            self._save()
        return updated

    def delete_location(self, location_id: str) -> bool:
        with self._lock:
            if self._locations.pop(location_id, None) is None:
                return False
            self._save()
            shutil.rmtree(self.root_dir / location_id, ignore_errors=True)
        return True


def _write_atomic(path: Path, content: bytes) -> None:
    """Write to a temp file first, then swap - the target is never half-written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_bytes(content)
    tmp_path.replace(path)


@lru_cache
def get_location_registry() -> LocationRegistry:
    """Return a single shared registry instance."""
    return LocationRegistry(get_settings().data_dir / "locations")
