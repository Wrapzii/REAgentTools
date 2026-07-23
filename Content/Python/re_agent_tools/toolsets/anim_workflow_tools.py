"""Animation composite workflow tools for RE combat / locomotion authoring."""

from __future__ import annotations

import unreal
import toolset_registry

from re_agent_tools.common import anim_helpers as ah
from re_agent_tools.common.logging import log_tool_call
from re_agent_tools.common.results import WorkflowTimer, error_result, make_request_id, workflow_result
from re_agent_tools.common.serialization import parse_json


def _eas() -> unreal.EditorAssetSubsystem:
    return unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)


@unreal.uclass()
class REAnimWorkflowTools(unreal.ToolsetDefinition):
    """RE composite animation workflows: Control Rig poses → AnimSequence → Montage."""

    @toolset_registry.tool_call
    @staticmethod
    def list_pose_presets() -> str:
        """List named mannequin Control Rig pose presets (guard/coil/slash/etc.)."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        presets = {
            name: {
                "controls": list(controls.keys()),
                "control_count": len(controls),
            }
            for name, controls in ah.POSE_PRESETS.items()
        }
        result = workflow_result(
            "list_pose_presets",
            True,
            f"{len(presets)} pose presets",
            request_id=request_id,
            duration_ms=timer.elapsed_ms,
            extra={
                "presets": presets,
                "control_rig": ah.DEFAULT_CONTROL_RIG_PATH,
                "skeleton": ah.DEFAULT_SKELETON_PATH,
                "units": "location_cm + rotator_degrees as [lx,ly,lz,pitch,yaw,roll]",
            },
        )
        log_tool_call("list_pose_presets", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
        return result

    @toolset_registry.tool_call
    @staticmethod
    def get_animation_pipeline_notes() -> str:
        """Compact notes: UE authoring path + Comfy skeletal motion research pointers."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        notes = {
            "ue_authoring": [
                "author_controlrig_pose_timeline — key named presets / bone transforms into a LevelSequence",
                "export_sequence_to_anim_and_montage — bake Control Rig Sequencer → AnimSequence + Montage",
                "create_montage_from_anim — Montage from existing AnimSequence + notify plan stamp",
            ],
            "defaults": {
                "skeleton": ah.DEFAULT_SKELETON_PATH,
                "control_rig": ah.DEFAULT_CONTROL_RIG_PATH,
                "character_class": ah.DEFAULT_CHARACTER_CLASS,
                "folder": ah.DEFAULT_ANIM_FOLDER,
            },
            "comfy_skeletal": {
                "doc": "Content/RE/Combat/COMFY_SKELETAL_MOTION.md",
                "summary": "Comfy can recover 3D SMPL motion (GVHMR) and export BVH/FBX; not installed in RE Comfy yet",
                "installed_now": "OpenPose/DWPose 2D + video nodes without base video weights",
                "import_path": "video/images → SMPL → BVH/FBX → Blender retarget → UE mannequin AnimSequence",
            },
            "not_for": [
                "Do not author gameplay anims as level actors",
                "Do not use Comfy video MP4 as a drop-in AnimSequence",
            ],
        }
        result = workflow_result(
            "get_animation_pipeline_notes",
            True,
            "Animation pipeline notes",
            request_id=request_id,
            duration_ms=timer.elapsed_ms,
            extra=notes,
        )
        log_tool_call("get_animation_pipeline_notes", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
        return result

    @toolset_registry.tool_call
    @staticmethod
    def author_controlrig_pose_timeline(
        folder_path: str,
        sequence_name: str,
        poses_json: str,
        binding_label: str = "",
        control_rig_path: str = "",
        character_class_path: str = "",
        end_frame: int = -1,
        save: bool = True,
    ) -> str:
        """Author Control Rig keys from a pose timeline JSON (presets and/or bone transforms).

        poses_json examples:
          [{"frame":0,"preset":"guard_r"},{"frame":9,"preset":"slash_contact"}]
          {"0":{"preset":"guard_r"},"9":{"bones":{"hand_r_fk_ctrl":[0,0,0,10,-20,35]}}}
        """
        timer = WorkflowTimer()
        request_id = make_request_id()
        if ah.pie_running():
            return error_result(
                "author_controlrig_pose_timeline",
                "Refusing to author animation while PIE is running",
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )
        try:
            raw = parse_json(poses_json, field_name="poses_json")
            if isinstance(raw, list):
                timeline = ah.normalize_timeline(raw)
            else:
                timeline = ah.normalize_timeline(raw if isinstance(raw, dict) else {})
            frames = sorted(timeline.keys())
            resolved_end = int(end_frame) if int(end_frame) >= 0 else int(frames[-1])
            if resolved_end < frames[-1]:
                resolved_end = frames[-1]

            folder = folder_path or ah.DEFAULT_ANIM_FOLDER
            seq, seq_created = ah.ensure_level_sequence(folder, sequence_name, resolved_end)
            label = binding_label or sequence_name.replace("LS_RE_", "SK_").replace("_Authoring", "")
            binding, track, section, _cr_class, binding_created = ah.ensure_control_rig_binding(
                seq,
                label,
                character_class_path=character_class_path or ah.DEFAULT_CHARACTER_CLASS,
                control_rig_path=control_rig_path or ah.DEFAULT_CONTROL_RIG_PATH,
            )
            if section:
                try:
                    section.set_start_frame_bounded(True)
                    section.set_end_frame_bounded(True)
                    section.set_start_frame(0)
                    section.set_end_frame(resolved_end)
                except Exception:
                    pass

            control_rig = ah.find_mannequin_control_rig(seq)
            if not control_rig:
                raise RuntimeError("Control Rig proxy missing after track creation")

            method = ah.apply_pose_timeline(seq, control_rig, section, timeline)
            full_path = f"{folder.rstrip('/')}/{sequence_name}"
            saved: list[str] = []
            if save:
                unreal.EditorAssetLibrary.save_loaded_asset(seq)
                saved.append(full_path)

            result = workflow_result(
                "author_controlrig_pose_timeline",
                True,
                f"Authored {len(frames)} pose keys on {full_path}",
                request_id=request_id,
                created=[full_path] if seq_created else [],
                changed=[full_path],
                saved=saved,
                duration_ms=timer.elapsed_ms,
                extra={
                    "sequence": full_path,
                    "binding": label,
                    "frames": frames,
                    "controls": sorted({n for pose in timeline.values() for n in pose.keys()}),
                    "key_method": method,
                    "binding_created": binding_created,
                    "track": track.get_path_name() if track else "",
                },
            )
            log_tool_call("author_controlrig_pose_timeline", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except Exception as exc:  # noqa: BLE001
            return error_result(
                "author_controlrig_pose_timeline",
                str(exc),
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )

    @toolset_registry.tool_call
    @staticmethod
    def export_sequence_to_anim_and_montage(
        level_sequence_path: str,
        anim_folder: str,
        anim_name: str,
        montage_name: str = "",
        binding_label: str = "",
        skeleton_path: str = "",
        start_frame: int = 0,
        end_frame: int = -1,
        fps: int = 30,
        notifies_json: str = "{}",
        save: bool = True,
    ) -> str:
        """Bake a Control Rig LevelSequence binding to AnimSequence + Montage."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        if ah.pie_running():
            return error_result(
                "export_sequence_to_anim_and_montage",
                "Refusing to export animation while PIE is running",
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )
        try:
            seq = unreal.load_asset(level_sequence_path)
            if not isinstance(seq, unreal.LevelSequence):
                raise RuntimeError(f"Missing LevelSequence: {level_sequence_path}")
            unreal.LevelSequenceEditorBlueprintLibrary.open_level_sequence(seq)

            label = binding_label
            if not label:
                # Prefer SK_* spawnable naming used by RE sword authoring.
                for binding in seq.get_bindings():
                    name = str(binding.get_display_name())
                    if name.startswith("SK_"):
                        label = name
                        break
            if not label:
                raise RuntimeError("binding_label required (no SK_* binding found)")

            binding = ah.find_binding(seq, label)
            if not binding:
                raise RuntimeError(f"Missing binding: {label}")

            resolved_end = int(end_frame)
            if resolved_end < 0:
                try:
                    resolved_end = int(seq.get_playback_end())
                except Exception:
                    resolved_end = 30

            skel_path = skeleton_path or ah.DEFAULT_SKELETON_PATH
            skeleton = unreal.load_asset(skel_path)
            if not skeleton:
                raise RuntimeError(f"Missing skeleton: {skel_path}")

            folder = anim_folder or ah.DEFAULT_ANIM_FOLDER
            anim, anim_created = ah.create_or_load_anim_sequence(folder, anim_name, skeleton)
            if not anim:
                raise RuntimeError(f"Failed to create AnimSequence {anim_name}")
            ok = ah.export_binding_to_anim(
                seq,
                binding,
                anim,
                start_frame=int(start_frame),
                end_frame=resolved_end,
                fps=int(fps),
            )
            if not ok:
                raise RuntimeError("export_anim_sequence_from_sequencer returned false")

            mont_name = montage_name or (anim_name.replace("A_RE_", "AM_RE_", 1) if anim_name.startswith("A_RE_") else f"AM_{anim_name}")
            montage, montage_created = ah.create_or_load_montage(folder, mont_name, skeleton, anim)
            notifies = parse_json(notifies_json, field_name="notifies_json")
            if not isinstance(notifies, dict):
                raise ValueError("notifies_json must be an object")
            notify_meta = ah.apply_notify_plan_metadata(montage, notifies)
            if montage:
                try:
                    montage.set_editor_property("enable_root_motion", False)
                    montage.set_editor_property("blend_out_trigger_time", 0.05)
                except Exception:
                    pass

            created: list[str] = []
            saved: list[str] = []
            anim_path = f"{folder.rstrip('/')}/{anim_name}"
            mont_path = f"{folder.rstrip('/')}/{mont_name}"
            if anim_created:
                created.append(anim_path)
            if montage_created:
                created.append(mont_path)
            if save:
                unreal.EditorAssetLibrary.save_loaded_asset(anim)
                saved.append(anim_path)
                if montage:
                    unreal.EditorAssetLibrary.save_loaded_asset(montage)
                    saved.append(mont_path)

            result = workflow_result(
                "export_sequence_to_anim_and_montage",
                True,
                f"Exported {anim_path} + {mont_path}",
                request_id=request_id,
                created=created,
                changed=[anim_path, mont_path],
                saved=saved,
                duration_ms=timer.elapsed_ms,
                extra={
                    "level_sequence": level_sequence_path,
                    "binding": label,
                    "anim_sequence": anim_path,
                    "montage": mont_path,
                    "range": [int(start_frame), resolved_end],
                    "fps": int(fps),
                    "notifies": notifies,
                    "notify_meta": notify_meta,
                },
            )
            log_tool_call("export_sequence_to_anim_and_montage", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except Exception as exc:  # noqa: BLE001
            return error_result(
                "export_sequence_to_anim_and_montage",
                str(exc),
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )

    @toolset_registry.tool_call
    @staticmethod
    def create_montage_from_anim(
        anim_sequence_path: str,
        montage_folder: str,
        montage_name: str,
        skeleton_path: str = "",
        notifies_json: str = "{}",
        save: bool = True,
    ) -> str:
        """Create or refresh an AnimMontage from an existing AnimSequence."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            anim = unreal.load_asset(anim_sequence_path)
            if not anim:
                raise RuntimeError(f"Missing AnimSequence: {anim_sequence_path}")
            skel_path = skeleton_path or ah.DEFAULT_SKELETON_PATH
            skeleton = unreal.load_asset(skel_path)
            if not skeleton:
                raise RuntimeError(f"Missing skeleton: {skel_path}")
            folder = montage_folder or ah.DEFAULT_ANIM_FOLDER
            montage, created = ah.create_or_load_montage(folder, montage_name, skeleton, anim)
            if not montage:
                raise RuntimeError(f"Failed to create montage {montage_name}")
            notifies = parse_json(notifies_json, field_name="notifies_json")
            if not isinstance(notifies, dict):
                raise ValueError("notifies_json must be an object")
            notify_meta = ah.apply_notify_plan_metadata(montage, notifies)
            try:
                montage.set_editor_property("enable_root_motion", False)
            except Exception:
                pass
            mont_path = f"{folder.rstrip('/')}/{montage_name}"
            saved: list[str] = []
            if save:
                unreal.EditorAssetLibrary.save_loaded_asset(montage)
                saved.append(mont_path)
            result = workflow_result(
                "create_montage_from_anim",
                True,
                f"{'Created' if created else 'Updated'} {mont_path}",
                request_id=request_id,
                created=[mont_path] if created else [],
                changed=[mont_path],
                saved=saved,
                duration_ms=timer.elapsed_ms,
                extra={
                    "anim_sequence": anim_sequence_path,
                    "montage": mont_path,
                    "notifies": notifies,
                    "notify_meta": notify_meta,
                },
            )
            log_tool_call("create_montage_from_anim", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except Exception as exc:  # noqa: BLE001
            return error_result(
                "create_montage_from_anim",
                str(exc),
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )

    @toolset_registry.tool_call
    @staticmethod
    def author_clip_from_pose_timeline(
        folder_path: str,
        clip_stem: str,
        poses_json: str,
        notifies_json: str = "{}",
        end_frame: int = -1,
        fps: int = 30,
        save: bool = True,
    ) -> str:
        """One-shot: pose timeline → LevelSequence → AnimSequence → Montage.

        clip_stem example: Sword_Light_01
          → LS_RE_Sword_Light_01_Authoring, A_RE_Sword_Light_01, AM_RE_Sword_Light_01
        """
        timer = WorkflowTimer()
        request_id = make_request_id()
        folder = folder_path or f"{ah.DEFAULT_ANIM_FOLDER}/Sword"
        seq_name = f"LS_RE_{clip_stem}_Authoring"
        anim_name = f"A_RE_{clip_stem}"
        mont_name = f"AM_RE_{clip_stem}"
        try:
            author = REAnimWorkflowTools.author_controlrig_pose_timeline(
                folder,
                seq_name,
                poses_json,
                "",
                "",
                "",
                end_frame,
                save,
            )
            author_data = parse_json(author, field_name="author_result")
            if not author_data.get("success"):
                return author

            seq_path = f"{folder.rstrip('/')}/{seq_name}"
            frames = author_data.get("frames") or []
            resolved_end = int(end_frame) if int(end_frame) >= 0 else (int(frames[-1]) if frames else 30)
            export = REAnimWorkflowTools.export_sequence_to_anim_and_montage(
                seq_path,
                folder,
                anim_name,
                mont_name,
                "",
                "",
                0,
                resolved_end,
                fps,
                notifies_json,
                save,
            )
            export_data = parse_json(export, field_name="export_result")
            ok = bool(export_data.get("success"))
            result = workflow_result(
                "author_clip_from_pose_timeline",
                ok,
                f"{'Authored' if ok else 'Failed'} clip {clip_stem}",
                request_id=request_id,
                created=list(author_data.get("created", [])) + list(export_data.get("created", [])),
                changed=list(author_data.get("changed", [])) + list(export_data.get("changed", [])),
                saved=list(author_data.get("saved", [])) + list(export_data.get("saved", [])),
                errors=list(export_data.get("errors", [])),
                warnings=list(author_data.get("warnings", [])) + list(export_data.get("warnings", [])),
                duration_ms=timer.elapsed_ms,
                extra={
                    "clip_stem": clip_stem,
                    "level_sequence": seq_path,
                    "anim_sequence": f"{folder.rstrip('/')}/{anim_name}",
                    "montage": f"{folder.rstrip('/')}/{mont_name}",
                    "author": {
                        "success": author_data.get("success"),
                        "key_method": author_data.get("key_method"),
                        "frames": frames,
                    },
                    "export": {
                        "success": export_data.get("success"),
                        "notifies": export_data.get("notifies"),
                    },
                },
            )
            log_tool_call("author_clip_from_pose_timeline", success=ok, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except Exception as exc:  # noqa: BLE001
            return error_result(
                "author_clip_from_pose_timeline",
                str(exc),
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )
