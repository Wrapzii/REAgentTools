"""Asset composite workflow tools."""

from __future__ import annotations

import unreal
import toolset_registry

from re_agent_tools.common import limits
from re_agent_tools.common.logging import log_tool_call
from re_agent_tools.common.properties import set_properties_and_verify
from re_agent_tools.common.resolution import ResolutionError, asset_ref, find_assets, resolve_asset
from re_agent_tools.common.results import WorkflowTimer, error_result, make_request_id, workflow_result
from re_agent_tools.common.serialization import parse_json_list


def _eas() -> unreal.EditorAssetSubsystem:
    return unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)


@unreal.uclass()
class REAssetWorkflowTools(unreal.ToolsetDefinition):
    """RE composite asset workflows: find, bulk edit, save."""

    @toolset_registry.tool_call
    @staticmethod
    def find_assets_compact(
        path: str = "/Game",
        class_name: str = "",
        name_filter: str = "",
        limit: int = 25,
    ) -> str:
        """Find assets under path with optional class/name filters (max 25)."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        cap = min(limit, limits.SEARCH_LIMIT)
        assets, truncated, msg = find_assets(
            path=path, class_name=class_name, name_filter=name_filter, limit=cap
        )
        warnings = [msg] if msg else []
        result = workflow_result(
            "find_assets_compact",
            True,
            f"Found {len(assets)} assets",
            request_id=request_id,
            resolved_targets=[asset_ref(p) for p in assets],
            warnings=warnings,
            truncated=truncated,
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("find_assets_compact", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
        return result

    @toolset_registry.tool_call
    @staticmethod
    def bulk_edit_asset_properties_and_save(edits_json: str) -> str:
        """Bulk edit assets: JSON list of {path, properties_json}."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        items = parse_json_list(edits_json, field_name="edits_json")
        changed: list[str] = []
        saved: list[str] = []
        errors: list[str] = []
        for entry in items[: limits.MUTATE_LIMIT]:
            if not isinstance(entry, dict) or "path" not in entry:
                errors.append(f"Invalid entry: {entry}")
                continue
            path = str(entry["path"])
            props = str(entry.get("properties_json", "{}"))
            try:
                asset = resolve_asset(path)
                ok, _, warnings = set_properties_and_verify(asset, props)
                if warnings:
                    errors.extend(warnings)
                if ok:
                    changed.append(path)
                    if _eas().save_asset(path):
                        saved.append(path)
            except (ResolutionError, ValueError) as exc:
                errors.append(f"{path}: {exc}")
        result = workflow_result(
            "bulk_edit_asset_properties_and_save",
            bool(changed) and not errors,
            f"Edited {len(changed)} assets",
            request_id=request_id,
            changed=changed,
            saved=saved,
            errors=errors,
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("bulk_edit_asset_properties_and_save", success=bool(changed), request_id=request_id, duration_ms=timer.elapsed_ms)
        return result

    @toolset_registry.tool_call
    @staticmethod
    def save_assets(asset_paths_json: str) -> str:
        """Save exact asset path list only."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        paths = [str(p) for p in parse_json_list(asset_paths_json, field_name="asset_paths_json")]
        saved: list[str] = []
        errors: list[str] = []
        for path in paths[: limits.MUTATE_LIMIT]:
            try:
                resolve_asset(path)
                if _eas().save_asset(path):
                    saved.append(path)
                else:
                    errors.append(f"Save failed: {path}")
            except ResolutionError as exc:
                errors.append(str(exc))
        result = workflow_result(
            "save_assets",
            bool(saved) and not errors,
            f"Saved {len(saved)} assets",
            request_id=request_id,
            saved=saved,
            errors=errors,
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("save_assets", success=bool(saved), request_id=request_id, duration_ms=timer.elapsed_ms)
        return result
