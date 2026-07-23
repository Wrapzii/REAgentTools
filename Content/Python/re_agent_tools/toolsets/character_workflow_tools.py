"""Character / visual combat composites — mesh, montages, sockets without BP dig."""

from __future__ import annotations

import unreal
import toolset_registry

from re_agent_tools.common.logging import log_tool_call
from re_agent_tools.common.results import WorkflowTimer, error_result, make_request_id, workflow_result
from re_agent_tools.common.serialization import parse_json
from re_agent_tools.common.transactions import scoped_transaction

DEFAULT_CHARACTER_BP = "/Game/RE/Core/BP_RECharacter"


def _load_bp(path: str):
    bp = unreal.load_asset(path)
    if not bp:
        raise ValueError(f"Blueprint not found: {path}")
    return bp


def _generated_cdo(bp):
    cls = bp.generated_class()
    if not cls:
        raise RuntimeError("Blueprint has no generated class — compile first")
    return unreal.get_default_object(cls)


def _find_visual_combat(cdo):
    for comp in cdo.get_components_by_class(unreal.ActorComponent) or []:
        name = comp.get_class().get_name()
        if "VisualCombat" in name or "PlayerVisual" in name:
            return comp
    return None


def _mesh_comp(cdo):
    # Character mesh
    try:
        mesh = cdo.get_editor_property("mesh")
        if mesh:
            return mesh
    except Exception:
        pass
    comps = cdo.get_components_by_class(unreal.SkeletalMeshComponent) or []
    return comps[0] if comps else None


@unreal.uclass()
class RECharacterWorkflowTools(unreal.ToolsetDefinition):
    """RE composite character: inspect, set mesh, wire combat montages, list sockets."""

    @toolset_registry.tool_call
    @staticmethod
    def inspect_character_compact(blueprint_path: str = DEFAULT_CHARACTER_BP) -> str:
        """Compact character BP inspect: mesh, visual combat montages, class."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            bp = _load_bp(blueprint_path)
            cdo = _generated_cdo(bp)
            mesh_comp = _mesh_comp(cdo)
            sk = None
            if mesh_comp:
                try:
                    sk_asset = mesh_comp.get_editor_property("skeletal_mesh")
                    sk = sk_asset.get_path_name() if sk_asset else None
                except Exception:
                    try:
                        sk_asset = mesh_comp.skeletal_mesh
                        sk = sk_asset.get_path_name() if sk_asset else None
                    except Exception:
                        pass
            vc = _find_visual_combat(cdo)
            montages = {}
            if vc:
                for prop in (
                    "LightAttackMontage",
                    "LightAttackMontageAlt",
                    "HeavyAttackMontage",
                    "CastMontage",
                    "PickupSwordMontage",
                ):
                    try:
                        val = vc.get_editor_property(prop)
                        montages[prop] = val.get_path_name() if val else None
                    except Exception:
                        montages[prop] = None
            result = workflow_result(
                "inspect_character_compact",
                True,
                f"Inspected {blueprint_path}",
                request_id=request_id,
                extra={
                    "blueprint": blueprint_path,
                    "class": cdo.get_class().get_name(),
                    "skeletal_mesh": sk,
                    "visual_combat": vc.get_class().get_name() if vc else None,
                    "montages": montages,
                },
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("inspect_character_compact", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except (ValueError, RuntimeError) as exc:
            return error_result("inspect_character_compact", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms)

    @toolset_registry.tool_call
    @staticmethod
    def set_character_mesh_and_verify(
        skeletal_mesh_path: str,
        blueprint_path: str = DEFAULT_CHARACTER_BP,
        compile_save: bool = True,
    ) -> str:
        """Set skeletal mesh on character CDO mesh component + optional compile/save."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            bp = _load_bp(blueprint_path)
            mesh_asset = unreal.load_asset(skeletal_mesh_path)
            if not mesh_asset:
                raise ValueError(f"SkeletalMesh not found: {skeletal_mesh_path}")
            with scoped_transaction("RE set character mesh"):
                cdo = _generated_cdo(bp)
                mesh_comp = _mesh_comp(cdo)
                if not mesh_comp:
                    raise RuntimeError("No SkeletalMeshComponent on character CDO")
                mesh_comp.set_editor_property("skeletal_mesh", mesh_asset)
            saved: list[str] = []
            if compile_save:
                try:
                    unreal.BlueprintEditorLibrary.compile_blueprint(bp)
                except Exception:
                    pass
                unreal.EditorAssetLibrary.save_loaded_asset(bp)
                saved.append(blueprint_path)
            # readback
            cdo2 = _generated_cdo(bp)
            mc2 = _mesh_comp(cdo2)
            current = None
            try:
                cur = mc2.get_editor_property("skeletal_mesh")
                current = cur.get_path_name() if cur else None
            except Exception:
                pass
            ok = bool(current and skeletal_mesh_path.split(".")[0] in current)
            result = workflow_result(
                "set_character_mesh_and_verify",
                ok,
                f"Set mesh on {blueprint_path}",
                request_id=request_id,
                changed=[blueprint_path],
                saved=saved,
                extra={"requested": skeletal_mesh_path, "readback": current},
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("set_character_mesh_and_verify", success=ok, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except (ValueError, RuntimeError) as exc:
            return error_result(
                "set_character_mesh_and_verify", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms
            )

    @toolset_registry.tool_call
    @staticmethod
    def set_visual_combat_montages(
        montages_json: str,
        blueprint_path: str = DEFAULT_CHARACTER_BP,
        compile_save: bool = True,
    ) -> str:
        """Set Light/Heavy/Cast/Pickup montage refs on REVisualCombat component defaults.

        montages_json example:
          {"LightAttackMontage":"/Game/.../AM_RE_Sword_Light_01",
           "CastMontage":"/Game/.../AM_RE_Magecraft_LPalm_Cast"}
        """
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            mapping = parse_json(montages_json, field_name="montages_json")
            if not isinstance(mapping, dict):
                raise ValueError("montages_json must be object")
            bp = _load_bp(blueprint_path)
            changed_props: list[str] = []
            errors: list[str] = []
            with scoped_transaction("RE set visual combat montages"):
                cdo = _generated_cdo(bp)
                vc = _find_visual_combat(cdo)
                if not vc:
                    raise RuntimeError("REVisualCombat / PlayerVisual component not found on CDO")
                for prop, path in mapping.items():
                    try:
                        if path in ("", None):
                            vc.set_editor_property(str(prop), None)
                            changed_props.append(str(prop))
                            continue
                        asset = unreal.load_asset(str(path))
                        if not asset:
                            errors.append(f"{prop}: missing {path}")
                            continue
                        vc.set_editor_property(str(prop), asset)
                        changed_props.append(str(prop))
                    except Exception as exc:  # noqa: BLE001
                        errors.append(f"{prop}: {exc}")
            saved: list[str] = []
            if compile_save:
                try:
                    unreal.BlueprintEditorLibrary.compile_blueprint(bp)
                except Exception:
                    pass
                unreal.EditorAssetLibrary.save_loaded_asset(bp)
                saved.append(blueprint_path)
            # readback
            cdo2 = _generated_cdo(bp)
            vc2 = _find_visual_combat(cdo2)
            readback = {}
            if vc2:
                for prop in changed_props:
                    try:
                        val = vc2.get_editor_property(prop)
                        readback[prop] = val.get_path_name() if val else None
                    except Exception:
                        readback[prop] = None
            result = workflow_result(
                "set_visual_combat_montages",
                bool(changed_props) and not errors,
                f"Updated {len(changed_props)} montage refs",
                request_id=request_id,
                changed=[blueprint_path],
                saved=saved,
                errors=errors,
                extra={"readback": readback},
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call(
                "set_visual_combat_montages",
                success=bool(changed_props) and not errors,
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )
            return result
        except (ValueError, RuntimeError) as exc:
            return error_result("set_visual_combat_montages", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms)

    @toolset_registry.tool_call
    @staticmethod
    def list_mesh_sockets_compact(
        skeletal_mesh_path: str = "",
        blueprint_path: str = DEFAULT_CHARACTER_BP,
        limit: int = 40,
    ) -> str:
        """List socket names on a skeletal mesh (or character's current mesh)."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            mesh = None
            if skeletal_mesh_path:
                mesh = unreal.load_asset(skeletal_mesh_path)
            else:
                bp = _load_bp(blueprint_path)
                cdo = _generated_cdo(bp)
                mc = _mesh_comp(cdo)
                if mc:
                    try:
                        mesh = mc.get_editor_property("skeletal_mesh")
                    except Exception:
                        mesh = getattr(mc, "skeletal_mesh", None)
            if not mesh:
                raise ValueError("No skeletal mesh to inspect")
            names: list[str] = []
            try:
                sockets = mesh.get_editor_property("sockets") or []
                for s in sockets:
                    try:
                        names.append(str(s.socket_name))
                    except Exception:
                        names.append(str(s))
            except Exception:
                # Skeleton sockets
                try:
                    skel = mesh.get_editor_property("skeleton")
                    if skel:
                        for s in skel.get_editor_property("sockets") or []:
                            names.append(str(getattr(s, "socket_name", s)))
                except Exception:
                    pass
            names = names[: max(1, min(int(limit), 100))]
            result = workflow_result(
                "list_mesh_sockets_compact",
                True,
                f"{len(names)} sockets",
                request_id=request_id,
                extra={"mesh": mesh.get_path_name(), "sockets": names, "count": len(names)},
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("list_mesh_sockets_compact", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except (ValueError, RuntimeError) as exc:
            return error_result("list_mesh_sockets_compact", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms)
