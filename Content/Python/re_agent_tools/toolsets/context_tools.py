"""Context and target resolution tools."""

from __future__ import annotations

import unreal
import toolset_registry

from re_agent_tools.common import limits
from re_agent_tools.common.logging import log_tool_call
from re_agent_tools.common.properties import get_properties_compact
from re_agent_tools.common.resolution import (
    AmbiguousResolutionError,
    ResolutionError,
    actor_ref,
    asset_ref,
    resolve_actor,
    resolve_asset,
    resolve_targets as resolve_targets_impl,
)
from re_agent_tools.common.results import WorkflowTimer, error_result, make_request_id, workflow_result
from re_agent_tools.common.serialization import parse_json_list
from re_agent_tools.common.validation import get_dirty_packages, is_pie_active


@unreal.uclass()
class REContextTools(unreal.ToolsetDefinition):
    """RE composite context: capabilities, editor state, target resolution, compact inspect."""

    @toolset_registry.tool_call
    @staticmethod
    def get_plugin_capabilities() -> str:
        """Return REAgentTools version and registered workflow toolsets."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        caps = {
            "plugin": "REAgentTools",
            "version": "1.1.0",
            "toolsets": [
                "REContextTools",
                "REActorWorkflowTools",
                "REAnimWorkflowTools",
                "REAssetWorkflowTools",
                "REBlueprintWorkflowTools",
                "REMaterialWorkflowTools",
                "RELevelWorkflowTools",
                "REValidationWorkflowTools",
                "REBatchWorkflowTools",
                "REProjectWorkflowTools",
            ],
            "limits": {
                "search": limits.SEARCH_LIMIT,
                "mutate": limits.MUTATE_LIMIT,
                "batch": limits.BATCH_LIMIT,
            },
        }
        result = workflow_result(
            "get_plugin_capabilities",
            True,
            "REAgentTools capabilities",
            request_id=request_id,
            extra={"capabilities": caps},
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("get_plugin_capabilities", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
        return result

    @toolset_registry.tool_call
    @staticmethod
    def get_editor_context(
        include_level: bool = True,
        include_selection: bool = True,
        include_pie: bool = True,
        include_dirty: bool = True,
    ) -> str:
        """Return compact editor context sections as JSON."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        ctx: dict = {}
        warnings: list[str] = []

        if include_level:
            try:
                les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
                level = les.get_current_level()
                ctx["level"] = (
                    level.get_outermost().get_name() if level else ""
                )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"level: {exc}")

        if include_selection:
            try:
                selected = unreal.EditorUtilityLibrary.get_selected_assets()
                ctx["selected_assets"] = [
                    a.get_path_name() for a in selected[: limits.SEARCH_LIMIT]
                ]
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"selected_assets: {exc}")
                ctx["selected_assets"] = []
            try:
                eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
                actors = eas.get_selected_level_actors() if eas else []
                ctx["selected_actors"] = [
                    a.get_actor_label() for a in actors[: limits.SEARCH_LIMIT]
                ]
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"selected_actors: {exc}")
                ctx["selected_actors"] = []

        if include_pie:
            ctx["pie_active"] = is_pie_active()

        if include_dirty:
            dirty = get_dirty_packages()
            ctx["dirty_packages"] = dirty[: limits.SEARCH_LIMIT]
            ctx["dirty_count"] = len(dirty)

        result = workflow_result(
            "get_editor_context",
            True,
            "Editor context snapshot",
            request_id=request_id,
            extra={"context": ctx},
            warnings=warnings,
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("get_editor_context", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
        return result

    @toolset_registry.tool_call
    @staticmethod
    def resolve_targets(
        actor_queries_json: str = "[]",
        asset_paths_json: str = "[]",
    ) -> str:
        """Resolve actors by label/path and assets by path. Ambiguous → error with candidates."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            actor_queries = [str(x) for x in parse_json_list(actor_queries_json, field_name="actor_queries_json")]
            asset_paths = [str(x) for x in parse_json_list(asset_paths_json, field_name="asset_paths_json")]
            data = resolve_targets_impl(actor_queries=actor_queries or None, asset_paths=asset_paths or None)
            result = workflow_result(
                "resolve_targets",
                True,
                f"Resolved {len(data['targets'])} targets",
                request_id=request_id,
                resolved_targets=data["targets"],
                warnings=data["warnings"],
                truncated=data["truncated"],
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("resolve_targets", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except (ResolutionError, AmbiguousResolutionError) as exc:
            extra = {}
            if isinstance(exc, AmbiguousResolutionError):
                extra["candidates"] = exc.candidates
            result = error_result(
                "resolve_targets",
                str(exc),
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
                extra=extra,
            )
            log_tool_call("resolve_targets", success=False, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result

    @toolset_registry.tool_call
    @staticmethod
    def inspect_targets_compact(
        actor_queries_json: str = "[]",
        asset_paths_json: str = "[]",
        properties_json: str = "[]",
    ) -> str:
        """Inspect explicit property list on resolved actors/assets only."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        properties = [str(p) for p in parse_json_list(properties_json, field_name="properties_json")]
        actor_queries = [str(x) for x in parse_json_list(actor_queries_json, field_name="actor_queries_json")]
        asset_paths = [str(x) for x in parse_json_list(asset_paths_json, field_name="asset_paths_json")]

        inspected: list[dict] = []
        errors: list[str] = []
        for q in actor_queries[: limits.SEARCH_LIMIT]:
            try:
                actor = resolve_actor(q)
                inspected.append(
                    {
                        **actor_ref(actor),
                        "properties": get_properties_compact(actor, properties) if properties else {},
                    }
                )
            except ResolutionError as exc:
                errors.append(str(exc))

        for path in asset_paths[: limits.SEARCH_LIMIT]:
            try:
                asset = resolve_asset(path)
                inspected.append(
                    {
                        **asset_ref(path),
                        "properties": get_properties_compact(asset, properties) if properties else {},
                    }
                )
            except ResolutionError as exc:
                errors.append(str(exc))

        result = workflow_result(
            "inspect_targets_compact",
            not errors or bool(inspected),
            f"Inspected {len(inspected)} targets",
            request_id=request_id,
            resolved_targets=inspected,
            errors=errors,
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call(
            "inspect_targets_compact",
            success=not errors or bool(inspected),
            request_id=request_id,
            duration_ms=timer.elapsed_ms,
        )
        return result
