"""Validation of Corridor Watch data packages produced by build_location.py."""
import json

# Top-level keys the web page needs - if they are missing, it is not our package
REQUIRED_KEYS = ("grid", "params", "payload", "zones", "pipes")
JS_PREFIX = "window.CW_DATA"


class InvalidPackageError(ValueError):
    """Raised when an uploaded file is not a usable data package."""


def parse_package(content: bytes, location_id: str) -> bytes:
    """Validate a data package and return it as compact JSON bytes.

    Accepts both plain JSON and the data.js file written by build_location.py:
        window.CW_DATA = window.CW_DATA || {};
        window.CW_DATA["<id>"] = { ... };
    """
    try:
        text = content.decode("utf-8").strip()
    except UnicodeDecodeError as e:
        raise InvalidPackageError("File is not UTF-8 text") from e

    if text.startswith(JS_PREFIX):
        # Cut away the JavaScript wrapper, keep only the { ... } object
        start = text.find("] = ")
        if start == -1:
            raise InvalidPackageError("Unrecognised data.js format")
        text = text[start + len("] = "):].rstrip().rstrip(";")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise InvalidPackageError(f"File is not valid JSON: {e}") from e

    if not isinstance(data, dict):
        raise InvalidPackageError("Package must be a JSON object")
    missing = [key for key in REQUIRED_KEYS if key not in data]
    if missing:
        raise InvalidPackageError(
            f"Missing keys: {', '.join(missing)} - is this a file from build_location.py?"
        )
    if data.get("id") not in (None, location_id):
        raise InvalidPackageError(f"Package is for location '{data['id']}', not '{location_id}'")

    data["id"] = location_id
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
