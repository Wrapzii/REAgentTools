"""Material composite workflow tools."""

from __future__ import annotations

import unreal
import toolset_registry
from toolset_registry.helpers import create_asset

from re_agent_tools.common.logging import log_tool_call
from re_agent_tools.common.resolution import ResolutionError, resolve_actor, resolve_asset
from re_agent_tools.common.results import WorkflowTimer, error_result, make_request_id, workflow_result
from re_agent_tools.common.serialization import parse_json, parse_json_list


def _eas() -> unreal.EditorAssetSubsystem:
    return unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)


def _set_mi_params(mi: unreal.MaterialInstanceConstant, params_json: str) -> list[str]:
    data = parse_json(params_json, field_name="parameters_json")
    changed: list[str] = []
    for key, value in data.items():
        if isinstance(value, (int, float)):
            unreal.MaterialEditingLibrary.set_material_instance_scalar_parameter_value(mi, key, float(value))
        elif isinstance(value, list) and len(value) in (3, 4):
            unreal.MaterialEditingLibrary.set_material_instance_vector_parameter_value(
                mi, key, unreal.LinearColor(value[0], value[1], value[2], value[3] if len(value) > 3 else 1.0)
            )
        elif isinstance(value, str) and value.startswith("/"):
            tex = unreal.load_asset(value)
            if tex:
                unreal.MaterialEditingLibrary.set_material_instance_texture_parameter_value(mi, key, tex)
        changed.append(key)
    return changed


@unreal.uclass()
class REMaterialWorkflowTools(unreal.ToolsetDefinition):
    """RE composite material workflows: MI create/configure/assign."""

    @toolset_registry.tool_call
    @staticmethod
    def create_material_instance_configure_save(
        folder_path: str,
        asset_name: str,
        parent_material_path: str,
        parameters_json: str = "{}",
    ) -> str:
        """Create MI, set parameters, save."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        full_path = f"{folder_path.rstrip('/')}/{asset_name}"
        if _eas().does_asset_exist(full_path):
            return error_result("create_material_instance_configure_save", f"Exists: {full_path}", request_id=request_id, duration_ms=timer.elapsed_ms)
        parent = resolve_asset(parent_material_path)
        if not isinstance(parent, unreal.MaterialInterface):
            return error_result("create_material_instance_configure_save", "Parent is not MaterialInterface", request_id=request_id, duration_ms=timer.elapsed_ms)
        mi = create_asset(
            folder_path,
            asset_name,
            unreal.MaterialInstanceConstant.static_class(),
            unreal.MaterialInstanceConstantFactoryNew(),
        )
        assert isinstance(mi, unreal.MaterialInstanceConstant)
        unreal.MaterialEditingLibrary.set_material_instance_parent(mi, parent)
        changed = _set_mi_params(mi, parameters_json)
        _eas().save_asset(full_path)
        result = workflow_result(
            "create_material_instance_configure_save",
            True,
            f"Created {full_path}",
            request_id=request_id,
            created=[full_path],
            saved=[full_path],
            changed=changed,
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("create_material_instance_configure_save", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
        return result

    @toolset_registry.tool_call
    @staticmethod
    def update_material_instance_parameters(
        material_instance_path: str,
        parameters_json: str,
        save: bool = True,
    ) -> str:
        """Update MI scalar/vector/texture parameters."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            mi = resolve_asset(material_instance_path)
            if not isinstance(mi, unreal.MaterialInstanceConstant):
                raise ResolutionError("Not a MaterialInstanceConstant")
            changed = _set_mi_params(mi, parameters_json)
            saved: list[str] = []
            if save:
                _eas().save_asset(material_instance_path)
                saved.append(material_instance_path)
            result = workflow_result(
                "update_material_instance_parameters",
                True,
                f"Updated {len(changed)} parameters",
                request_id=request_id,
                changed=changed,
                saved=saved,
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("update_material_instance_parameters", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except (ResolutionError, ValueError) as exc:
            return error_result("update_material_instance_parameters", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms)

    @toolset_registry.tool_call
    @staticmethod
    def assign_materials_to_mesh_components(assignments_json: str) -> str:
        """Assign materials: JSON list of {actor_label, component_name?, slot, material_path}."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        items = parse_json_list(assignments_json, field_name="assignments_json")
        changed: list[str] = []
        errors: list[str] = []
        for entry in items:
            if not isinstance(entry, dict):
                errors.append(f"Invalid: {entry}")
                continue
            try:
                actor = resolve_actor(str(entry["actor_label"]))
                mat = resolve_asset(str(entry["material_path"]))
                slot = int(entry.get("slot", 0))
                from re_agent_tools.common.component_helpers import get_mesh_component

                comp = get_mesh_component(actor, str(entry.get("component_name", "") or ""))
                if comp and isinstance(mat, unreal.MaterialInterface):
                    comp.set_material(slot, mat)
                    changed.append(f"{entry['actor_label']}[{slot}]")
            except (ResolutionError, ValueError) as exc:
                errors.append(str(exc))
        result = workflow_result(
            "assign_materials_to_mesh_components",
            bool(changed),
            f"Assigned {len(changed)} slots",
            request_id=request_id,
            changed=changed,
            errors=errors,
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("assign_materials_to_mesh_components", success=bool(changed), request_id=request_id, duration_ms=timer.elapsed_ms)
        return result

    @toolset_registry.tool_call
    @staticmethod
    def create_assign_material_instance(
        folder_path: str,
        asset_name: str,
        parent_material_path: str,
        parameters_json: str,
        actor_label: str,
        slot: int = 0,
        component_name: str = "",
    ) -> str:
        """Create MI, configure, assign to actor mesh slot."""
        created = REMaterialWorkflowTools.create_material_instance_configure_save(
            folder_path, asset_name, parent_material_path, parameters_json
        )
        full_path = f"{folder_path.rstrip('/')}/{asset_name}"
        assign_json = f'[{{"actor_label":"{actor_label}","component_name":"{component_name}","slot":{slot},"material_path":"{full_path}"}}]'
        return REMaterialWorkflowTools.assign_materials_to_mesh_components(assign_json)
