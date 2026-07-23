"""Validation helpers and bundles."""

from __future__ import annotations

from typing import Any

import unreal


def is_pie_active() -> bool:
    try:
        return unreal.EditorUtilityLibrary.is_playing_in_editor()
    except Exception:  # noqa: BLE001
        return False


def get_dirty_packages() -> list[str]:
    dirty = list(unreal.EditorLoadingAndSavingUtils.get_dirty_content_packages())
    dirty.extend(unreal.EditorLoadingAndSavingUtils.get_dirty_map_packages())
    return [str(p) for p in dirty]


def run_map_check() -> dict[str, Any]:
    """Best-effort map check; returns unsupported if API missing."""
    try:
        subsystem = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        if hasattr(subsystem, "run_map_check"):
            subsystem.run_map_check()
            return {"supported": True, "ran": True}
    except Exception as exc:  # noqa: BLE001
        return {"supported": False, "error": str(exc)}
    return {
        "supported": False,
        "note": "run_map_check not exposed on LevelEditorSubsystem in this build",
    }


def compile_blueprint_paths(paths: list[str]) -> tuple[list[str], list[str]]:
    compiled: list[str] = []
    errors: list[str] = []
    for path in paths:
        asset = unreal.load_asset(path)
        if not isinstance(asset, unreal.Blueprint):
            errors.append(f"Not a Blueprint: {path}")
            continue
        try:
            unreal.BlueprintEditorLibrary.compile_blueprint(asset)
            compiled.append(path)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{path}: {exc}")
    return compiled, errors


def get_recent_log_errors(max_lines: int = 25) -> dict[str, Any]:
    """Best-effort — UE Python has no stable public log tail API."""
    return {
        "supported": False,
        "note": "Use Output Log in editor; no stable Python log-tail API in UE 5.8",
        "max_lines": max_lines,
    }
