"""Invoke REAgentTools composites without MCP (Unreal Remote Control / editor Python).

Cursor Remote Control / Agents Window often fails mcp-unreal live discovery even when
the editor is reachable via Unreal Remote Control (:30010). The composites still live
in-process — this bridge calls them directly so agents must NOT fall back to Epic
one-shots or ad-hoc RC scripts.
"""

from __future__ import annotations

import inspect
import json
import os
from typing import Any, Callable

from re_agent_tools.common.results import error_result, make_request_id
from re_agent_tools.common.serialization import dumps_compact
from re_agent_tools.toolsets.actor_workflow_tools import REActorWorkflowTools
from re_agent_tools.toolsets.anim_workflow_tools import REAnimWorkflowTools
from re_agent_tools.toolsets.asset_workflow_tools import REAssetWorkflowTools
from re_agent_tools.toolsets.batch_workflow_tools import REBatchWorkflowTools
from re_agent_tools.toolsets.blueprint_workflow_tools import REBlueprintWorkflowTools
from re_agent_tools.toolsets.capture_workflow_tools import RECaptureWorkflowTools
from re_agent_tools.toolsets.character_workflow_tools import RECharacterWorkflowTools
from re_agent_tools.toolsets.context_tools import REContextTools
from re_agent_tools.toolsets.dress_workflow_tools import REDressWorkflowTools
from re_agent_tools.toolsets.level_workflow_tools import RELevelWorkflowTools
from re_agent_tools.toolsets.lighting_workflow_tools import RELightingWorkflowTools
from re_agent_tools.toolsets.material_workflow_tools import REMaterialWorkflowTools
from re_agent_tools.toolsets.niagara_workflow_tools import RENiagaraWorkflowTools
from re_agent_tools.toolsets.project_workflow_tools import REProjectWorkflowTools
from re_agent_tools.toolsets.validation_workflow_tools import REValidationWorkflowTools

TOOLSETS: dict[str, type] = {
    "REContextTools": REContextTools,
    "REActorWorkflowTools": REActorWorkflowTools,
    "REAnimWorkflowTools": REAnimWorkflowTools,
    "REAssetWorkflowTools": REAssetWorkflowTools,
    "REBlueprintWorkflowTools": REBlueprintWorkflowTools,
    "REMaterialWorkflowTools": REMaterialWorkflowTools,
    "RELevelWorkflowTools": RELevelWorkflowTools,
    "REValidationWorkflowTools": REValidationWorkflowTools,
    "REBatchWorkflowTools": REBatchWorkflowTools,
    "REProjectWorkflowTools": REProjectWorkflowTools,
    "RENiagaraWorkflowTools": RENiagaraWorkflowTools,
    "REDressWorkflowTools": REDressWorkflowTools,
    "RECharacterWorkflowTools": RECharacterWorkflowTools,
    "RELightingWorkflowTools": RELightingWorkflowTools,
    "RECaptureWorkflowTools": RECaptureWorkflowTools,
}

# Full MCP-style names → short class names.
_FULL_ALIASES: dict[str, str] = {
    f"re_agent_tools.toolsets.{module}.{name}": name
    for name, module in (
        ("REContextTools", "context_tools"),
        ("REActorWorkflowTools", "actor_workflow_tools"),
        ("REAnimWorkflowTools", "anim_workflow_tools"),
        ("REAssetWorkflowTools", "asset_workflow_tools"),
        ("REBlueprintWorkflowTools", "blueprint_workflow_tools"),
        ("REMaterialWorkflowTools", "material_workflow_tools"),
        ("RELevelWorkflowTools", "level_workflow_tools"),
        ("REValidationWorkflowTools", "validation_workflow_tools"),
        ("REBatchWorkflowTools", "batch_workflow_tools"),
        ("REProjectWorkflowTools", "project_workflow_tools"),
        ("RENiagaraWorkflowTools", "niagara_workflow_tools"),
        ("REDressWorkflowTools", "dress_workflow_tools"),
        ("RECharacterWorkflowTools", "character_workflow_tools"),
        ("RELightingWorkflowTools", "lighting_workflow_tools"),
        ("RECaptureWorkflowTools", "capture_workflow_tools"),
    )
}

# Explicit catalog — do not rely on Unreal dir()/reflection for discovery.
TOOL_CATALOG: dict[str, tuple[str, ...]] = {
    "REContextTools": (
        "get_plugin_capabilities",
        "get_editor_context",
        "resolve_targets",
        "inspect_targets_compact",
    ),
    "REActorWorkflowTools": (
        "set_actor_properties_and_verify",
        "set_component_properties_and_verify",
        "spawn_configure_attach_and_verify",
        "batch_transform_actors",
        "delete_actors_validated",
        "organize_actors",
        "place_from_asset_and_verify",
        "rotate_actors_and_verify",
    ),
    "REAnimWorkflowTools": (
        "list_pose_presets",
        "get_animation_pipeline_notes",
        "author_controlrig_pose_timeline",
        "export_sequence_to_anim_and_montage",
        "create_montage_from_anim",
        "author_clip_from_pose_timeline",
    ),
    "REAssetWorkflowTools": (
        "find_assets_compact",
        "bulk_edit_asset_properties_and_save",
        "save_assets",
    ),
    "REBatchWorkflowTools": ("execute_editor_batch",),
    "REBlueprintWorkflowTools": (
        "inspect_blueprint_compact",
        "create_blueprint_from_class",
        "set_class_defaults_compile_save",
        "compile_blueprints",
        "create_or_update_blueprint",
    ),
    "RECaptureWorkflowTools": (
        "capture_viewport_to_disk",
        "render_material_preview_to_disk",
        "get_recent_log_entries_compact",
        "pie_cast_and_capture",
        "visual_loop_tool_notes",
    ),
    "RECharacterWorkflowTools": (
        "inspect_character_compact",
        "set_character_mesh_and_verify",
        "set_visual_combat_montages",
        "list_mesh_sockets_compact",
    ),
    "REDressWorkflowTools": (
        "place_static_mesh_and_verify",
        "batch_place_static_meshes",
        "scatter_static_meshes_ring",
        "snap_actors_to_floor",
    ),
    "RELevelWorkflowTools": (
        "open_or_create_level",
        "place_configure_save_actors",
        "run_map_check",
    ),
    "RELightingWorkflowTools": (
        "get_environment_lights_compact",
        "list_mood_presets",
        "apply_mood_lighting",
        "set_light_properties_and_verify",
    ),
    "REMaterialWorkflowTools": (
        "create_material_instance_configure_save",
        "update_material_instance_parameters",
        "assign_materials_to_mesh_components",
        "create_assign_material_instance",
    ),
    "RENiagaraWorkflowTools": (
        "place_niagara_system_and_verify",
        "assign_niagara_system_to_component",
        "set_niagara_user_parameters_and_verify",
        "inspect_niagara_compact",
    ),
    "REProjectWorkflowTools": (
        "reload_workflow_modules",
        "get_plugin_project_notes",
    ),
    "REValidationWorkflowTools": (
        "compile_save_validate",
        "get_recent_errors_compact",
        "run_validation_bundle",
    ),
}


def normalize_toolset_name(name: str) -> str:
    raw = (name or "").strip()
    if not raw:
        raise ValueError("toolset name is empty")
    if raw in TOOLSETS:
        return raw
    if raw in _FULL_ALIASES:
        return _FULL_ALIASES[raw]
    # Accept trailing .ToolName stripped already; also Class.method → Class
    if "." in raw:
        short = raw.rsplit(".", 1)[-1]
        if short in TOOLSETS:
            return short
        if raw in _FULL_ALIASES:
            return _FULL_ALIASES[raw]
    raise ValueError(
        f"Unknown REAgentTools toolset: {name!r}. "
        f"Known={sorted(TOOLSETS)}"
    )


def list_toolsets() -> list[str]:
    return sorted(TOOLSETS)


def list_tools(toolset: str | None = None) -> dict[str, list[str]]:
    names = [normalize_toolset_name(toolset)] if toolset else list_toolsets()
    return {n: list(TOOL_CATALOG[n]) for n in names}


def call_tool(
    toolset: str,
    tool: str,
    arguments: dict[str, Any] | None = None,
) -> str:
    """Call one REAgentTools composite; returns WorkflowResult JSON string."""
    request_id = make_request_id()
    try:
        ts = normalize_toolset_name(toolset)
        cls = TOOLSETS[ts]
        available = TOOL_CATALOG[ts]
        tool_name = (tool or "").strip()
        if tool_name not in available:
            return error_result(
                "rc_bridge.call_tool",
                f"Unknown tool {tool_name!r} on {ts}. Available={list(available)}",
                request_id=request_id,
            )
        fn: Callable[..., Any] = getattr(cls, tool_name)
        args = dict(arguments or {})
        # Filter kwargs to signature when available (ignore extras quietly).
        try:
            sig = inspect.signature(fn)
            allowed = {k: v for k, v in args.items() if k in sig.parameters}
        except (TypeError, ValueError):
            allowed = args
        result = fn(**allowed)
        if not isinstance(result, str):
            return dumps_compact({"success": True, "result": result, "via": "rc_bridge"})
        return result
    except Exception as exc:  # noqa: BLE001
        return error_result(
            "rc_bridge.call_tool",
            f"RC bridge call failed: {exc}",
            request_id=request_id,
            extra={
                "via": "rc_bridge",
                "toolset": toolset,
                "tool": tool,
                "hint": (
                    "Stay on REAgentTools via rc_bridge / _rc_reagent_exec.py. "
                    "Do not fall back to Epic SceneTools/ActorTools."
                ),
            },
        )


def run_request(request: dict[str, Any]) -> str:
    """Dispatch a request dict.

    Shapes:
      {"action":"list_toolsets"}
      {"action":"list_tools","toolset":"REContextTools"}
      {"action":"call","toolset":"REContextTools","tool":"get_editor_context","arguments":{...}}
      {"toolset":"...","tool":"...","arguments":{...}}  # implied call
    """
    if not isinstance(request, dict):
        return error_result("rc_bridge.run_request", "request must be a JSON object")

    action = str(request.get("action") or "").strip().lower()
    if not action:
        if request.get("toolset") and request.get("tool"):
            action = "call"
        else:
            action = "list_toolsets"

    if action in ("list_toolsets", "list"):
        return dumps_compact({
            "success": True,
            "via": "rc_bridge",
            "transport": "unreal_remote_control_or_editor_python",
            "note": (
                "MCP preferred when mcp-unreal is ready; this bridge is the "
                "required fallback when Cursor Remote Control fails MCP discovery."
            ),
            "toolsets": list_toolsets(),
        })

    if action == "list_tools":
        ts = request.get("toolset")
        return dumps_compact({
            "success": True,
            "via": "rc_bridge",
            "tools": list_tools(str(ts) if ts else None),
        })

    if action == "call":
        return call_tool(
            str(request.get("toolset") or ""),
            str(request.get("tool") or request.get("tool_name") or ""),
            request.get("arguments") if isinstance(request.get("arguments"), dict) else {},
        )

    return error_result(
        "rc_bridge.run_request",
        f"Unknown action {action!r}. Use list_toolsets | list_tools | call",
    )


def _project_saved_dir() -> str:
    try:
        import unreal

        return unreal.Paths.project_saved_dir()
    except Exception:  # noqa: BLE001
        return os.path.join(os.getcwd(), "Saved")


def request_paths() -> tuple[str, str]:
    folder = os.path.join(_project_saved_dir(), "REAgentTools")
    os.makedirs(folder, exist_ok=True)
    return (
        os.path.join(folder, "rc_request.json"),
        os.path.join(folder, "rc_response.json"),
    )


def run_from_files(
    request_path: str | None = None,
    response_path: str | None = None,
) -> str:
    """Read Saved/REAgentTools/rc_request.json, write rc_response.json, return JSON."""
    req_path, resp_path = request_paths()
    if request_path:
        req_path = request_path
    if response_path:
        resp_path = response_path

    if not os.path.isfile(req_path):
        result = error_result(
            "rc_bridge.run_from_files",
            f"Missing request file: {req_path}",
            extra={
                "hint": (
                    "Write JSON to Saved/REAgentTools/rc_request.json then run "
                    "Content/Python/_rc_reagent_exec.py via Unreal Remote Control."
                ),
            },
        )
    else:
        try:
            with open(req_path, "r", encoding="utf-8") as fh:
                request = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            result = error_result(
                "rc_bridge.run_from_files",
                f"Failed to read request JSON: {exc}",
            )
        else:
            result = run_request(request if isinstance(request, dict) else {})

    try:
        with open(resp_path, "w", encoding="utf-8") as fh:
            fh.write(result if isinstance(result, str) else dumps_compact(result))
            fh.write("\n")
    except Exception:  # noqa: BLE001
        pass
    return result
