import json
import threading
from functools import lru_cache
from pathlib import Path

from app.config import get_settings
from app.schemas.layer import Layer, LayerRole


class LayerRegistry:
    """Stores layer metadata in a JSON file.

    Temporary storage for the early stage of the project - will be replaced
    by a database (PostGIS). The rest of the app only uses the public methods
    below, so swapping the storage will not affect the endpoints.
    """

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._layers: dict[str, Layer] = {}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        self._layers = {item["id"]: Layer.model_validate(item) for item in raw}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = [layer.model_dump(mode="json") for layer in self._layers.values()]
        # Write to a temp file first, then swap - the registry is never half-written
        tmp_path = self.path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp_path.replace(self.path)

    def list_layers(self, role: LayerRole | None = None) -> list[Layer]:
        layers = sorted(self._layers.values(), key=lambda layer: layer.created_at)
        if role is not None:
            layers = [layer for layer in layers if layer.role == role]
        return layers

    def get_layer(self, layer_id: str) -> Layer | None:
        return self._layers.get(layer_id)

    def add_layer(self, layer: Layer) -> Layer:
        with self._lock:
            self._layers[layer.id] = layer
            self._save()
        return layer

    def delete_layer(self, layer_id: str) -> bool:
        with self._lock:
            if self._layers.pop(layer_id, None) is None:
                return False
            self._save()
        return True


@lru_cache
def get_layer_registry() -> LayerRegistry:
    """Return a single shared registry instance."""
    return LayerRegistry(get_settings().data_dir / "layers.json")