"""Import locations from a Corridor Watch folder into the backend.

Reads every locations/<id>/location.json (metadata) and, if present,
locations/<id>/data.js (data package written by build_location.py).

Run from the backend/ folder:
    python scripts/import_corridor_watch.py "C:\\path\\to\\corridor_watch\\locations"

Safe to run again: existing locations get their metadata and data refreshed.
The running server picks up the changes automatically (no restart needed).
"""
import json
import sys
from pathlib import Path

# Make the "app" package importable when running this file directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.location import Location, LocationUpdate  # noqa: E402
from app.services.location_package import InvalidPackageError, parse_package  # noqa: E402
from app.services.location_registry import get_location_registry  # noqa: E402

METADATA_FIELDS = ("name", "region", "hazard", "summary", "order", "note", "view")


def import_locations(locations_dir: Path) -> None:
    registry = get_location_registry()
    configs = sorted(locations_dir.glob("*/location.json"))
    if not configs:
        sys.exit(f"No */location.json found in {locations_dir}")

    for config_path in configs:
        location_id = config_path.parent.name
        config = json.loads(config_path.read_text(encoding="utf-8"))
        metadata = {key: config[key] for key in METADATA_FIELDS if config.get(key) is not None}

        if registry.get_location(location_id) is None:
            registry.create_location(Location(id=location_id, **metadata))
            action = "created"
        else:
            changes = LocationUpdate(**metadata).model_dump(exclude_unset=True)
            registry.update_location(location_id, changes)
            action = "updated"

        data_path = config_path.parent / "data.js"
        if data_path.exists():
            try:
                package = parse_package(data_path.read_bytes(), location_id)
            except InvalidPackageError as e:
                print(f"{location_id}: {action}, data SKIPPED ({e})")
                continue
            registry.save_data(location_id, package)
            action += f", data {len(package) / 1e6:.1f} MB"
        else:
            action += ", no data yet"
        print(f"{location_id}: {action}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    import_locations(Path(sys.argv[1]))
