"""Safe actor spawn helpers matching Epic SceneTools (Vector location, not Transform)."""

from __future__ import annotations

import unreal


def spawn_actor_from_class(
    actor_class: unreal.Class,
    location: unreal.Vector,
    rotation: unreal.Rotator | None = None,
    scale: unreal.Vector | None = None,
) -> unreal.Actor:
    """Spawn via EditorActorSubsystem.spawn_actor_from_class(class, location: Vector)."""
    eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    if not eas:
        raise RuntimeError("EditorActorSubsystem unavailable")
    # Must pass Vector — Transform causes: Cannot nativize 'Transform' as 'Location'
    actor = eas.spawn_actor_from_class(actor_class, location)
    if not actor:
        raise RuntimeError(f"spawn_actor_from_class failed for {actor_class}")
    if rotation is not None:
        actor.set_actor_rotation(rotation, False)
    if scale is not None:
        actor.set_actor_scale3d(scale)
    return actor


def vector_from_list(values) -> unreal.Vector:
    return unreal.Vector(float(values[0]), float(values[1]), float(values[2]))


def rotator_from_list(values) -> unreal.Rotator:
    # Rotator(roll, pitch, yaw) in Unreal Python — Epic SceneTools uses Rotator(x,y,z) as pitch/yaw/roll from JSON order carefully
    # Our JSON is [pitch, yaw, roll] in actor_workflow set_actor_rotation(Rotator(rot[0], rot[1], rot[2]))
    # Keep consistent with existing code: Rotator(pitch, yaw, roll) via constructor (pitch, yaw, roll) in UE5 Python is often (roll, pitch, yaw)
    # Match Epic: unreal.Rotator(rot[0], rot[1], rot[2]) as used in actor_workflow_tools
    return unreal.Rotator(float(values[0]), float(values[1]), float(values[2]))
