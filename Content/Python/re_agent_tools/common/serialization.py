"""JSON serialization helpers."""

from __future__ import annotations

import json
from typing import Any


def dumps_compact(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), default=_json_default)


def parse_json(text: str, *, field_name: str = "json") -> Any:
    if not text or not str(text).strip():
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid {field_name}: {exc}") from exc


def parse_json_list(text: str, *, field_name: str = "json") -> list[Any]:
    parsed = parse_json(text, field_name=field_name)
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        return [parsed]
    raise ValueError(f"{field_name} must be a JSON array or object")


def transform_from_json(text: str) -> dict[str, Any]:
    """Parse location/rotation/scale JSON. Empty -> {}."""
    parsed = parse_json(text, field_name="transform")
    if not isinstance(parsed, dict):
        raise ValueError("transform must be a JSON object")
    return parsed


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "export_text"):
        return obj.export_text()
    if hasattr(obj, "to_tuple"):
        return obj.to_tuple()
    return str(obj)
