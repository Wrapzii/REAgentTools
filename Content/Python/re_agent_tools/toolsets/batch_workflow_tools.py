"""Batch composite workflow executor."""

from __future__ import annotations

import unreal
import toolset_registry

from re_agent_tools.common import limits
from re_agent_tools.common.agent_policy import recovery_block
from re_agent_tools.common.logging import log_tool_call
from re_agent_tools.common.properties import set_properties_and_verify
from re_agent_tools.common.resolution import (
    ResolutionError,
    find_actors_compact,
    resolve_actor_soft,
    resolve_asset,
    suggest_actors,
)
from re_agent_tools.common.results import WorkflowTimer, error_result, make_request_id, workflow_result
from re_agent_tools.common.serialization import parse_json_list, transform_from_json
from re_agent_tools.common.transactions import scoped_transaction
from re_agent_tools.common.validation import compile_blueprint_paths, is_pie_active

ALLOWED_ACTIONS = frozenset({
    "resolve_actor",
    "find_actors",
    "get_editor_context",
    "spawn_actor",
    "set_actor_properties",
    "set_actor_transform",
    "save_level",
    "compile_blueprint",
    "resolve_asset",
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
    # find_actors result: use first hit label if unique
    hits = data.get("actors")
    if isinstance(hits, list) and len(hits) == 1 and isinstance(hits[0], dict):
        return str(hits[0].get("label") or hits[0].get("path") or "")
    raise ValueError(f"$ref {ref} has no label/path")


def _entry_label(entry: dict) -> str:
    """Accept label / actor_label / name (agents mix these)."""
    for key in ("label", "actor_label", "name", "actor"):
        val = entry.get(key)
        if val is not None and str(val).strip():
            return str(val)
    return ""


def _suggested_recovery_ops(failed_query: str, candidates: list[dict]) -> list[dict]:
    """One-shot batch recipe so the agent never needs Epic find_actors."""
    ops: list[dict] = [
        {
            "id": "f1",
            "action": "find_actors",
            "name": failed_query,
        }
    ]
    if candidates:
        exact = candidates[0].get("label") or candidates[0].get("path")
        if exact:
            ops.append({"id": "r1", "action": "resolve_actor", "label": exact})
    else:
        ops.append({"id": "ctx", "action": "get_editor_context"})
    return ops


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
        """Execute allowlisted ops in ONE MCP call. Actions: resolve_actor, find_actors, get_editor_context, spawn_actor, set_actor_properties, set_actor_transform, save_level, compile_blueprint, resolve_asset, set_asset_properties, save_asset. On failure: retry ONCE via this tool with recovery.suggested_ops — NEVER fall back to Epic SceneTools/ActorTools/ObjectTools (token burn). Actor field aliases: label|actor_label|name."""
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
        candidates: list[dict] = []
        failed_query = ""

        def run_op(entry: dict) -> None:
            nonlocal failed_query, candidates
            action = str(entry.get("action", ""))
            step_id = str(entry.get("id", ""))
            if action not in ALLOWED_ACTIONS:
                raise ValueError(
                    f"Action not allowlisted: {action}. "
                    f"Allowed={sorted(ALLOWED_ACTIONS)}. "
                    "Do not fall back to Epic tools — fix the action name and retry this batch."
                )

            if action == "find_actors":
                name = str(entry.get("name") or _entry_label(entry) or "")
                class_name = str(entry.get("class_name") or entry.get("class") or "")
                hits = find_actors_compact(name=name, class_name=class_name)
                step_results[step_id] = {
                    "actors": hits,
                    "count": len(hits),
                    "label": hits[0]["label"] if len(hits) == 1 else "",
                    "path": hits[0]["path"] if len(hits) == 1 else "",
                }
                if not hits:
                    failed_query = name or class_name
                    candidates = suggest_actors(failed_query)
                    raise ResolutionError(
                        f"find_actors returned 0 hits for name={name!r} class={class_name!r}",
                        candidates=candidates,
                    )
                return

            if action == "get_editor_context":
                level_path = ""
                try:
                    les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
                    level = les.get_current_level() if les else None
                    level_path = level.get_outermost().get_name() if level else ""
                except Exception:  # noqa: BLE001
                    level_path = ""
                selected = []
                try:
                    selected = [
                        a.get_actor_label()
                        for a in _eas().get_selected_level_actors()[: limits.SEARCH_LIMIT]
                    ]
                except Exception:  # noqa: BLE001
                    selected = []
                ctx = {
                    "level": level_path,
                    "selected_actors": selected,
                    "pie_active": is_pie_active(),
                }
                step_results[step_id] = {"context": ctx, "label": level_path, "path": level_path}
                return

            if action == "resolve_asset":
                path = _resolve_ref(step_results, str(entry.get("path") or ""))
                if not path:
                    raise ValueError("resolve_asset requires path")
                asset = resolve_asset(path)
                step_results[step_id] = {
                    "path": asset.get_path_name(),
                    "label": asset.get_name(),
                    "class": asset.get_class().get_name(),
                }
                return

            if action == "resolve_actor":
                label = _resolve_ref(step_results, _entry_label(entry))
                if not label:
                    raise ValueError("resolve_actor requires label|actor_label|name")
                failed_query = label
                try:
                    actor, soft_warnings = resolve_actor_soft(label)
                except ResolutionError as exc:
                    candidates = list(getattr(exc, "candidates", None) or suggest_actors(label))
                    raise
                warnings.extend(soft_warnings)
                step_results[step_id] = {
                    "label": actor.get_actor_label(),
                    "path": actor.get_path_name(),
                    "class": actor.get_class().get_name(),
                }
                return

            if action == "spawn_actor":
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
                return

            if action == "set_actor_properties":
                label = _resolve_ref(step_results, _entry_label(entry))
                if not label:
                    raise ValueError("set_actor_properties requires label|actor_label|name")
                failed_query = label
                if dry_run:
                    warnings.append(f"dry_run: set props on {label}")
                    return
                actor, soft_warnings = resolve_actor_soft(label)
                warnings.extend(soft_warnings)
                set_properties_and_verify(actor, str(entry.get("properties_json", "{}")))
                changed.append(actor.get_actor_label())
                return

            if action == "set_actor_transform":
                label = _resolve_ref(step_results, _entry_label(entry))
                if not label:
                    raise ValueError("set_actor_transform requires label|actor_label|name")
                failed_query = label
                if dry_run:
                    warnings.append(f"dry_run: transform {label}")
                    return
                actor, soft_warnings = resolve_actor_soft(label)
                warnings.extend(soft_warnings)
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
                changed.append(actor.get_actor_label())
                return

            if action == "save_level":
                if dry_run:
                    warnings.append("dry_run: save_level")
                    return
                unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
                saved.append("current_level")
                return

            if action == "compile_blueprint":
                path = _resolve_ref(step_results, str(entry["path"]))
                if dry_run:
                    warnings.append(f"dry_run: compile {path}")
                    return
                done, errs = compile_blueprint_paths([path])
                compiled.extend(done)
                errors.extend(errs)
                return

            if action == "set_asset_properties":
                path = _resolve_ref(step_results, str(entry["path"]))
                if dry_run:
                    warnings.append(f"dry_run: set asset props {path}")
                    return
                asset = resolve_asset(path)
                set_properties_and_verify(asset, str(entry.get("properties_json", "{}")))
                changed.append(path)
                return

            if action == "save_asset":
                path = _resolve_ref(step_results, str(entry["path"]))
                if dry_run:
                    warnings.append(f"dry_run: save {path}")
                    return
                if _eas_assets().save_asset(path):
                    saved.append(path)
                return

            raise ValueError(f"Unhandled allowlisted action: {action}")

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
                        if isinstance(exc, ResolutionError):
                            more = list(getattr(exc, "candidates", None) or [])
                            if more:
                                candidates = more
                            elif failed_query and not candidates:
                                candidates = suggest_actors(failed_query)
                        if stop_on_error:
                            break
        except Exception as exc:  # noqa: BLE001
            errors.append(str(exc))

        suggested_ops = (
            _suggested_recovery_ops(failed_query or "PlayerStart", candidates)
            if errors
            else []
        )
        recovery = recovery_block(
            operation="execute_editor_batch",
            errors=errors,
            candidates=candidates,
            suggested_ops=suggested_ops,
        ) if errors else None

        extra = {
            "dry_run": dry_run,
            "step_results": step_results,
            "allowed_actions": sorted(ALLOWED_ACTIONS),
        }
        if recovery:
            extra["recovery"] = recovery
            extra["candidates"] = candidates[:15]
            extra["suggested_ops"] = suggested_ops

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
            extra=extra,
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("execute_editor_batch", success=not errors, request_id=request_id, duration_ms=timer.elapsed_ms)
        return result
