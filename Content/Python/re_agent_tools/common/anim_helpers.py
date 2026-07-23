"""Animation / Control Rig helpers for REAnimWorkflowTools."""

from __future__ import annotations

from typing import Any

import unreal

# Default mannequin targets used by RE combat authoring.
DEFAULT_SKELETON_PATH = "/Game/Characters/Mannequins/Meshes/SK_Mannequin"
DEFAULT_CONTROL_RIG_PATH = "/Game/Characters/Mannequins/Rigs/CR_Mannequin_Body.CR_Mannequin_Body"
DEFAULT_CHARACTER_CLASS = "/Game/RE/Core/BP_RECharacter.BP_RECharacter_C"
DEFAULT_ANIM_FOLDER = "/Game/RE/Combat/Anims"

# Named combat pose presets (Control Rig local transforms).
# Values: (loc_x, loc_y, loc_z, pitch, yaw, roll) in cm / degrees.
POSE_PRESETS: dict[str, dict[str, tuple[float, float, float, float, float, float]]] = {
    "guard_r": {
        "hips_ctrl": (0, 0, 0, 0, 0, 0),
        "spine_03_ctrl": (0, 0, 0, 0, 0, 0),
        "clavicle_r_ctrl": (0, 0, 0, 0, 0, 10),
        "upperarm_r_fk_ctrl": (0, 0, 0, -30, 45, 65),
        "lowerarm_r_fk_ctrl": (0, 0, 0, -18, 20, 35),
        "hand_r_fk_ctrl": (0, 0, 0, 10, -20, 35),
        "upperarm_l_fk_ctrl": (0, 0, 0, -10, -20, -25),
        "lowerarm_l_fk_ctrl": (0, 0, 0, -8, -10, -35),
    },
    "coil_r": {
        "hips_ctrl": (0, 0, 0, 0, -18, 0),
        "spine_03_ctrl": (0, 0, 0, -6, -26, -8),
        "clavicle_r_ctrl": (0, 0, 0, -8, 8, 22),
        "upperarm_r_fk_ctrl": (0, 0, 0, -58, 78, 96),
        "lowerarm_r_fk_ctrl": (0, 0, 0, -32, 35, 48),
        "hand_r_fk_ctrl": (0, 0, 0, 18, -42, 60),
        "upperarm_l_fk_ctrl": (0, 0, 0, -6, -35, -30),
        "lowerarm_l_fk_ctrl": (0, 0, 0, -8, -18, -48),
    },
    "slash_contact": {
        "hips_ctrl": (0, 0, 0, 0, 18, 0),
        "spine_03_ctrl": (0, 0, 0, 8, 34, 10),
        "clavicle_r_ctrl": (0, 0, 0, 4, -12, -10),
        "upperarm_r_fk_ctrl": (0, 0, 0, 24, -30, -78),
        "lowerarm_r_fk_ctrl": (0, 0, 0, -8, -18, -20),
        "hand_r_fk_ctrl": (0, 0, 0, -16, 30, -64),
        "upperarm_l_fk_ctrl": (0, 0, 0, -8, -18, -12),
        "lowerarm_l_fk_ctrl": (0, 0, 0, -12, -8, -28),
    },
    "follow_through_low": {
        "hips_ctrl": (0, 0, 0, 0, 28, 0),
        "spine_03_ctrl": (0, 0, 0, 10, 42, 14),
        "upperarm_r_fk_ctrl": (0, 0, 0, 38, -58, -88),
        "lowerarm_r_fk_ctrl": (0, 0, 0, 12, -25, -35),
        "hand_r_fk_ctrl": (0, 0, 0, -24, 45, -74),
    },
    "overhead_windup": {
        "hips_ctrl": (0, 0, -5, -8, -22, 0),
        "spine_03_ctrl": (0, 0, 0, -18, -35, -14),
        "clavicle_r_ctrl": (0, 0, 0, -10, 10, 28),
        "upperarm_r_fk_ctrl": (0, 0, 0, -78, 82, 110),
        "lowerarm_r_fk_ctrl": (0, 0, 0, -42, 38, 58),
        "hand_r_fk_ctrl": (0, 0, 0, 24, -55, 80),
    },
    "heavy_impact": {
        "hips_ctrl": (0, 0, -7, 14, 30, 0),
        "spine_03_ctrl": (0, 0, 0, 18, 42, 20),
        "upperarm_r_fk_ctrl": (0, 0, 0, 55, -60, -96),
        "lowerarm_r_fk_ctrl": (0, 0, 0, 18, -32, -42),
        "hand_r_fk_ctrl": (0, 0, 0, -30, 58, -90),
    },
    "pickup_reach": {
        "hips_ctrl": (12, 0, -24, 34, 0, 0),
        "spine_03_ctrl": (0, 0, 0, 48, 8, 0),
        "upperarm_r_fk_ctrl": (0, 0, 0, 55, 8, -28),
        "lowerarm_r_fk_ctrl": (0, 0, 0, 45, 0, -22),
        "hand_r_fk_ctrl": (0, 0, 0, 32, 0, -18),
    },
}


def pie_running() -> bool:
    get_pie = getattr(unreal.EditorLevelLibrary, "editor_get_pie_worlds", None)
    if not callable(get_pie):
        return False
    try:
        worlds = get_pie()
        return bool(worlds)
    except Exception:
        return False


def make_frame(value: int) -> unreal.FrameNumber:
    frame = unreal.FrameNumber()
    frame.value = int(value)
    return frame


def transform_from_six(vals: Any) -> unreal.Transform:
    if isinstance(vals, dict):
        loc = vals.get("location") or vals.get("loc") or [0, 0, 0]
        rot = vals.get("rotation") or vals.get("rot") or [0, 0, 0]
        lx, ly, lz = (float(loc[0]), float(loc[1]), float(loc[2])) if len(loc) >= 3 else (0.0, 0.0, 0.0)
        pitch, yaw, roll = (float(rot[0]), float(rot[1]), float(rot[2])) if len(rot) >= 3 else (0.0, 0.0, 0.0)
    else:
        seq = list(vals)
        if len(seq) < 6:
            raise ValueError(f"Transform needs 6 numbers (loc+rot), got {seq}")
        lx, ly, lz, pitch, yaw, roll = (float(x) for x in seq[:6])
    return unreal.Transform(
        unreal.Vector(lx, ly, lz),
        unreal.Rotator(pitch, yaw, roll),
        unreal.Vector(1.0, 1.0, 1.0),
    )


def find_binding(seq: unreal.LevelSequence, label: str):
    for binding in seq.get_bindings():
        try:
            if str(binding.get_display_name()) == label:
                return binding
        except Exception:
            continue
    return None


def resolve_pose_controls(pose_entry: dict[str, Any]) -> dict[str, Any]:
    """Resolve a pose entry that may use preset + bone overrides."""
    controls: dict[str, Any] = {}
    preset_name = pose_entry.get("preset")
    if preset_name:
        preset = POSE_PRESETS.get(str(preset_name))
        if not preset:
            raise ValueError(f"Unknown pose preset: {preset_name}")
        controls.update(preset)
    bones = pose_entry.get("bones") or pose_entry.get("controls") or {}
    if isinstance(bones, dict):
        controls.update(bones)
    # Allow flat control map on the entry itself (excluding meta keys).
    for key, value in pose_entry.items():
        if key in ("frame", "preset", "bones", "controls", "notes"):
            continue
        if isinstance(value, (list, tuple, dict)):
            controls[key] = value
    if not controls:
        raise ValueError("Pose entry has no preset or controls")
    return controls


def normalize_timeline(poses_json: list[Any] | dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Accept list of {frame,...} or dict frame->pose."""
    timeline: dict[int, dict[str, Any]] = {}
    if isinstance(poses_json, dict) and "poses" in poses_json:
        poses_json = poses_json["poses"]
    if isinstance(poses_json, dict):
        for frame_key, pose in poses_json.items():
            frame = int(frame_key)
            if not isinstance(pose, dict):
                raise ValueError(f"Pose at frame {frame} must be an object")
            timeline[frame] = resolve_pose_controls(pose)
        return timeline
    if not isinstance(poses_json, list):
        raise ValueError("poses_json must be a list or object")
    for entry in poses_json:
        if not isinstance(entry, dict) or "frame" not in entry:
            raise ValueError(f"Each pose needs a frame field: {entry}")
        frame = int(entry["frame"])
        timeline[frame] = resolve_pose_controls(entry)
    if not timeline:
        raise ValueError("poses_json is empty")
    return timeline


def ensure_level_sequence(folder_path: str, asset_name: str, end_frame: int) -> tuple[unreal.LevelSequence, bool]:
    eas = unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)
    full = f"{folder_path.rstrip('/')}/{asset_name}"
    existing = unreal.load_asset(full)
    if existing and isinstance(existing, unreal.LevelSequence):
        seq = existing
        created = False
    else:
        asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
        factory = unreal.LevelSequenceFactoryNew()
        seq = asset_tools.create_asset(asset_name, folder_path.rstrip("/"), unreal.LevelSequence, factory)
        created = True
    assert isinstance(seq, unreal.LevelSequence)
    unreal.LevelSequenceEditorBlueprintLibrary.open_level_sequence(seq)
    seq.set_display_rate(unreal.FrameRate(30, 1))
    seq.set_playback_start(0)
    seq.set_playback_end(int(end_frame))
    return seq, created


def ensure_control_rig_binding(
    seq: unreal.LevelSequence,
    binding_label: str,
    *,
    character_class_path: str = DEFAULT_CHARACTER_CLASS,
    control_rig_path: str = DEFAULT_CONTROL_RIG_PATH,
):
    world = unreal.EditorLevelLibrary.get_editor_world()
    if not world:
        raise RuntimeError("No editor world")
    cr_bp = unreal.load_asset(control_rig_path)
    if not cr_bp:
        raise RuntimeError(f"Missing Control Rig: {control_rig_path}")
    cr_class = cr_bp.get_control_rig_class()
    if not cr_class:
        raise RuntimeError(f"Control Rig class missing on {control_rig_path}")

    binding = find_binding(seq, binding_label)
    created_binding = False
    if not binding:
        char_class = unreal.load_class(None, character_class_path)
        if not char_class:
            raise RuntimeError(f"Missing character class: {character_class_path}")
        binding = seq.add_spawnable_from_class(char_class)
        try:
            binding.set_display_name(binding_label)
        except Exception:
            pass
        created_binding = True

    track = unreal.ControlRigSequencerLibrary.find_or_create_control_rig_track(
        world, seq, cr_class, binding, False
    )
    sections = list(track.get_sections()) if track else []
    section = sections[0] if sections else None
    if not section and track:
        section = track.add_section()
    return binding, track, section, cr_class, created_binding


def find_mannequin_control_rig(seq: unreal.LevelSequence):
    for proxy in unreal.ControlRigSequencerLibrary.get_control_rigs(seq):
        rig = proxy.control_rig
        if rig and "CR_Mannequin_Body" in rig.get_name():
            return rig
    # Fall back to first available rig.
    for proxy in unreal.ControlRigSequencerLibrary.get_control_rigs(seq):
        if proxy.control_rig:
            return proxy.control_rig
    return None


def make_control_arrays(frames: list[int], timeline: dict[int, dict[str, Any]]):
    default = (0, 0, 0, 0, 0, 0)
    controls = sorted({name for pose in timeline.values() for name in pose.keys()})
    arrays = []
    for control_name in controls:
        arr = unreal.ArrayOfRigControlTransforms()
        arr.control_name = control_name
        arr.transforms = [
            transform_from_six(timeline.get(frame, {}).get(control_name, default)) for frame in frames
        ]
        arrays.append(arr)
    return controls, arrays


def apply_pose_timeline(
    seq: unreal.LevelSequence,
    control_rig,
    section,
    timeline: dict[int, dict[str, Any]],
) -> str:
    frames = sorted(timeline.keys())
    frame_numbers = [make_frame(f) for f in frames]
    controls, arrays = make_control_arrays(frames, timeline)
    ok = unreal.ControlRigSequencerLibrary.batch_set_control_transforms(
        seq,
        control_rig,
        arrays,
        frame_numbers,
        unreal.ControlRigTransformSpace.LOCAL,
        section,
        unreal.MovieSceneTimeUnit.DISPLAY_RATE,
    )
    method = "batch_set_control_transforms"
    if not ok:
        method = "set_local_control_rig_transform"
        for frame in frames:
            frame_number = make_frame(frame)
            for control_name, vals in sorted(timeline[frame].items()):
                unreal.ControlRigSequencerLibrary.set_local_control_rig_transform(
                    seq,
                    control_rig,
                    control_name,
                    frame_number,
                    transform_from_six(vals),
                    unreal.MovieSceneTimeUnit.DISPLAY_RATE,
                    True,
                )
    return method


def create_or_load_anim_sequence(folder_path: str, asset_name: str, skeleton) -> tuple[Any, bool]:
    full = f"{folder_path.rstrip('/')}/{asset_name}"
    existing = unreal.load_asset(full)
    if existing:
        return existing, False
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    factory = unreal.AnimSequenceFactory()
    factory.set_editor_property("target_skeleton", skeleton)
    factory.set_editor_property("edit_after_new", False)
    asset = asset_tools.create_asset(asset_name, folder_path.rstrip("/"), unreal.AnimSequence, factory)
    return asset, True


def create_or_load_montage(folder_path: str, asset_name: str, skeleton, anim_sequence) -> tuple[Any, bool]:
    full = f"{folder_path.rstrip('/')}/{asset_name}"
    existing = unreal.load_asset(full)
    if existing:
        return existing, False
    asset_tools = unreal.AssetToolsHelpers.get_asset_tools()
    factory = unreal.AnimMontageFactory()
    factory.set_editor_property("target_skeleton", skeleton)
    factory.set_editor_property("source_animation", anim_sequence)
    factory.set_editor_property("edit_after_new", False)
    asset = asset_tools.create_asset(asset_name, folder_path.rstrip("/"), unreal.AnimMontage, factory)
    return asset, True


def export_binding_to_anim(
    seq: unreal.LevelSequence,
    binding,
    anim_sequence,
    *,
    start_frame: int,
    end_frame: int,
    fps: int = 30,
) -> bool:
    options = unreal.AnimSeqExportOption()
    options.set_editor_property("custom_frame_rate", unreal.FrameRate(int(fps), 1))
    options.set_editor_property("custom_display_rate", unreal.FrameRate(int(fps), 1))
    options.set_editor_property("custom_start_frame", make_frame(start_frame))
    options.set_editor_property("custom_end_frame", make_frame(end_frame))
    options.set_editor_property("evaluate_all_skeletal_mesh_components", True)
    return bool(
        unreal.ControlRigSequencerLibrary.export_anim_sequence_from_sequencer(
            anim_sequence, options, binding, True
        )
    )


def apply_notify_plan_metadata(montage, notifies: dict[str, Any]) -> list[str]:
    """Store notify plan on the montage as metadata tags when possible.

    Full AnimNotifyState authoring via Python is engine-version fragile; we
    stamp a compact plan agents / follow-up tools can read.
    """
    applied: list[str] = []
    if not montage or not notifies:
        return applied
    try:
        # Prefer a readable string property if present; otherwise tags.
        plan = ",".join(f"{k}={v}" for k, v in sorted(notifies.items()))
        tags = list(montage.get_editor_property("asset_user_data") or [])
        # Also mirror into Tags if the asset has them.
        try:
            existing_tags = list(montage.get_editor_property("tags") or [])
        except Exception:
            existing_tags = []
        tag_name = f"RENotifyPlan:{plan}"
        if tag_name not in existing_tags:
            existing_tags.append(tag_name)
            try:
                montage.set_editor_property("tags", existing_tags)
                applied.append("tags")
            except Exception:
                pass
        applied.append(f"plan={plan}")
    except Exception as exc:
        applied.append(f"notify_plan_warning:{exc}")
    return applied
