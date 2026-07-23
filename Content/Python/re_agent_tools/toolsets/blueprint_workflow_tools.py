"""Blueprint composite workflow tools."""

from __future__ import annotations

import unreal
import toolset_registry
from toolset_registry.helpers import compile_blueprint

from re_agent_tools.common import limits
from re_agent_tools.common.blueprint_helpers import (
    create_blueprint_asset,
    get_blueprint_parent_name,
)
from re_agent_tools.common.logging import log_tool_call
from re_agent_tools.common.properties import get_properties_compact, set_properties_and_verify
from re_agent_tools.common.resolution import ResolutionError, asset_ref, resolve_asset
from re_agent_tools.common.results import WorkflowTimer, error_result, make_request_id, workflow_result
from re_agent_tools.common.serialization import parse_json_list
from re_agent_tools.common.validation import compile_blueprint_paths


def _eas() -> unreal.EditorAssetSubsystem:
    return unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)


@unreal.uclass()
class REBlueprintWorkflowTools(unreal.ToolsetDefinition):
    """RE composite Blueprint workflows: inspect, create, defaults, compile."""

    @toolset_registry.tool_call
    @staticmethod
    def inspect_blueprint_compact(blueprint_path: str, properties_json: str = "[]") -> str:
        """Compact Blueprint inspect: class, parent, optional default properties."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            bp = resolve_asset(blueprint_path)
            if not isinstance(bp, unreal.Blueprint):
                raise ResolutionError(f"Not a Blueprint: {blueprint_path}")
            props = [str(p) for p in parse_json_list(properties_json, field_name="properties_json")]
            gen_class = bp.generated_class()
            info = {
                "path": blueprint_path,
                "parent_class": get_blueprint_parent_name(bp),
                "generated_class": gen_class.get_name() if gen_class else "",
                "properties": get_properties_compact(gen_class, props) if props and gen_class else {},
            }
            result = workflow_result(
                "inspect_blueprint_compact",
                True,
                f"Inspected {blueprint_path}",
                request_id=request_id,
                resolved_targets=[asset_ref(blueprint_path)],
                extra={"blueprint": info},
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("inspect_blueprint_compact", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except (ResolutionError, ValueError) as exc:
            return error_result("inspect_blueprint_compact", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms)

    @toolset_registry.tool_call
    @staticmethod
    def create_blueprint_from_class(
        folder_path: str,
        asset_name: str,
        parent_class_path: str,
    ) -> str:
        """Create Blueprint asset from parent class path."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        full_path = f"{folder_path.rstrip('/')}/{asset_name}"
        if _eas().does_asset_exist(full_path):
            return error_result(
                "create_blueprint_from_class",
                f"Asset exists: {full_path}",
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )
        parent = unreal.load_class(None, parent_class_path)
        if not parent:
            return error_result(
                "create_blueprint_from_class",
                f"Parent class not found: {parent_class_path}",
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )
        try:
            create_blueprint_asset(folder_path, asset_name, parent)
        except (ResolutionError, ValueError) as exc:
            return error_result(
                "create_blueprint_from_class",
                str(exc),
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )
        result = workflow_result(
            "create_blueprint_from_class",
            True,
            f"Created {full_path}",
            request_id=request_id,
            created=[full_path],
            compiled=[full_path],
            duration_ms=timer.elapsed_ms,
            extra={"parent_class": parent_class_path},
        )
        log_tool_call("create_blueprint_from_class", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
        return result

    @toolset_registry.tool_call
    @staticmethod
    def set_class_defaults_compile_save(
        blueprint_path: str,
        properties_json: str,
    ) -> str:
        """Set Blueprint class defaults, compile, save."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            bp = resolve_asset(blueprint_path)
            if not isinstance(bp, unreal.Blueprint):
                raise ResolutionError(f"Not a Blueprint: {blueprint_path}")
            gen_class = bp.generated_class()
            ok, readback, warnings = set_properties_and_verify(gen_class, properties_json)
            compile_blueprint(bp)
            _eas().save_asset(blueprint_path)
            result = workflow_result(
                "set_class_defaults_compile_save",
                ok,
                f"Updated defaults for {blueprint_path}",
                request_id=request_id,
                changed=[blueprint_path],
                compiled=[blueprint_path],
                saved=[blueprint_path],
                warnings=warnings,
                extra={"readback": readback},
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("set_class_defaults_compile_save", success=ok, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except (ResolutionError, ValueError) as exc:
            return error_result("set_class_defaults_compile_save", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms)

    @toolset_registry.tool_call
    @staticmethod
    def compile_blueprints(blueprint_paths_json: str) -> str:
        """Compile list of Blueprint asset paths."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        paths = [str(p) for p in parse_json_list(blueprint_paths_json, field_name="blueprint_paths_json")]
        compiled, errors = compile_blueprint_paths(paths[: limits.MUTATE_LIMIT])
        result = workflow_result(
            "compile_blueprints",
            bool(compiled) and not errors,
            f"Compiled {len(compiled)} Blueprints",
            request_id=request_id,
            compiled=compiled,
            errors=errors,
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("compile_blueprints", success=bool(compiled), request_id=request_id, duration_ms=timer.elapsed_ms)
        return result

    @toolset_registry.tool_call
    @staticmethod
    def create_or_update_blueprint(
        folder_path: str,
        asset_name: str,
        parent_class_path: str,
        properties_json: str = "{}",
    ) -> str:
        """Idempotent: create Blueprint if missing else update class defaults."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        full_path = f"{folder_path.rstrip('/')}/{asset_name}"
        created: list[str] = []
        changed: list[str] = []
        if _eas().does_asset_exist(full_path):
            result_str = REBlueprintWorkflowTools.set_class_defaults_compile_save(
                full_path, properties_json
            )
            return result_str
        result_str = REBlueprintWorkflowTools.create_blueprint_from_class(
            folder_path, asset_name, parent_class_path
        )
        if properties_json.strip() not in ("", "{}"):
            REBlueprintWorkflowTools.set_class_defaults_compile_save(full_path, properties_json)
            changed.append(full_path)
        return result_str
