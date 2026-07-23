"""Lighting / atmosphere composites — mood passes without duplicate sun/sky spam."""

from __future__ import annotations

import unreal
import toolset_registry

from re_agent_tools.common.logging import log_tool_call
from re_agent_tools.common.resolution import ResolutionError, actor_ref, resolve_actor
from re_agent_tools.common.results import WorkflowTimer, error_result, make_request_id, workflow_result
from re_agent_tools.common.serialization import parse_json
from re_agent_tools.common.transactions import scoped_transaction

MOOD_PRESETS = {
    "cave_cool": {
        "directional": {"Intensity": 1.2, "LightColor": [0.55, 0.65, 0.85]},
        "fog": {"FogDensity": 0.04, "FogHeightFalloff": 0.15, "FogInscatteringLuminance": [0.15, 0.2, 0.28]},
    },
    "bright_neutral": {
        "directional": {"Intensity": 3.5, "LightColor": [1.0, 0.98, 0.94]},
        "fog": {"FogDensity": 0.01, "FogHeightFalloff": 0.2},
    },
    "boss_dim": {
        "directional": {"Intensity": 0.8, "LightColor": [0.7, 0.55, 0.5]},
        "fog": {"FogDensity": 0.06, "FogHeightFalloff": 0.12, "FogInscatteringLuminance": [0.2, 0.1, 0.08]},
    },
}


def _find_by_class(cls) -> list:
    eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    out = []
    for a in eas.get_all_level_actors() or []:
        try:
            if isinstance(a, cls):
                out.append(a)
        except Exception:
            if a.get_class() and a.get_class().get_name() == cls.__name__:
                out.append(a)
    return out


def _set_light_props(actor, props: dict) -> list[str]:
    applied = []
    # Prefer component on light actors
    comp = None
    try:
        comps = actor.get_components_by_class(unreal.LightComponent) or []
        comp = comps[0] if comps else None
    except Exception:
        comp = None
    target = comp or actor
    for k, v in props.items():
        try:
            if isinstance(v, (list, tuple)) and len(v) >= 3:
                if len(v) >= 4:
                    col = unreal.LinearColor(float(v[0]), float(v[1]), float(v[2]), float(v[3]))
                else:
                    col = unreal.LinearColor(float(v[0]), float(v[1]), float(v[2]), 1.0)
                # LightColor often Color
                try:
                    target.set_editor_property(k, unreal.Color(
                        int(col.r * 255), int(col.g * 255), int(col.b * 255), 255
                    ))
                except Exception:
                    target.set_editor_property(k, col)
            else:
                target.set_editor_property(k, v)
            applied.append(k)
        except Exception:
            try:
                actor.set_editor_property(k, v)
                applied.append(k)
            except Exception:
                pass
    return applied


@unreal.uclass()
class RELightingWorkflowTools(unreal.ToolsetDefinition):
    """RE composite lighting: inventory existing lights, apply mood, set+verify."""

    @toolset_registry.tool_call
    @staticmethod
    def get_environment_lights_compact() -> str:
        """List DirectionalLight / SkyLight / HeightFog / SkyAtmosphere labels (no spawn)."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            buckets = {
                "directional": [],
                "sky_light": [],
                "height_fog": [],
                "sky_atmosphere": [],
                "volumetric_cloud": [],
            }
            eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
            for a in eas.get_all_level_actors() or []:
                cls = a.get_class().get_name()
                label = a.get_actor_label()
                if "DirectionalLight" in cls:
                    buckets["directional"].append(label)
                elif "SkyLight" in cls:
                    buckets["sky_light"].append(label)
                elif "ExponentialHeightFog" in cls:
                    buckets["height_fog"].append(label)
                elif "SkyAtmosphere" in cls:
                    buckets["sky_atmosphere"].append(label)
                elif "VolumetricCloud" in cls:
                    buckets["volumetric_cloud"].append(label)
            result = workflow_result(
                "get_environment_lights_compact",
                True,
                "Environment light inventory",
                request_id=request_id,
                extra=buckets,
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("get_environment_lights_compact", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except Exception as exc:  # noqa: BLE001
            return error_result(
                "get_environment_lights_compact", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms
            )

    @toolset_registry.tool_call
    @staticmethod
    def list_mood_presets() -> str:
        timer = WorkflowTimer()
        request_id = make_request_id()
        result = workflow_result(
            "list_mood_presets",
            True,
            f"{len(MOOD_PRESETS)} mood presets",
            request_id=request_id,
            extra={"presets": sorted(MOOD_PRESETS.keys())},
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("list_mood_presets", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
        return result

    @toolset_registry.tool_call
    @staticmethod
    def apply_mood_lighting(mood: str = "cave_cool", create_if_missing: bool = False) -> str:
        """Apply named mood to existing DirectionalLight + ExponentialHeightFog (no duplicate sun)."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            preset = MOOD_PRESETS.get(str(mood))
            if not preset:
                raise ValueError(f"Unknown mood '{mood}'. Use list_mood_presets.")
            eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
            dir_lights = [a for a in eas.get_all_level_actors() or [] if "DirectionalLight" in a.get_class().get_name()]
            fogs = [a for a in eas.get_all_level_actors() or [] if "ExponentialHeightFog" in a.get_class().get_name()]
            warnings: list[str] = []
            changed: list[str] = []
            with scoped_transaction(f"RE mood {mood}"):
                if not dir_lights:
                    if create_if_missing:
                        cls = unreal.load_class(None, "/Script/Engine.DirectionalLight")
                        actor = eas.spawn_actor_from_class(cls, unreal.Vector(0, 0, 500))
                        actor.set_actor_label("RE_MoodDirectional")
                        dir_lights = [actor]
                        changed.append(actor.get_actor_label())
                    else:
                        warnings.append("No DirectionalLight — skipped (set create_if_missing=true to spawn)")
                if not fogs:
                    if create_if_missing:
                        cls = unreal.load_class(None, "/Script/Engine.ExponentialHeightFog")
                        actor = eas.spawn_actor_from_class(cls, unreal.Vector(0, 0, 0))
                        actor.set_actor_label("RE_MoodHeightFog")
                        fogs = [actor]
                        changed.append(actor.get_actor_label())
                    else:
                        warnings.append("No ExponentialHeightFog — skipped")

                if dir_lights and "directional" in preset:
                    applied = _set_light_props(dir_lights[0], preset["directional"])
                    changed.append(dir_lights[0].get_actor_label())
                    warnings.append(f"directional applied: {applied}")
                if fogs and "fog" in preset:
                    # Fog props often on component
                    fog = fogs[0]
                    fog_props = preset["fog"]
                    comps = fog.get_components_by_class(unreal.ExponentialHeightFogComponent) or []
                    target = comps[0] if comps else fog
                    applied = []
                    for k, v in fog_props.items():
                        try:
                            if isinstance(v, (list, tuple)):
                                target.set_editor_property(
                                    k, unreal.LinearColor(float(v[0]), float(v[1]), float(v[2]), 1.0)
                                )
                            else:
                                target.set_editor_property(k, v)
                            applied.append(k)
                        except Exception:
                            pass
                    changed.append(fog.get_actor_label())
                    warnings.append(f"fog applied: {applied}")

            result = workflow_result(
                "apply_mood_lighting",
                True,
                f"Applied mood '{mood}'",
                request_id=request_id,
                changed=list(dict.fromkeys(changed)),
                warnings=warnings,
                extra={"mood": mood, "create_if_missing": create_if_missing},
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("apply_mood_lighting", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except ValueError as exc:
            return error_result("apply_mood_lighting", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms)

    @toolset_registry.tool_call
    @staticmethod
    def set_light_properties_and_verify(actor_label: str, properties_json: str) -> str:
        """Set properties on a light actor / its LightComponent with listed keys applied."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            actor = resolve_actor(actor_label)
            props = parse_json(properties_json, field_name="properties_json")
            if not isinstance(props, dict):
                raise ValueError("properties_json must be object")
            with scoped_transaction(f"RE set light {actor_label}"):
                applied = _set_light_props(actor, props)
            result = workflow_result(
                "set_light_properties_and_verify",
                bool(applied),
                f"Set {len(applied)} light props on {actor_label}",
                request_id=request_id,
                changed=[actor_label],
                resolved_targets=[actor_ref(actor)],
                extra={"applied": applied},
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call(
                "set_light_properties_and_verify",
                success=bool(applied),
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )
            return result
        except (ResolutionError, ValueError) as exc:
            return error_result(
                "set_light_properties_and_verify", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms
            )
