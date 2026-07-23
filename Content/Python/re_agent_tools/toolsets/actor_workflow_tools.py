"""Actor composite workflow tools."""

from __future__ import annotations

import unreal
import toolset_registry
from toolset_registry.helpers import require_editable

from re_agent_tools.common import limits
from re_agent_tools.common.logging import log_tool_call
from re_agent_tools.common.properties import set_properties_and_verify
from re_agent_tools.common.resolution import ResolutionError, actor_ref, resolve_actor, resolve_actors
from re_agent_tools.common.results import WorkflowTimer, error_result, make_request_id, workflow_result
from re_agent_tools.common.serialization import parse_json, parse_json_list, transform_from_json
from re_agent_tools.common.spawn_helpers import rotator_from_list, spawn_actor_from_class, vector_from_list
from re_agent_tools.common.transactions import scoped_transaction


def _eas() -> unreal.EditorActorSubsystem:
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def _apply_transform(actor: unreal.Actor, transform_json: str) -> None:
    data = transform_from_json(transform_json)
    if "location" in data:
        loc = data["location"]
        actor.set_actor_location(
            unreal.Vector(loc[0], loc[1], loc[2]),
            False,
            False,
        )
    if "rotation" in data:
        rot = data["rotation"]
        actor.set_actor_rotation(unreal.Rotator(rot[0], rot[1], rot[2]), False)
    if "scale" in data:
        sc = data["scale"]
        actor.set_actor_scale3d(unreal.Vector(sc[0], sc[1], sc[2]))


@unreal.uclass()
class REActorWorkflowTools(unreal.ToolsetDefinition):
    """RE composite actor workflows: set/verify, spawn, batch transform, delete, organize."""

    @toolset_registry.tool_call
    @staticmethod
    def set_actor_properties_and_verify(
        actor_label: str,
        properties_json: str,
        save_level: bool = False,
    ) -> str:
        """Set actor properties from JSON, read back, optional save level."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            actor = resolve_actor(actor_label)
            with scoped_transaction(f"RE set props {actor_label}"):
                ok, readback, warnings = set_properties_and_verify(actor, properties_json)
            saved: list[str] = []
            if save_level:
                unreal.EditorLoadingAndSavingUtils.save_dirty_packages(True, True)
                saved.append("current_level")
            result = workflow_result(
                "set_actor_properties_and_verify",
                ok,
                f"Set properties on {actor_label}",
                request_id=request_id,
                resolved_targets=[actor_ref(actor)],
                changed=[actor_label],
                saved=saved,
                warnings=warnings,
                extra={"readback": readback},
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("set_actor_properties_and_verify", success=ok, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except (ResolutionError, ValueError) as exc:
            return error_result("set_actor_properties_and_verify", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms)

    @toolset_registry.tool_call
    @staticmethod
    def set_component_properties_and_verify(
        actor_label: str,
        component_name: str,
        properties_json: str,
    ) -> str:
        """Set properties on a named actor component with readback."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            actor = resolve_actor(actor_label)
            from re_agent_tools.common.component_helpers import get_actor_component_by_name

            component = get_actor_component_by_name(actor, component_name)
            with scoped_transaction(f"RE set component {component_name}"):
                ok, readback, warnings = set_properties_and_verify(component, properties_json)
            result = workflow_result(
                "set_component_properties_and_verify",
                ok,
                f"Set {component_name} on {actor_label}",
                request_id=request_id,
                changed=[f"{actor_label}.{component_name}"],
                warnings=warnings,
                extra={"readback": readback},
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("set_component_properties_and_verify", success=ok, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except (ResolutionError, ValueError) as exc:
            return error_result("set_component_properties_and_verify", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms)

    @toolset_registry.tool_call
    @staticmethod
    def spawn_configure_attach_and_verify(
        class_path: str,
        location_json: str = "[0,0,0]",
        rotation_json: str = "[0,0,0]",
        scale_json: str = "[1,1,1]",
        properties_json: str = "{}",
        actor_label: str = "",
        folder_path: str = "",
        attach_parent_label: str = "",
    ) -> str:
        """Spawn actor from class, set transform/props, optional attach and label."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            actor_class = unreal.load_class(None, class_path)
            if not actor_class:
                raise ValueError(f"Class not found: {class_path}")
            loc = parse_json(location_json, field_name="location")
            rot = parse_json(rotation_json, field_name="rotation")
            sc = parse_json(scale_json, field_name="scale")
            with scoped_transaction("RE spawn configure"):
                actor = spawn_actor_from_class(
                    actor_class,
                    vector_from_list(loc),
                    rotator_from_list(rot),
                    vector_from_list(sc),
                )
                if actor_label:
                    actor.set_actor_label(actor_label)
                if folder_path:
                    actor.set_folder_path(unreal.Name(folder_path))
                if properties_json and properties_json.strip() not in ("", "{}"):
                    set_properties_and_verify(actor, properties_json)
                if attach_parent_label:
                    parent = resolve_actor(attach_parent_label)
                    actor.attach_to_actor(
                        parent,
                        unreal.Name(""),
                        unreal.AttachmentRule.KEEP_WORLD,
                        unreal.AttachmentRule.KEEP_WORLD,
                        unreal.AttachmentRule.KEEP_WORLD,
                        False,
                    )
            result = workflow_result(
                "spawn_configure_attach_and_verify",
                True,
                f"Spawned {actor.get_actor_label()}",
                request_id=request_id,
                created=[actor.get_actor_label()],
                resolved_targets=[actor_ref(actor)],
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("spawn_configure_attach_and_verify", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except (ResolutionError, ValueError) as exc:
            return error_result("spawn_configure_attach_and_verify", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms)

    @toolset_registry.tool_call
    @staticmethod
    def batch_transform_actors(transforms_json: str) -> str:
        """Batch transform actors: JSON list of {label, location, rotation, scale}."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        items = parse_json_list(transforms_json, field_name="transforms_json")
        changed: list[str] = []
        errors: list[str] = []
        with scoped_transaction("RE batch transform"):
            for entry in items[: limits.MUTATE_LIMIT]:
                if not isinstance(entry, dict) or "label" not in entry:
                    errors.append(f"Invalid entry: {entry}")
                    continue
                try:
                    actor = resolve_actor(str(entry["label"]))
                    require_editable(actor)
                    if "location" in entry:
                        loc = entry["location"]
                        actor.set_actor_location(unreal.Vector(loc[0], loc[1], loc[2]), False, False)
                    if "rotation" in entry:
                        rot = entry["rotation"]
                        actor.set_actor_rotation(unreal.Rotator(rot[0], rot[1], rot[2]), False)
                    if "scale" in entry:
                        sc = entry["scale"]
                        actor.set_actor_scale3d(unreal.Vector(sc[0], sc[1], sc[2]))
                    changed.append(entry["label"])
                except ResolutionError as exc:
                    errors.append(str(exc))
        result = workflow_result(
            "batch_transform_actors",
            bool(changed) and not errors,
            f"Transformed {len(changed)} actors",
            request_id=request_id,
            changed=changed,
            errors=errors,
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("batch_transform_actors", success=bool(changed), request_id=request_id, duration_ms=timer.elapsed_ms)
        return result

    @toolset_registry.tool_call
    @staticmethod
    def delete_actors_validated(actor_labels_json: str, dry_run: bool = True) -> str:
        """Delete actors by label. dry_run defaults true."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        labels = [str(x) for x in parse_json_list(actor_labels_json, field_name="actor_labels_json")]
        to_delete: list[unreal.Actor] = []
        errors: list[str] = []
        for label in labels[: limits.MUTATE_LIMIT]:
            try:
                to_delete.append(resolve_actor(label))
            except ResolutionError as exc:
                errors.append(str(exc))

        deleted_labels = [a.get_actor_label() for a in to_delete]
        if not dry_run and to_delete:
            with scoped_transaction("RE delete actors"):
                _eas().destroy_actors(to_delete)

        result = workflow_result(
            "delete_actors_validated",
            not errors,
            f"{'Would delete' if dry_run else 'Deleted'} {len(deleted_labels)} actors",
            request_id=request_id,
            deleted=deleted_labels if not dry_run else [],
            warnings=[f"dry_run: would delete {deleted_labels}"] if dry_run else [],
            errors=errors,
            extra={"dry_run": dry_run, "candidates": deleted_labels},
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("delete_actors_validated", success=not errors, request_id=request_id, duration_ms=timer.elapsed_ms)
        return result

    @toolset_registry.tool_call
    @staticmethod
    def organize_actors(organization_json: str) -> str:
        """Organize actors: JSON list of {label, folder?, new_label?, add_tags?, remove_tags?}."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        items = parse_json_list(organization_json, field_name="organization_json")
        changed: list[str] = []
        errors: list[str] = []
        with scoped_transaction("RE organize actors"):
            for entry in items[: limits.MUTATE_LIMIT]:
                if not isinstance(entry, dict) or "label" not in entry:
                    errors.append(f"Invalid entry: {entry}")
                    continue
                try:
                    actor = resolve_actor(str(entry["label"]))
                    require_editable(actor)
                    if entry.get("folder") is not None:
                        actor.set_folder_path(unreal.Name(str(entry["folder"])))
                    if entry.get("new_label"):
                        actor.set_actor_label(str(entry["new_label"]))
                    for tag in entry.get("add_tags", []):
                        if not actor.actor_has_tag(unreal.Name(tag)):
                            tags = list(actor.tags)
                            tags.append(unreal.Name(tag))
                            actor.tags = tags
                    for tag in entry.get("remove_tags", []):
                        actor.tags = [t for t in actor.tags if t != unreal.Name(tag)]
                    changed.append(entry.get("new_label") or entry["label"])
                except ResolutionError as exc:
                    errors.append(str(exc))
        result = workflow_result(
            "organize_actors",
            bool(changed),
            f"Organized {len(changed)} actors",
            request_id=request_id,
            changed=changed,
            errors=errors,
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("organize_actors", success=bool(changed), request_id=request_id, duration_ms=timer.elapsed_ms)
        return result

    @toolset_registry.tool_call
    @staticmethod
    def place_from_asset_and_verify(
        asset_path: str,
        actor_label: str,
        location_json: str = "[0,0,0]",
        rotation_json: str = "[0,0,0]",
        scale_json: str = "[1,1,1]",
        folder_path: str = "",
    ) -> str:
        """Place actor from StaticMesh / Blueprint / class-like asset path + verify transform."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            loc = parse_json(location_json, field_name="location")
            rot = parse_json(rotation_json, field_name="rotation")
            sc = parse_json(scale_json, field_name="scale")
            asset = unreal.load_asset(asset_path)
            if not asset:
                raise ValueError(f"Asset not found: {asset_path}")
            with scoped_transaction(f"RE place asset {actor_label}"):
                if isinstance(asset, unreal.StaticMesh):
                    cls = unreal.load_class(None, "/Script/Engine.StaticMeshActor")
                    actor = spawn_actor_from_class(
                        cls, vector_from_list(loc), rotator_from_list(rot), vector_from_list(sc)
                    )
                    actor.static_mesh_component.set_static_mesh(asset)
                elif isinstance(asset, unreal.Blueprint):
                    gen = asset.generated_class()
                    if not gen:
                        raise RuntimeError("Blueprint has no generated class")
                    actor = spawn_actor_from_class(
                        gen, vector_from_list(loc), rotator_from_list(rot), vector_from_list(sc)
                    )
                else:
                    # Try as actor class path via load_class
                    cls = unreal.load_class(None, asset_path)
                    if not cls:
                        raise ValueError(f"Unsupported asset type for place: {asset.get_class().get_name()}")
                    actor = spawn_actor_from_class(
                        cls, vector_from_list(loc), rotator_from_list(rot), vector_from_list(sc)
                    )
                actor.set_actor_label(actor_label)
                if folder_path:
                    actor.set_folder_path(unreal.Name(folder_path))
            t = actor.get_actor_location()
            result = workflow_result(
                "place_from_asset_and_verify",
                True,
                f"Placed {actor_label}",
                request_id=request_id,
                created=[actor_label],
                resolved_targets=[actor_ref(actor)],
                extra={
                    "asset": asset_path,
                    "location": [t.x, t.y, t.z],
                },
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("place_from_asset_and_verify", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except (ResolutionError, ValueError, RuntimeError) as exc:
            return error_result(
                "place_from_asset_and_verify", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms
            )

    @toolset_registry.tool_call
    @staticmethod
    def rotate_actors_and_verify(rotations_json: str) -> str:
        """Batch rotate: [{label, rotation:[pitch,yaw,roll]} or {label, add_yaw:deg}]."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        items = parse_json_list(rotations_json, field_name="rotations_json")
        changed: list[str] = []
        errors: list[str] = []
        with scoped_transaction("RE rotate actors"):
            for entry in items[: limits.MUTATE_LIMIT]:
                if not isinstance(entry, dict):
                    errors.append(f"Invalid: {entry}")
                    continue
                label = str(entry.get("label") or entry.get("actor_label") or entry.get("name") or "")
                if not label:
                    errors.append(f"Missing label: {entry}")
                    continue
                try:
                    actor = resolve_actor(label)
                    require_editable(actor)
                    if "rotation" in entry:
                        rot = entry["rotation"]
                        actor.set_actor_rotation(unreal.Rotator(rot[0], rot[1], rot[2]), False)
                    elif "add_yaw" in entry:
                        cur = actor.get_actor_rotation()
                        actor.set_actor_rotation(
                            unreal.Rotator(cur.pitch, cur.yaw + float(entry["add_yaw"]), cur.roll),
                            False,
                        )
                    else:
                        raise ValueError("need rotation or add_yaw")
                    changed.append(label)
                except (ResolutionError, ValueError, RuntimeError) as exc:
                    errors.append(str(exc))
        result = workflow_result(
            "rotate_actors_and_verify",
            bool(changed),
            f"Rotated {len(changed)} actors",
            request_id=request_id,
            changed=changed,
            errors=errors,
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("rotate_actors_and_verify", success=bool(changed), request_id=request_id, duration_ms=timer.elapsed_ms)
        return result
