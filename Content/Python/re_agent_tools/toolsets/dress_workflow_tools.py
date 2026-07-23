"""Cave / level dress composites — place meshes, scatter, snap, less Epic call spam."""

from __future__ import annotations

import math
import random

import unreal
import toolset_registry

from re_agent_tools.common import limits
from re_agent_tools.common.logging import log_tool_call
from re_agent_tools.common.resolution import ResolutionError, actor_ref, resolve_actor
from re_agent_tools.common.results import WorkflowTimer, error_result, make_request_id, workflow_result
from re_agent_tools.common.serialization import parse_json, parse_json_list
from re_agent_tools.common.spawn_helpers import rotator_from_list, spawn_actor_from_class, vector_from_list
from re_agent_tools.common.transactions import scoped_transaction


def _eas() -> unreal.EditorActorSubsystem:
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def _spawn_static_mesh(
    mesh_path: str,
    label: str,
    loc,
    rot,
    scale,
    folder: str = "",
    tags: list | None = None,
) -> unreal.Actor:
    mesh = unreal.load_asset(mesh_path)
    if not mesh:
        raise ValueError(f"StaticMesh not found: {mesh_path}")
    cls = unreal.load_class(None, "/Script/Engine.StaticMeshActor")
    actor = spawn_actor_from_class(cls, vector_from_list(loc), rotator_from_list(rot), vector_from_list(scale))
    actor.set_actor_label(label)
    if folder:
        actor.set_folder_path(unreal.Name(folder))
    smc = actor.static_mesh_component
    if smc:
        smc.set_static_mesh(mesh)
    if tags:
        for t in tags:
            actor.tags.append(unreal.Name(str(t)))
    return actor


def _bounds_cm(actor: unreal.Actor) -> dict:
    try:
        origin, extent = actor.get_actor_bounds(False)
        return {
            "origin": [origin.x, origin.y, origin.z],
            "extent": [extent.x, extent.y, extent.z],
            "size": [extent.x * 2, extent.y * 2, extent.z * 2],
        }
    except Exception:
        return {}


@unreal.uclass()
class REDressWorkflowTools(unreal.ToolsetDefinition):
    """RE composite dress: place/scatter static meshes for caves/hubs with verify."""

    @toolset_registry.tool_call
    @staticmethod
    def place_static_mesh_and_verify(
        mesh_path: str,
        actor_label: str,
        location_json: str = "[0,0,0]",
        rotation_json: str = "[0,0,0]",
        scale_json: str = "[1,1,1]",
        folder_path: str = "RE/Dress",
        tags_json: str = "[]",
    ) -> str:
        """Place one StaticMeshActor from asset path; return bounds for scale gate."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            loc = parse_json(location_json, field_name="location")
            rot = parse_json(rotation_json, field_name="rotation")
            sc = parse_json(scale_json, field_name="scale")
            tags = parse_json(tags_json, field_name="tags_json")
            if not isinstance(tags, list):
                tags = []
            with scoped_transaction(f"RE dress place {actor_label}"):
                actor = _spawn_static_mesh(mesh_path, actor_label, loc, rot, sc, folder_path, tags)
            bounds = _bounds_cm(actor)
            result = workflow_result(
                "place_static_mesh_and_verify",
                True,
                f"Placed {actor_label}",
                request_id=request_id,
                created=[actor_label],
                resolved_targets=[actor_ref(actor)],
                extra={"mesh": mesh_path, "bounds_cm": bounds, "scale": sc},
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("place_static_mesh_and_verify", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except (ResolutionError, ValueError, RuntimeError) as exc:
            return error_result("place_static_mesh_and_verify", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms)

    @toolset_registry.tool_call
    @staticmethod
    def batch_place_static_meshes(placements_json: str, folder_path: str = "RE/Dress") -> str:
        """Batch place meshes: [{mesh_path, label, location, rotation?, scale?, tags?}]."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        items = parse_json_list(placements_json, field_name="placements_json")
        created: list[str] = []
        errors: list[str] = []
        bounds_map: dict = {}
        with scoped_transaction("RE dress batch place"):
            for entry in items[: limits.MUTATE_LIMIT]:
                if not isinstance(entry, dict):
                    errors.append(f"Invalid entry: {entry}")
                    continue
                try:
                    mesh_path = str(entry.get("mesh_path") or entry.get("asset") or "")
                    label = str(entry.get("label") or entry.get("actor_label") or entry.get("name") or "")
                    if not mesh_path or not label:
                        raise ValueError("mesh_path and label required")
                    loc = entry.get("location", [0, 0, 0])
                    rot = entry.get("rotation", [0, 0, 0])
                    sc = entry.get("scale", [1, 1, 1])
                    tags = entry.get("tags") or []
                    folder = str(entry.get("folder") or folder_path)
                    actor = _spawn_static_mesh(mesh_path, label, loc, rot, sc, folder, tags)
                    created.append(label)
                    bounds_map[label] = _bounds_cm(actor)
                except Exception as exc:  # noqa: BLE001
                    errors.append(str(exc))
        result = workflow_result(
            "batch_place_static_meshes",
            bool(created),
            f"Placed {len(created)} dress meshes",
            request_id=request_id,
            created=created,
            errors=errors,
            extra={"bounds_cm": bounds_map},
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("batch_place_static_meshes", success=bool(created), request_id=request_id, duration_ms=timer.elapsed_ms)
        return result

    @toolset_registry.tool_call
    @staticmethod
    def scatter_static_meshes_ring(
        mesh_path: str,
        label_prefix: str,
        center_json: str = "[0,0,0]",
        radius_cm: float = 400.0,
        count: int = 8,
        scale_json: str = "[1,1,1]",
        random_yaw: bool = True,
        seed: int = 42,
        folder_path: str = "RE/Dress/Scatter",
        tag: str = "RE_DressScatter",
    ) -> str:
        """Scatter N copies of a mesh on a ring (cave rubble / perimeter dress)."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            center = parse_json(center_json, field_name="center")
            sc = parse_json(scale_json, field_name="scale")
            n = max(1, min(int(count), limits.MUTATE_LIMIT))
            rng = random.Random(int(seed))
            created: list[str] = []
            with scoped_transaction("RE dress scatter ring"):
                for i in range(n):
                    ang = (2.0 * math.pi * i) / float(n)
                    x = float(center[0]) + radius_cm * math.cos(ang)
                    y = float(center[1]) + radius_cm * math.sin(ang)
                    z = float(center[2])
                    yaw = rng.uniform(0, 360) if random_yaw else math.degrees(ang)
                    label = f"{label_prefix}_{i:02d}"
                    _spawn_static_mesh(
                        mesh_path,
                        label,
                        [x, y, z],
                        [0.0, yaw, 0.0],
                        sc,
                        folder_path,
                        [tag],
                    )
                    created.append(label)
            result = workflow_result(
                "scatter_static_meshes_ring",
                True,
                f"Scattered {len(created)} × {mesh_path}",
                request_id=request_id,
                created=created,
                extra={"radius_cm": radius_cm, "center": center, "mesh": mesh_path},
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("scatter_static_meshes_ring", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except (ValueError, RuntimeError) as exc:
            return error_result("scatter_static_meshes_ring", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms)

    @toolset_registry.tool_call
    @staticmethod
    def snap_actors_to_floor(
        actor_labels_json: str,
        trace_up_cm: float = 500.0,
        trace_down_cm: float = 5000.0,
    ) -> str:
        """Line-trace snap actors down onto geometry (dress anti-float)."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        labels = [str(x) for x in parse_json_list(actor_labels_json, field_name="actor_labels_json")]
        changed: list[str] = []
        errors: list[str] = []
        world = unreal.EditorLevelLibrary.get_editor_world()
        if not world:
            return error_result("snap_actors_to_floor", "No editor world", request_id=request_id, duration_ms=timer.elapsed_ms)

        with scoped_transaction("RE dress snap floor"):
            for label in labels[: limits.MUTATE_LIMIT]:
                try:
                    actor = resolve_actor(label)
                    loc = actor.get_actor_location()
                    start = unreal.Vector(loc.x, loc.y, loc.z + float(trace_up_cm))
                    end = unreal.Vector(loc.x, loc.y, loc.z - float(trace_down_cm))
                    hit = unreal.SystemLibrary.line_trace_single(
                        world,
                        start,
                        end,
                        unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
                        False,
                        [actor],
                        unreal.DrawDebugTrace.NONE,
                        True,
                    )
                    # UE Python hit result shapes vary
                    impact = None
                    if hit:
                        if hasattr(hit, "to_tuple"):
                            # (hit_bool, ...)
                            pass
                        if isinstance(hit, (list, tuple)) and len(hit) >= 1:
                            # Often returns struct-like
                            pass
                        for attr in ("location", "impact_point", "Location", "ImpactPoint"):
                            if hasattr(hit, attr):
                                impact = getattr(hit, attr)
                                break
                        if impact is None and hasattr(hit, "get_editor_property"):
                            try:
                                impact = hit.get_editor_property("location")
                            except Exception:
                                pass
                    # Fallback API
                    if impact is None:
                        hit2 = unreal.EditorLevelLibrary.get_level_viewport_camera_info()
                        # Use ActorDownwardSweep style via EditorActorSubsystem if present
                        try:
                            actors_sub = _eas()
                            # simple: drop using set_actor_location after component bound
                            origin, extent = actor.get_actor_bounds(True)
                            # Trace via HitResult out-param pattern
                            hit_result = unreal.HitResult()
                            ok = unreal.SystemLibrary.line_trace_single(
                                world,
                                start,
                                end,
                                unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
                                True,
                                [],
                                unreal.DrawDebugTrace.NONE,
                                hit_result,
                                True,
                            )
                            if ok and hit_result:
                                impact = hit_result.location
                        except Exception as exc2:
                            raise RuntimeError(f"trace failed: {exc2}") from exc2
                    if impact is None:
                        raise RuntimeError("no floor hit")
                    # Sit bottom of bounds on impact
                    _origin, extent = actor.get_actor_bounds(True)
                    new_z = float(impact.z) + float(extent.z)
                    actor.set_actor_location(unreal.Vector(loc.x, loc.y, new_z), False, False)
                    changed.append(label)
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{label}: {exc}")

        result = workflow_result(
            "snap_actors_to_floor",
            bool(changed),
            f"Snapped {len(changed)} actors",
            request_id=request_id,
            changed=changed,
            errors=errors,
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("snap_actors_to_floor", success=bool(changed), request_id=request_id, duration_ms=timer.elapsed_ms)
        return result
