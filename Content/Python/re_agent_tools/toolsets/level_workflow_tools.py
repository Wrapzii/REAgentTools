"""Level composite workflow tools."""

from __future__ import annotations

import unreal
import toolset_registry

from re_agent_tools.common import limits
from re_agent_tools.common.logging import log_tool_call
from re_agent_tools.common.properties import set_properties_and_verify
from re_agent_tools.common.resolution import ResolutionError, actor_ref, resolve_actor
from re_agent_tools.common.results import WorkflowTimer, error_result, make_request_id, workflow_result
from re_agent_tools.common.serialization import parse_json_list
from re_agent_tools.common.transactions import scoped_transaction
from re_agent_tools.common.validation import run_map_check


def _les() -> unreal.LevelEditorSubsystem:
    return unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)


def _eas() -> unreal.EditorActorSubsystem:
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def _eas_assets() -> unreal.EditorAssetSubsystem:
    return unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)


@unreal.uclass()
class RELevelWorkflowTools(unreal.ToolsetDefinition):
    """RE composite level workflows: open/create, place actors, map check."""

    @toolset_registry.tool_call
    @staticmethod
    def open_or_create_level(level_path: str, create_if_missing: bool = False) -> str:
        """Open level; optionally create new level asset if missing."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        if not level_path.startswith("/"):
            level_path = f"/Game/{level_path.lstrip('/')}"
        try:
            if not _eas_assets().does_asset_exist(level_path):
                if not create_if_missing:
                    return error_result(
                        "open_or_create_level",
                        f"Level not found: {level_path}",
                        request_id=request_id,
                        duration_ms=timer.elapsed_ms,
                    )
                folder, _, name = level_path.rpartition("/")
                unreal.EditorLevelLibrary.new_level(f"{folder}/{name}")
                created = [level_path]
            else:
                created = []
            _les().load_level(level_path)
            result = workflow_result(
                "open_or_create_level",
                True,
                f"Opened {level_path}",
                request_id=request_id,
                created=created,
                changed=[level_path],
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("open_or_create_level", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except Exception as exc:  # noqa: BLE001
            return error_result("open_or_create_level", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms)

    @toolset_registry.tool_call
    @staticmethod
    def place_configure_save_actors(placements_json: str, save_level: bool = True) -> str:
        """Place actors from JSON list, configure, optional save."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        items = parse_json_list(placements_json, field_name="placements_json")
        created: list[str] = []
        errors: list[str] = []
        with scoped_transaction("RE place actors"):
            for entry in items[: limits.MUTATE_LIMIT]:
                if not isinstance(entry, dict) or "class_path" not in entry:
                    errors.append(f"Invalid: {entry}")
                    continue
                try:
                    cls = unreal.load_class(None, str(entry["class_path"]))
                    if not cls:
                        raise ValueError(f"Class not found: {entry['class_path']}")
                    loc = entry.get("location", [0, 0, 0])
                    rot = entry.get("rotation", [0, 0, 0])
                    sc = entry.get("scale", [1, 1, 1])
                    from re_agent_tools.common.spawn_helpers import (
                        rotator_from_list,
                        spawn_actor_from_class,
                        vector_from_list,
                    )
                    actor = spawn_actor_from_class(
                        cls,
                        vector_from_list(loc),
                        rotator_from_list(rot),
                        vector_from_list(sc),
                    )
                    label = entry.get("label", "")
                    if label:
                        actor.set_actor_label(label)
                    if entry.get("folder"):
                        actor.set_folder_path(unreal.Name(str(entry["folder"])))
                    if entry.get("properties_json"):
                        set_properties_and_verify(actor, str(entry["properties_json"]))
                    created.append(actor.get_actor_label())
                except (ResolutionError, ValueError) as exc:
                    errors.append(str(exc))
        saved: list[str] = []
        if save_level and created:
            unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
            saved.append("current_level")
        result = workflow_result(
            "place_configure_save_actors",
            bool(created),
            f"Placed {len(created)} actors",
            request_id=request_id,
            created=created,
            saved=saved,
            errors=errors,
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("place_configure_save_actors", success=bool(created), request_id=request_id, duration_ms=timer.elapsed_ms)
        return result

    @toolset_registry.tool_call
    @staticmethod
    def run_map_check() -> str:
        """Run editor map check if API available."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        info = run_map_check()
        result = workflow_result(
            "run_map_check",
            info.get("ran", False) or info.get("supported", False) is False,
            "Map check requested",
            request_id=request_id,
            extra={"map_check": info},
            warnings=[] if info.get("ran") else [info.get("note", "unsupported")],
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("run_map_check", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
        return result
