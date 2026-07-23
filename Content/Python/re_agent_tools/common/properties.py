"""Property get/set with readback verification."""

from __future__ import annotations

import json
from typing import Any

import unreal
from toolset_registry.helpers import require_editable

from re_agent_tools.common.serialization import parse_json


def _instance_object(obj: unreal.Object) -> unreal.Object:
    if isinstance(obj, unreal.Blueprint):
        return obj.generated_class()
    return obj


def get_properties_compact(instance: unreal.Object, properties: list[str]) -> dict[str, Any]:
    if not properties:
        return {}
    raw = unreal.ToolsetLibrary.get_object_properties(_instance_object(instance), properties)
    return json.loads(raw) if raw else {}


def set_properties_and_verify(
    instance: unreal.Object,
    values_json: str,
    *,
    verify_properties: list[str] | None = None,
) -> tuple[bool, dict[str, Any], list[str]]:
    """Set properties from JSON string; optionally read back verify list."""
    obj = _instance_object(instance)
    require_editable(obj)
    values = parse_json(values_json, field_name="properties")
    if not isinstance(values, dict):
        raise ValueError("properties must be a JSON object")

    ok = unreal.ToolsetLibrary.set_object_properties(obj, values_json)
    warnings: list[str] = []
    readback: dict[str, Any] = {}
    props = verify_properties or list(values.keys())
    if props:
        readback = get_properties_compact(instance, props)
        for key, expected in values.items():
            if key not in readback:
                warnings.append(f"Readback missing property: {key}")
            elif readback.get(key) != expected:
                warnings.append(f"Readback mismatch for {key}")
    return ok, readback, warnings
