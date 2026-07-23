"""UE 5.8-safe actor component lookup (no Actor.get_component_by_name)."""

from __future__ import annotations

import unreal

from re_agent_tools.common.resolution import ResolutionError


def iter_actor_components(actor: unreal.Actor) -> list[unreal.ActorComponent]:
    """Return actor components via get_components_by_class when available."""
    try:
        comps = actor.get_components_by_class(unreal.ActorComponent)
        if comps:
            return list(comps)
    except Exception:  # noqa: BLE001
        pass

    found: list[unreal.ActorComponent] = []
    try:
        root = actor.root_component
        if root:
            found.append(root)
            try:
                for child in root.get_children_components(True):
                    found.append(child)
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    return found


def get_actor_component_by_name(
    actor: unreal.Actor,
    component_name: str,
) -> unreal.ActorComponent:
    """Resolve a component by exact/substring name, with mesh aliases."""
    if not component_name or not str(component_name).strip():
        raise ResolutionError("component_name is required")

    target = str(component_name).strip()
    target_l = target.lower()
    comps = iter_actor_components(actor)

    # Exact match first
    for comp in comps:
        if comp is None:
            continue
        name = str(comp.get_name())
        if name == target or name.lower() == target_l:
            return comp

    # Substring (unique)
    substr = [c for c in comps if c and target_l in str(c.get_name()).lower()]
    if len(substr) == 1:
        return substr[0]
    if len(substr) > 1:
        raise ResolutionError(
            f"Ambiguous component '{component_name}' on {actor.get_actor_label()}: "
            f"{[c.get_name() for c in substr[:10]]}"
        )

    # Common mesh aliases → first StaticMeshComponent
    if target_l in (
        "mesh",
        "staticmesh",
        "staticmeshcomponent",
        "staticmeshcomponent0",
        "smc",
    ):
        mesh = actor.get_component_by_class(unreal.StaticMeshComponent)
        if mesh:
            return mesh

    available = [str(c.get_name()) for c in comps if c][:25]
    raise ResolutionError(
        f"Component '{component_name}' not found on {actor.get_actor_label()}. "
        f"Available: {available}"
    )


def get_mesh_component(
    actor: unreal.Actor,
    component_name: str = "",
) -> unreal.MeshComponent:
    """Resolve mesh component by optional name, else first StaticMeshComponent."""
    if component_name and str(component_name).strip():
        comp = get_actor_component_by_name(actor, component_name)
        if not isinstance(comp, unreal.MeshComponent):
            raise ResolutionError(
                f"Component '{component_name}' is {comp.get_class().get_name()}, not a MeshComponent"
            )
        return comp
    mesh = actor.get_component_by_class(unreal.StaticMeshComponent)
    if mesh:
        return mesh
    mesh = actor.get_component_by_class(unreal.SkeletalMeshComponent)
    if mesh:
        return mesh
    raise ResolutionError(f"No mesh component on {actor.get_actor_label()}")
