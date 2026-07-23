"""Project-specific workflow notes (stub — no GAS/enemy systems)."""

from __future__ import annotations

import unreal
import toolset_registry

from re_agent_tools.common.logging import log_tool_call
from re_agent_tools.common.results import WorkflowTimer, error_result, make_request_id, workflow_result


@unreal.uclass()
class REProjectWorkflowTools(unreal.ToolsetDefinition):
    """RE project architecture notes — honest gaps for missing game systems."""

    @toolset_registry.tool_call
    @staticmethod
    def reload_workflow_modules() -> str:
        """Hot-reload REAgentTools Python modules + re-register toolsets (editor stay open)."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            import re_agent_tools
            from re_agent_tools import toolsets

            try:
                toolsets._registration.unregister()
            except Exception as exc:  # noqa: BLE001
                unreal.log_warning(f"[REAgentTools] unregister: {exc}")

            toolset_registry.reload_module(re_agent_tools)
            from re_agent_tools import toolsets as toolsets_reloaded
            from re_agent_tools import __version__ as ver

            ok = bool(toolsets_reloaded._registration.register())
            result = workflow_result(
                "reload_workflow_modules",
                ok,
                f"REAgentTools reloaded v{ver}" if ok else "Reload register failed",
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
                extra={"version": ver, "registered": ok},
            )
            log_tool_call(
                "reload_workflow_modules",
                success=ok,
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )
            return result
        except Exception as exc:  # noqa: BLE001
            return error_result(
                "reload_workflow_modules",
                str(exc),
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )

    @toolset_registry.tool_call
    @staticmethod
    def get_plugin_project_notes() -> str:
        """Return RE project architecture notes and unsupported domains."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        notes = {
            "project": "RE",
            "engine": "5.8",
            "game_cpp_module": False,
            "content_roots": ["/Game", "Content/RE/"],
            "naming": {
                "actors": "RE_ prefix for authored actors",
                "static_meshes": "SM_ prefix",
                "voxel_world": "VW_RE_ prefix",
            },
            "missing_architecture": [
                "GAS (Gameplay Ability System) — not installed; no ability/combat workflow tools",
                "Enemy AI framework — no standardized enemy BP hierarchy",
                "Dungeon procedural generator — manual level craft + voxel caves only",
            ],
            "supported_via_epic_low_level": [
                "SceneTools.find_actors",
                "ObjectTools.get_properties / set_properties",
                "ActorTools transforms and labels",
            ],
            "use_reagenttools_for": [
                "Multi-step spawn + configure + verify",
                "Batch transforms in one transaction",
                "Asset find + bulk edit + save",
                "Blueprint create/defaults/compile chains",
                "Material instance create + assign",
            ],
            "unsupported_graph_authoring": [
                "Niagara system/emitter graph authoring",
                "Animation Blueprint / montage authoring",
                "Landscape sculpt/paint via MCP",
            ],
        }
        result = workflow_result(
            "get_plugin_project_notes",
            True,
            "RE project notes",
            request_id=request_id,
            extra={"notes": notes},
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("get_plugin_project_notes", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
        return result
