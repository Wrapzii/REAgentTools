"""Validation composite workflow tools."""

from __future__ import annotations

import unreal
import toolset_registry

from re_agent_tools.common.logging import log_tool_call
from re_agent_tools.common.results import WorkflowTimer, make_request_id, workflow_result
from re_agent_tools.common.serialization import parse_json_list
from re_agent_tools.common.validation import (
    compile_blueprint_paths,
    get_dirty_packages,
    get_recent_log_errors,
    run_map_check,
)


def _eas() -> unreal.EditorAssetSubsystem:
    return unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)


@unreal.uclass()
class REValidationWorkflowTools(unreal.ToolsetDefinition):
    """RE composite validation: compile/save/validate bundles."""

    @toolset_registry.tool_call
    @staticmethod
    def compile_save_validate(blueprint_paths_json: str = "[]", save_all_dirty: bool = False) -> str:
        """Compile Blueprints, optionally save all dirty packages."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        paths = [str(p) for p in parse_json_list(blueprint_paths_json, field_name="blueprint_paths_json")]
        compiled, errors = compile_blueprint_paths(paths) if paths else ([], [])
        saved: list[str] = []
        if save_all_dirty:
            unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
            saved = get_dirty_packages()
        result = workflow_result(
            "compile_save_validate",
            not errors,
            f"Compiled {len(compiled)} Blueprints",
            request_id=request_id,
            compiled=compiled,
            saved=saved,
            errors=errors,
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("compile_save_validate", success=not errors, request_id=request_id, duration_ms=timer.elapsed_ms)
        return result

    @toolset_registry.tool_call
    @staticmethod
    def get_recent_errors_compact(max_lines: int = 25) -> str:
        """Best-effort recent errors — honest unsupported without log API."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        info = get_recent_log_errors(max_lines)
        result = workflow_result(
            "get_recent_errors_compact",
            False,
            "Log tail not available via Python API",
            request_id=request_id,
            extra={"log": info},
            warnings=[info.get("note", "")],
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("get_recent_errors_compact", success=False, request_id=request_id, duration_ms=timer.elapsed_ms)
        return result

    @toolset_registry.tool_call
    @staticmethod
    def run_validation_bundle(
        blueprint_paths_json: str = "[]",
        run_map_check_flag: bool = True,
        save_dirty: bool = False,
    ) -> str:
        """Run compile + optional map check + optional save dirty."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        paths = [str(p) for p in parse_json_list(blueprint_paths_json, field_name="blueprint_paths_json")]
        compiled, compile_errors = compile_blueprint_paths(paths) if paths else ([], [])
        map_info = run_map_check() if run_map_check_flag else {"skipped": True}
        saved: list[str] = []
        if save_dirty:
            unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
        warnings: list[str] = []
        if not map_info.get("ran"):
            warnings.append(map_info.get("note", "map check skipped"))
        result = workflow_result(
            "run_validation_bundle",
            not compile_errors,
            "Validation bundle complete",
            request_id=request_id,
            compiled=compiled,
            saved=saved,
            errors=compile_errors,
            warnings=warnings,
            extra={"map_check": map_info},
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("run_validation_bundle", success=not compile_errors, request_id=request_id, duration_ms=timer.elapsed_ms)
        return result
