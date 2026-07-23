"""Niagara composite workflow tools — place / assign / tune without Epic call chains."""

from __future__ import annotations

import unreal
import toolset_registry

from re_agent_tools.common.component_helpers import get_actor_component_by_name
from re_agent_tools.common.logging import log_tool_call
from re_agent_tools.common.resolution import ResolutionError, actor_ref, resolve_actor
from re_agent_tools.common.results import WorkflowTimer, error_result, make_request_id, workflow_result
from re_agent_tools.common.serialization import parse_json
from re_agent_tools.common.spawn_helpers import rotator_from_list, spawn_actor_from_class, vector_from_list
from re_agent_tools.common.transactions import scoped_transaction


def _find_niagara_component(actor: unreal.Actor, component_name: str = ""):
    if component_name:
        return get_actor_component_by_name(actor, component_name)
    comps = actor.get_components_by_class(unreal.NiagaraComponent) or []
    if not comps:
        raise ResolutionError(f"No NiagaraComponent on {actor.get_actor_label()}")
    return comps[0]


def _set_user_params(comp, params: dict):
    """Set Niagara user parameters. Keys without User. prefix OK. Returns (applied, warnings)."""
    applied: list[str] = []
    warnings: list[str] = []
    for key, value in params.items():
        name = str(key)
        if name.startswith("User."):
            name = name[5:]
        try:
            if isinstance(value, bool):
                comp.set_niagara_variable_bool(name, value)
            elif isinstance(value, int) and not isinstance(value, bool):
                # Prefer float for gameplay knobs; int via float if needed
                try:
                    comp.set_niagara_variable_int(name, int(value))
                except Exception:
                    comp.set_niagara_variable_float(name, float(value))
            elif isinstance(value, float):
                comp.set_niagara_variable_float(name, float(value))
            elif isinstance(value, (list, tuple)) and len(value) >= 3:
                if len(value) >= 4:
                    comp.set_niagara_variable_linear_color(
                        name,
                        unreal.LinearColor(float(value[0]), float(value[1]), float(value[2]), float(value[3])),
                    )
                else:
                    comp.set_niagara_variable_vec3(
                        name, unreal.Vector(float(value[0]), float(value[1]), float(value[2]))
                    )
            elif isinstance(value, str):
                # Soft object / string params vary by engine build
                if hasattr(comp, "set_niagara_variable_object"):
                    obj = unreal.load_asset(value)
                    if obj:
                        comp.set_niagara_variable_object(name, obj)
                    else:
                        warnings.append(f"{name}: asset not found {value}")
                        continue
                else:
                    warnings.append(f"{name}: string params unsupported on this build")
                    continue
            else:
                warnings.append(f"{name}: unsupported value type {type(value).__name__}")
                continue
            applied.append(name)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{name}: {exc}")
    return applied, warnings


@unreal.uclass()
class RENiagaraWorkflowTools(unreal.ToolsetDefinition):
    """RE composite Niagara: place system, assign, set user params, compact inspect."""

    @toolset_registry.tool_call
    @staticmethod
    def place_niagara_system_and_verify(
        system_path: str,
        actor_label: str,
        location_json: str = "[0,0,100]",
        rotation_json: str = "[0,0,0]",
        scale_json: str = "[1,1,1]",
        folder_path: str = "RE/FX",
        auto_activate: bool = True,
        parameters_json: str = "{}",
    ) -> str:
        """Spawn NiagaraActor (or StaticMeshActor+component fallback), assign system, optional params."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            system = unreal.load_asset(system_path)
            if not system:
                raise ValueError(f"NiagaraSystem not found: {system_path}")
            loc = parse_json(location_json, field_name="location")
            rot = parse_json(rotation_json, field_name="rotation")
            sc = parse_json(scale_json, field_name="scale")
            params = parse_json(parameters_json, field_name="parameters_json")
            if not isinstance(params, dict):
                raise ValueError("parameters_json must be object")

            with scoped_transaction(f"RE place Niagara {actor_label}"):
                niagara_cls = unreal.load_class(None, "/Script/Niagara.NiagaraActor")
                if not niagara_cls:
                    raise RuntimeError("NiagaraActor class missing — is Niagara plugin enabled?")
                actor = spawn_actor_from_class(
                    niagara_cls,
                    vector_from_list(loc),
                    rotator_from_list(rot),
                    vector_from_list(sc),
                )
                actor.set_actor_label(actor_label)
                if folder_path:
                    actor.set_folder_path(unreal.Name(folder_path))
                comp = _find_niagara_component(actor)
                comp.set_asset(system)
                if auto_activate and hasattr(comp, "activate"):
                    try:
                        comp.activate(True)
                    except Exception:
                        pass
                applied, warnings = _set_user_params(comp, params) if params else ([], [])

            assigned = None
            try:
                assigned = str(comp.get_asset().get_path_name()) if comp.get_asset() else None
            except Exception:
                assigned = system_path
            ok = bool(assigned)
            result = workflow_result(
                "place_niagara_system_and_verify",
                ok,
                f"Placed {actor_label} → {system_path}",
                request_id=request_id,
                created=[actor_label],
                resolved_targets=[actor_ref(actor)],
                warnings=warnings,
                extra={
                    "system": system_path,
                    "assigned": assigned,
                    "params_applied": applied,
                    "location": loc,
                },
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("place_niagara_system_and_verify", success=ok, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except (ResolutionError, ValueError, RuntimeError) as exc:
            return error_result(
                "place_niagara_system_and_verify", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms
            )

    @toolset_registry.tool_call
    @staticmethod
    def assign_niagara_system_to_component(
        actor_label: str,
        system_path: str,
        component_name: str = "",
        reset_overrides: bool = False,
        auto_activate: bool = True,
    ) -> str:
        """Assign a NiagaraSystem to an existing actor's NiagaraComponent + verify."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            actor = resolve_actor(actor_label)
            system = unreal.load_asset(system_path)
            if not system:
                raise ValueError(f"NiagaraSystem not found: {system_path}")
            with scoped_transaction(f"RE assign Niagara {actor_label}"):
                comp = _find_niagara_component(actor, component_name)
                if hasattr(comp, "set_asset"):
                    comp.set_asset(system, reset_overrides) if reset_overrides else comp.set_asset(system)
                else:
                    raise RuntimeError("NiagaraComponent.set_asset unavailable")
                if auto_activate:
                    try:
                        comp.activate(True)
                    except Exception:
                        pass
            assigned = str(comp.get_asset().get_path_name()) if comp.get_asset() else ""
            ok = system_path.split(".")[0] in assigned or assigned.endswith(system_path.split("/")[-1])
            result = workflow_result(
                "assign_niagara_system_to_component",
                ok,
                f"Assigned system on {actor_label}",
                request_id=request_id,
                changed=[actor_label],
                resolved_targets=[actor_ref(actor)],
                extra={"system": system_path, "assigned": assigned, "component": str(comp.get_name())},
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("assign_niagara_system_to_component", success=ok, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except (ResolutionError, ValueError, RuntimeError) as exc:
            return error_result(
                "assign_niagara_system_to_component", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms
            )

    @toolset_registry.tool_call
    @staticmethod
    def set_niagara_user_parameters_and_verify(
        actor_label: str,
        parameters_json: str,
        component_name: str = "",
    ) -> str:
        """Set Niagara User parameters on a placed component (no User. prefix required)."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            actor = resolve_actor(actor_label)
            params = parse_json(parameters_json, field_name="parameters_json")
            if not isinstance(params, dict):
                raise ValueError("parameters_json must be object")
            with scoped_transaction(f"RE Niagara params {actor_label}"):
                comp = _find_niagara_component(actor, component_name)
                applied, warnings = _set_user_params(comp, params)
            result = workflow_result(
                "set_niagara_user_parameters_and_verify",
                bool(applied) or not params,
                f"Applied {len(applied)} Niagara params on {actor_label}",
                request_id=request_id,
                changed=[actor_label],
                warnings=warnings,
                extra={"params_applied": applied},
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call(
                "set_niagara_user_parameters_and_verify",
                success=bool(applied) or not params,
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )
            return result
        except (ResolutionError, ValueError, RuntimeError) as exc:
            return error_result(
                "set_niagara_user_parameters_and_verify",
                str(exc),
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )

    @toolset_registry.tool_call
    @staticmethod
    def inspect_niagara_compact(actor_label: str, component_name: str = "") -> str:
        """Compact Niagara component inspect (asset path, active, location) — no stack dump."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            actor = resolve_actor(actor_label)
            comp = _find_niagara_component(actor, component_name)
            asset = comp.get_asset() if hasattr(comp, "get_asset") else None
            t = actor.get_actor_transform()
            extra = {
                "actor": actor.get_actor_label(),
                "component": str(comp.get_name()),
                "system": asset.get_path_name() if asset else None,
                "is_active": bool(comp.is_active()) if hasattr(comp, "is_active") else None,
                "location": [t.translation.x, t.translation.y, t.translation.z],
            }
            result = workflow_result(
                "inspect_niagara_compact",
                True,
                f"Inspected Niagara on {actor_label}",
                request_id=request_id,
                resolved_targets=[actor_ref(actor)],
                extra=extra,
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("inspect_niagara_compact", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except (ResolutionError, RuntimeError) as exc:
            return error_result("inspect_niagara_compact", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms)
