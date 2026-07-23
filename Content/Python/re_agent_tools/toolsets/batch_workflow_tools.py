"""Batch composite workflow executor."""

from __future__ import annotations

import unreal
import toolset_registry

from re_agent_tools.common import limits
from re_agent_tools.common.logging import log_tool_call
from re_agent_tools.common.properties import set_properties_and_verify
from re_agent_tools.common.resolution import ResolutionError, resolve_actor, resolve_asset
from re_agent_tools.common.results import WorkflowTimer, error_result, make_request_id, workflow_result
from re_agent_tools.common.serialization import parse_json, parse_json_list, transform_from_json
from re_agent_tools.common.transactions import scoped_transaction
from re_agent_tools.common.validation import compile_blueprint_paths

ALLOWED_ACTIONS = frozenset({
    "resolve_actor",
    "spawn_actor",
    "set_actor_properties",
    "set_actor_transform",
    "save_level",
    "compile_blueprint",
    "set_asset_properties",
    "save_asset",
})


def _eas() -> unreal.EditorActorSubsystem:
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def _eas_assets() -> unreal.EditorAssetSubsystem:
    return unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)


def _resolve_ref(step_results: dict[str, dict], ref: str) -> str:
    if not ref.startswith("$"):
        return ref
    step_id = ref[1:]
    if step_id not in step_results:
        raise ValueError(f"Unknown $ref: {ref}")
    data = step_results[step_id]
    if "label" in data:
        return data["label"]
    if "path" in data:
        return data["path"]
    raise ValueError(f"$ref {ref} has no label/path")


def _entry_label(entry: dict) -> str:
    """Accept label / actor_label / name (agents mix these)."""
    for key in ("label", "actor_label", "name", "actor"):
        val = entry.get(key)
        if val is not None and str(val).strip():
            return str(val)
    return ""


@unreal.uclass()
class REBatchWorkflowTools(unreal.ToolsetDefinition):
    """RE batch executor for allowlisted editor operations with $ref chaining."""

    @toolset_registry.tool_call
    @staticmethod
    def execute_editor_batch(
        operations_json: str,
        dry_run: bool = False,
        stop_on_error: bool = True,
    ) -> str:
        """Execute allowlisted ops from JSON array string. Actions: resolve_actor, spawn_actor, set_actor_properties, set_actor_transform, save_level, compile_blueprint, set_asset_properties, save_asset. Actor field aliases: label|actor_label|name."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        ops = parse_json_list(operations_json, field_name="operations_json")
        if len(ops) > limits.BATCH_LIMIT:
            return error_result(
                "execute_editor_batch",
                f"Batch limit {limits.BATCH_LIMIT} exceeded ({len(ops)} ops)",
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )

        step_results: dict[str, dict] = {}
        changed: list[str] = []
        created: list[str] = []
        compiled: list[str] = []
        saved: list[str] = []
        errors: list[str] = []
        warnings: list[str] = []

        def run_op(entry: dict) -> None:
            action = str(entry.get("action", ""))
            step_id = str(entry.get("id", ""))
            if action not in ALLOWED_ACTIONS:
                raise ValueError(f"Action not allowlisted: {action}")

            if action == "resolve_actor":
                label = _resolve_ref(step_results, _entry_label(entry))
                if not label:
                    raise ValueError("resolve_actor requires label|actor_label|name")
                actor = resolve_actor(label)
                step_results[step_id] = {"label": actor.get_actor_label(), "path": actor.get_path_name()}
            elif action == "spawn_actor":
                if dry_run:
                    warnings.append(f"dry_run: would spawn {entry.get('class_path')}")
                    return
                cls = unreal.load_class(None, str(entry["class_path"]))
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
                label = _entry_label(entry) or actor.get_actor_label()
                actor.set_actor_label(label)
                created.append(label)
                step_results[step_id] = {"label": label}
            elif action == "set_actor_properties":
                label = _resolve_ref(step_results, _entry_label(entry))
                if not label:
                    raise ValueError("set_actor_properties requires label|actor_label|name")
                if dry_run:
                    warnings.append(f"dry_run: set props on {label}")
                    return
                actor = resolve_actor(label)
                set_properties_and_verify(actor, str(entry.get("properties_json", "{}")))
                changed.append(label)
            elif action == "set_actor_transform":
                label = _resolve_ref(step_results, _entry_label(entry))
                if not label:
                    raise ValueError("set_actor_transform requires label|actor_label|name")
                if dry_run:
                    warnings.append(f"dry_run: transform {label}")
                    return
                actor = resolve_actor(label)
                # Accept transform_json or inline location/rotation/scale
                raw_xform = entry.get("transform_json")
                if raw_xform is not None:
                    data = transform_from_json(str(raw_xform))
                else:
                    data = {}
                    if "location" in entry:
                        data["location"] = entry["location"]
                    if "rotation" in entry:
                        data["rotation"] = entry["rotation"]
                    if "scale" in entry:
                        data["scale"] = entry["scale"]
                if "location" in data:
                    loc = data["location"]
                    actor.set_actor_location(unreal.Vector(loc[0], loc[1], loc[2]), False, False)
                if "rotation" in data:
                    rot = data["rotation"]
                    actor.set_actor_rotation(unreal.Rotator(rot[0], rot[1], rot[2]), False)
                if "scale" in data:
                    sc = data["scale"]
                    actor.set_actor_scale3d(unreal.Vector(sc[0], sc[1], sc[2]))
                changed.append(label)
            elif action == "save_level":
                if dry_run:
                    warnings.append("dry_run: save_level")
                    return
                unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
                saved.append("current_level")
            elif action == "compile_blueprint":
                path = _resolve_ref(step_results, str(entry["path"]))
                if dry_run:
                    warnings.append(f"dry_run: compile {path}")
                    return
                done, errs = compile_blueprint_paths([path])
                compiled.extend(done)
                errors.extend(errs)
            elif action == "set_asset_properties":
                path = _resolve_ref(step_results, str(entry["path"]))
                if dry_run:
                    warnings.append(f"dry_run: set asset props {path}")
                    return
                asset = resolve_asset(path)
                set_properties_and_verify(asset, str(entry.get("properties_json", "{}")))
                changed.append(path)
            elif action == "save_asset":
                path = _resolve_ref(step_results, str(entry["path"]))
                if dry_run:
                    warnings.append(f"dry_run: save {path}")
                    return
                if _eas_assets().save_asset(path):
                    saved.append(path)

        try:
            with scoped_transaction("RE execute_editor_batch"):
                for entry in ops:
                    if not isinstance(entry, dict):
                        errors.append(f"Invalid op: {entry}")
                        if stop_on_error:
                            break
                        continue
                    try:
                        run_op(entry)
                    except (ResolutionError, ValueError) as exc:
                        errors.append(str(exc))
                        if stop_on_error:
                            break
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))

        result = workflow_result(
            "execute_editor_batch",
            not errors,
            f"Batch {len(ops)} ops, {len(errors)} errors",
            request_id=request_id,
            changed=changed,
            created=created,
            compiled=compiled,
            saved=saved,
            warnings=warnings,
            errors=errors,
            extra={"dry_run": dry_run, "step_results": step_results},
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("execute_editor_batch", success=not errors, request_id=request_id, duration_ms=timer.elapsed_ms)
        return result
