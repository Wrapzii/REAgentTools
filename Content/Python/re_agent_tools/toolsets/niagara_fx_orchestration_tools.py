"""FX orchestration — RE directs stages; Epic Niagara MCP authors systems.

RE does NOT build Niagara graphs. When assets are missing, results include
`epic_handoff` steps targeting NiagaraToolsets.* for the agent to call via MCP.
"""

from __future__ import annotations

import unreal
import toolset_registry

from re_agent_tools.common.logging import log_tool_call
from re_agent_tools.common.resolution import ResolutionError, actor_ref, resolve_actor
from re_agent_tools.common.results import WorkflowTimer, error_result, make_request_id, workflow_result
from re_agent_tools.common.serialization import parse_json, parse_json_list
from re_agent_tools.toolsets.niagara_workflow_tools import (
    RENiagaraWorkflowTools,
    _find_niagara_component,
)

# Epic MCP toolset ids (UE 5.8 AllToolsets / Tool Search call_tool targets).
EPIC_NIAGARA_SYSTEM = "NiagaraToolsets.NiagaraToolset_System"
EPIC_NIAGARA_COMPONENT = "NiagaraToolsets.NiagaraToolset_Component"
EPIC_NIAGARA_ASSETS = "NiagaraToolsets.NiagaraToolset_Assets"
EPIC_NIAGARA_BLUEPRINT = "NiagaraToolsets.NiagaraToolset_Blueprint"
EPIC_NIAGARA_INFO = "NiagaraToolsets.NiagaraToolset_Info"

# Built-in stage sheet: 8-beat fireball (motion/choreography + required NS roles).
FIREBALL_8BEAT: list[dict] = [
    {
        "id": "cast_start",
        "beat": 1,
        "name": "Cast Start",
        "role": "focus",
        "attach": "hand_socket",
        "system_key": "hand_warmup",
        "system_hint": "NS_FX_HandWarmup — soft palm glow / almost dry",
        "visual": "subtle emissive glow; almost no flame yet",
        "duration_s": 0.2,
    },
    {
        "id": "fire_gather",
        "beat": 2,
        "name": "Fire Gather",
        "role": "hand_fx",
        "attach": "hand_socket",
        "system_key": "hand_gather",
        "system_hint": "NS_FX_FlameGather — continuous flame ribbons spiral into palm (not spark confetti)",
        "visual": "flame ribbons / volume plume orbiting into hand",
        "duration_s": 0.5,
    },
    {
        "id": "ignition",
        "beat": 3,
        "name": "Ignition",
        "role": "hand_fx",
        "attach": "hand_socket",
        "system_key": "hand_core",
        "system_hint": "NS_FX_FlameCore — dense flame body in palm",
        "visual": "continuous fireball core in hand",
        "duration_s": 0.3,
    },
    {
        "id": "release",
        "beat": 4,
        "name": "Release",
        "role": "muzzle_burst",
        "attach": "hand_socket",
        "system_key": "release_burst",
        "system_hint": "NS_FX_FlameRelease — one-shot flame burst at launch",
        "visual": "launch burst; spawn projectile",
        "duration_s": 0.25,
        "spawns_projectile": True,
    },
    {
        "id": "projectile_travel",
        "beat": 5,
        "name": "Projectile Travel",
        "role": "projectile",
        "attach": "projectile",
        "system_key": "projectile",
        "system_hint": "NS_FX_FlameProjectile — mesh/volume flame body + continuous trail",
        "visual": "flying flame body + trail (ribbons/volume, not particle pepper)",
        "duration_s": None,
    },
    {
        "id": "impact",
        "beat": 6,
        "name": "Impact",
        "role": "impact",
        "attach": "world_hit",
        "system_key": "impact",
        "system_hint": "NS_FX_FlameImpact — violent continuous flame bloom",
        "visual": "spherical flame explosion at contact",
        "duration_s": 0.35,
    },
    {
        "id": "aftermath",
        "beat": 7,
        "name": "Aftermath",
        "role": "ground_linger",
        "attach": "world_hit",
        "system_key": "aftermath",
        "system_hint": "NS_FX_FlameAftermath — ground flame + smoke linger",
        "visual": "lingering ground flame / smoke",
        "duration_s": 4.0,
    },
    {
        "id": "dissipate",
        "beat": 8,
        "name": "Dissipate",
        "role": "fade",
        "attach": "world_hit",
        "system_key": "aftermath",
        "system_hint": "Same aftermath system: ramp spawn→0, smoke dominates",
        "visual": "flame dies; smoke + dying glow",
        "duration_s": 2.0,
        "param_ramp": {"flame_spawn_scale": 0.0, "emissive_boost": 0.0},
    },
]

STAGE_PRESETS: dict[str, list[dict]] = {
    "fireball_8beat": FIREBALL_8BEAT,
}


def _epic_step(
    toolset: str,
    tool: str,
    arguments: dict,
    *,
    why: str,
) -> dict:
    return {
        "transport": "epic_mcp",
        "toolset_name": toolset,
        "tool_name": tool,
        "arguments": arguments,
        "why": why,
        # Tool Search mode: call_tool(toolset_name, tool_name, arguments)
        "call_tool_hint": {
            "toolset_name": toolset,
            "tool_name": tool,
            "arguments": arguments,
        },
    }


def _authoring_recipe_for_system(
    *,
    asset_name: str,
    asset_path: str,
    template_system: str,
    role: str,
) -> list[dict]:
    """Compact Epic Niagara sequence to create/verify a system from a template."""
    full_path = f"{asset_path.rstrip('/')}/{asset_name}"
    steps = [
        _epic_step(
            EPIC_NIAGARA_SYSTEM,
            "CreateNiagaraSystem",
            {
                "assetName": asset_name,
                "assetPath": asset_path,
                "templateSystem": template_system,
            },
            why=f"Create {role} flame system from template (Epic owns graph).",
        ),
        _epic_step(
            EPIC_NIAGARA_SYSTEM,
            "GetSystemSummary",
            {"system": full_path},
            why="Confirm emitters/user vars after create.",
        ),
        _epic_step(
            EPIC_NIAGARA_SYSTEM,
            "GetSystemCompileState",
            {"system": full_path},
            why="Ensure system compiles before wiring into RE stages.",
        ),
    ]
    if role in ("hand_fx", "projectile", "impact", "ground_linger", "muzzle_burst"):
        steps.insert(
            1,
            _epic_step(
                EPIC_NIAGARA_SYSTEM,
                "AddUserVariables",
                {
                    "system": full_path,
                    "variablesToAdd": [
                        {"name": "Tint", "type": "LinearColor"},
                        {"name": "EmissiveBoost", "type": "float"},
                        {"name": "FlameScale", "type": "float"},
                    ],
                },
                why="Expose tint/scale knobs for RE set_niagara_user_parameters_and_verify.",
            ),
        )
    return steps


def _resolve_sheet(preset: str, stages_json: str) -> list[dict]:
    preset_key = (preset or "").strip()
    if stages_json and stages_json.strip() and stages_json.strip() not in ("{}", "[]"):
        raw = parse_json_list(stages_json, field_name="stages_json")
        out = []
        for i, row in enumerate(raw):
            if not isinstance(row, dict):
                raise ValueError("stages_json entries must be objects")
            stage = dict(row)
            stage.setdefault("id", f"stage_{i+1}")
            stage.setdefault("beat", i + 1)
            stage.setdefault("system_key", stage["id"])
            out.append(stage)
        return out
    if preset_key not in STAGE_PRESETS:
        raise ValueError(
            f"Unknown preset {preset_key!r}. Known={sorted(STAGE_PRESETS)} "
            "or pass stages_json."
        )
    return [dict(s) for s in STAGE_PRESETS[preset_key]]


def _asset_exists(path: str) -> bool:
    if not path:
        return False
    try:
        return bool(unreal.load_asset(path))
    except Exception:  # noqa: BLE001
        return False


def _default_path_map(folder: str, stages: list[dict]) -> dict[str, str]:
    """Map system_key → suggested asset path."""
    folder = folder.rstrip("/")
    mapping: dict[str, str] = {}
    for stage in stages:
        key = str(stage.get("system_key") or stage.get("id"))
        hint = str(stage.get("system_hint") or "")
        name = hint.split("—")[0].strip() if "—" in hint else hint.split("-")[0].strip()
        if not name.startswith("NS_"):
            name = f"NS_FX_{key}"
        # strip spaces
        name = name.split()[0]
        mapping[key] = f"{folder}/{name}.{name.split('/')[-1]}"
    return mapping


@unreal.uclass()
class RENiagaraFxOrchestrationTools(unreal.ToolsetDefinition):
    """RE FX director: stage sheets + Epic Niagara handoff. No graph authoring in RE."""

    @toolset_registry.tool_call
    @staticmethod
    def list_fx_stage_presets() -> str:
        """List built-in FX stage sheet presets (e.g. fireball_8beat)."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        presets = {
            k: [{"id": s["id"], "name": s.get("name"), "system_key": s.get("system_key")} for s in v]
            for k, v in STAGE_PRESETS.items()
        }
        result = workflow_result(
            "list_fx_stage_presets",
            True,
            f"{len(presets)} FX presets",
            request_id=request_id,
            extra={
                "presets": presets,
                "policy": {
                    "re_role": "director_wire_verify",
                    "epic_role": "niagara_graph_authoring",
                    "forbid_particle_confetti_as_flame": True,
                    "prefer_continuous_flame": (
                        "mesh/volume/ribbon flame bodies — Epic Niagara authors; "
                        "RE only stages/wires/verifies"
                    ),
                },
            },
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("list_fx_stage_presets", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
        return result

    @toolset_registry.tool_call
    @staticmethod
    def plan_fx_stage_sheet(
        preset: str = "fireball_8beat",
        stages_json: str = "[]",
        fx_folder: str = "/Game/RE/FX",
        template_system: str = "",
        systems_json: str = "{}",
    ) -> str:
        """Plan an FX beat sheet. Returns stages, required NS paths, missing assets, epic_handoff.

        preset: fireball_8beat (or empty if stages_json provided).
        systems_json: optional {system_key: asset_path} overrides.
        template_system: Epic CreateNiagaraSystem template path when authoring missing NS.
        """
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            stages = _resolve_sheet(preset, stages_json)
            overrides = parse_json(systems_json, field_name="systems_json")
            if not isinstance(overrides, dict):
                raise ValueError("systems_json must be object")
            suggested = _default_path_map(fx_folder, stages)
            suggested.update({str(k): str(v) for k, v in overrides.items() if v})

            required_keys = []
            for s in stages:
                k = str(s.get("system_key"))
                if k not in required_keys:
                    required_keys.append(k)

            resolved = []
            missing = []
            epic_handoff: list[dict] = []
            for key in required_keys:
                path = suggested.get(key, "")
                exists = _asset_exists(path.split(".")[0] if path else "")
                # Also try without duplicate suffix
                if not exists and path:
                    exists = _asset_exists(path)
                entry = {"system_key": key, "path": path, "exists": exists}
                resolved.append(entry)
                if not exists:
                    missing.append(entry)
                    name = path.split("/")[-1].split(".")[0] if path else f"NS_FX_{key}"
                    folder = "/".join(path.split("/")[:-1]) if path else fx_folder
                    tmpl = template_system or "/Niagara/DefaultAssets/Templates/Systems/NS_SimpleSprite"
                    epic_handoff.extend(
                        _authoring_recipe_for_system(
                            asset_name=name,
                            asset_path=folder,
                            template_system=tmpl,
                            role=next(
                                (str(s.get("role") or "") for s in stages if s.get("system_key") == key),
                                key,
                            ),
                        )
                    )

            # Deduplicate handoff by (toolset, tool, assetName-ish)
            seen = set()
            unique_handoff = []
            for step in epic_handoff:
                sig = (
                    step["toolset_name"],
                    step["tool_name"],
                    str(step.get("arguments", {}).get("assetName") or step.get("arguments", {}).get("system") or ""),
                )
                if sig in seen:
                    continue
                seen.add(sig)
                unique_handoff.append(step)

            warn_list = [
                f"Missing NiagaraSystem: {m['path']} — use epic_handoff (Epic MCP), not RE graph tools"
                for m in missing
            ]
            ok = True
            result = workflow_result(
                "plan_fx_stage_sheet",
                ok,
                f"Planned {len(stages)} stages; missing_systems={len(missing)}",
                request_id=request_id,
                warnings=warn_list,
                extra={
                    "preset": preset or "custom",
                    "stages": stages,
                    "systems": resolved,
                    "missing_systems": missing,
                    "epic_handoff": unique_handoff,
                    "next": (
                        "If missing_systems: call Epic MCP via epic_handoff (NiagaraToolset_System). "
                        "Then RE: wire_fx_stage / place_niagara_system_and_verify / set params / capture."
                    ),
                    "agent_policy_override": {
                        "forbid_epic_scene_actor_object_fallback": True,
                        "allow_epic_niagara_when_handoff_present": True,
                        "forbid_re_niagara_graph_dsl": True,
                    },
                },
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("plan_fx_stage_sheet", success=ok, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except (ValueError, RuntimeError) as exc:
            return error_result(
                "plan_fx_stage_sheet", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms
            )

    @toolset_registry.tool_call
    @staticmethod
    def get_epic_niagara_recipe(
        goal: str = "create_flame_system",
        asset_name: str = "NS_FX_FlameCore",
        asset_path: str = "/Game/RE/FX",
        template_system: str = "",
        system_path: str = "",
    ) -> str:
        """Return ordered Epic Niagara MCP steps for a goal (create/inspect/compile).

        goals: create_flame_system | inspect_system | compile_check | find_scripts |
               add_emitter_from_template | component_user_vars
        """
        timer = WorkflowTimer()
        request_id = make_request_id()
        goal_key = (goal or "").strip().lower()
        tmpl = template_system or "/Niagara/DefaultAssets/Templates/Systems/NS_SimpleSprite"
        full = system_path or f"{asset_path.rstrip('/')}/{asset_name}.{asset_name}"
        recipes: dict[str, list[dict]] = {
            "create_flame_system": _authoring_recipe_for_system(
                asset_name=asset_name,
                asset_path=asset_path,
                template_system=tmpl,
                role="hand_fx",
            ),
            "inspect_system": [
                _epic_step(EPIC_NIAGARA_SYSTEM, "GetSystemSummary", {"system": full}, why="Lightweight summary"),
                _epic_step(EPIC_NIAGARA_SYSTEM, "GetUserVariables", {"system": full}, why="List user params for RE"),
            ],
            "compile_check": [
                _epic_step(
                    EPIC_NIAGARA_SYSTEM,
                    "GetSystemCompileState",
                    {"system": full},
                    why="Compile state before place/wire",
                ),
                _epic_step(
                    EPIC_NIAGARA_SYSTEM,
                    "GetStackIssues",
                    {"system": full},
                    why="Surface stack errors/warnings",
                ),
            ],
            "find_scripts": [
                _epic_step(
                    EPIC_NIAGARA_ASSETS,
                    "FindNiagaraScripts",
                    {"folderPath": asset_path, "name": "", "bRecursive": True},
                    why="Discover modules/scripts for flame authoring",
                ),
                _epic_step(
                    EPIC_NIAGARA_ASSETS,
                    "GetAssetDiscoveryInfo",
                    {},
                    why="Project Niagara discovery groups",
                ),
            ],
            "add_emitter_from_template": [
                _epic_step(
                    EPIC_NIAGARA_SYSTEM,
                    "AddEmitter",
                    {
                        "system": full,
                        "templateEmitter": tmpl,
                        "emitterName": "FlameBody",
                    },
                    why="Add emitter from template — still Epic graph work, not RE",
                ),
                _epic_step(
                    EPIC_NIAGARA_SYSTEM,
                    "GetSystemCompileState",
                    {"system": full},
                    why="Recompile check after emitter add",
                ),
            ],
            "component_user_vars": [
                _epic_step(
                    EPIC_NIAGARA_COMPONENT,
                    "GetUserVariables",
                    {"component": "SELECT_NIAGARA_COMPONENT"},
                    why="Read component overrides; replace SELECT_NIAGARA_COMPONENT",
                ),
            ],
        }
        if goal_key not in recipes:
            return error_result(
                "get_epic_niagara_recipe",
                f"Unknown goal {goal!r}. Known={sorted(recipes)}",
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )
        steps = recipes[goal_key]
        result = workflow_result(
            "get_epic_niagara_recipe",
            True,
            f"Epic Niagara recipe: {goal_key} ({len(steps)} steps)",
            request_id=request_id,
            extra={
                "goal": goal_key,
                "epic_handoff": steps,
                "note": (
                    "Execute via mcp-unreal Tool Search call_tool. "
                    "Tool names may be PascalCase (CreateNiagaraSystem) or epic_* aliases "
                    "depending on Cursor/server mapping — describe_toolset if a name 404s. "
                    "RE will place/wire after assets exist."
                ),
                "agent_policy_override": {
                    "allow_epic_niagara_when_handoff_present": True,
                    "forbid_epic_scene_actor_object_fallback": True,
                },
            },
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("get_epic_niagara_recipe", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
        return result

    @toolset_registry.tool_call
    @staticmethod
    def resolve_fx_systems(systems_json: str) -> str:
        """Check which NiagaraSystem paths exist. systems_json: {key:path} or [path,...]."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            raw = parse_json(systems_json, field_name="systems_json")
            items: list[tuple[str, str]] = []
            if isinstance(raw, dict):
                items = [(str(k), str(v)) for k, v in raw.items()]
            elif isinstance(raw, list):
                items = [(str(p), str(p)) for p in raw]
            else:
                raise ValueError("systems_json must be object or array")
            rows = []
            missing = []
            for key, path in items:
                exists = _asset_exists(path)
                row = {"key": key, "path": path, "exists": exists}
                rows.append(row)
                if not exists:
                    missing.append(row)
            result = workflow_result(
                "resolve_fx_systems",
                True,
                f"Resolved {len(rows)} systems; missing={len(missing)}",
                request_id=request_id,
                extra={"systems": rows, "missing_systems": missing},
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("resolve_fx_systems", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except ValueError as exc:
            return error_result("resolve_fx_systems", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms)

    @toolset_registry.tool_call
    @staticmethod
    def wire_fx_stage(
        system_path: str,
        actor_label: str,
        location_json: str = "[0,0,100]",
        rotation_json: str = "[0,0,0]",
        scale_json: str = "[1,1,1]",
        folder_path: str = "RE/FX",
        parameters_json: str = "{}",
        stage_id: str = "",
        auto_activate: bool = True,
    ) -> str:
        """Place an existing NiagaraSystem for one FX stage (RE wiring only).

        If system_path missing, returns error + epic_handoff create recipe — does not author graphs.
        """
        timer = WorkflowTimer()
        request_id = make_request_id()
        if not _asset_exists(system_path):
            name = system_path.split("/")[-1].split(".")[0] or "NS_FX_Stage"
            folder = "/".join(system_path.split("/")[:-1]) or "/Game/RE/FX"
            handoff = _authoring_recipe_for_system(
                asset_name=name,
                asset_path=folder,
                template_system="/Niagara/DefaultAssets/Templates/Systems/NS_SimpleSprite",
                role=stage_id or "hand_fx",
            )
            return error_result(
                "wire_fx_stage",
                f"NiagaraSystem not found: {system_path}. Author via Epic MCP epic_handoff, then retry.",
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
                extra={
                    "stage_id": stage_id,
                    "epic_handoff": handoff,
                    "agent_policy_override": {"allow_epic_niagara_when_handoff_present": True},
                },
            )
        # Delegate to existing place composite (returns WorkflowResult JSON string).
        placed = RENiagaraWorkflowTools.place_niagara_system_and_verify(
            system_path=system_path,
            actor_label=actor_label,
            location_json=location_json,
            rotation_json=rotation_json,
            scale_json=scale_json,
            folder_path=folder_path,
            auto_activate=auto_activate,
            parameters_json=parameters_json,
        )
        # Annotate stage_id without re-parsing heavy — wrap lightly
        try:
            import json

            payload = json.loads(placed)
            payload["stage_id"] = stage_id
            payload["operation"] = "wire_fx_stage"
            payload["summary"] = f"Wired stage {stage_id or actor_label} → {system_path}"
            from re_agent_tools.common.serialization import dumps_compact

            return dumps_compact(payload)
        except Exception:  # noqa: BLE001
            return placed

    @toolset_registry.tool_call
    @staticmethod
    def set_fx_stage_params(
        actor_label: str,
        parameters_json: str,
        component_name: str = "",
        stage_id: str = "",
    ) -> str:
        """Set user params on a wired FX stage actor (RE)."""
        out = RENiagaraWorkflowTools.set_niagara_user_parameters_and_verify(
            actor_label=actor_label,
            parameters_json=parameters_json,
            component_name=component_name,
        )
        try:
            import json

            payload = json.loads(out)
            payload["stage_id"] = stage_id
            payload["operation"] = "set_fx_stage_params"
            from re_agent_tools.common.serialization import dumps_compact

            return dumps_compact(payload)
        except Exception:  # noqa: BLE001
            return out

    @toolset_registry.tool_call
    @staticmethod
    def activate_fx_stage(
        actor_label: str,
        activate: bool = True,
        reset: bool = False,
        component_name: str = "",
    ) -> str:
        """Activate/deactivate (optional reset) a placed Niagara FX actor."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            actor = resolve_actor(actor_label)
            comp = _find_niagara_component(actor, component_name)
            if reset and hasattr(comp, "reset_system"):
                try:
                    comp.reset_system()
                except Exception:  # noqa: BLE001
                    pass
            if activate:
                if hasattr(comp, "activate"):
                    comp.activate(True)
                elif hasattr(comp, "set_active"):
                    comp.set_active(True, True)
            else:
                if hasattr(comp, "deactivate"):
                    comp.deactivate()
                elif hasattr(comp, "set_active"):
                    comp.set_active(False, True)
            active = bool(comp.is_active()) if hasattr(comp, "is_active") else activate
            result = workflow_result(
                "activate_fx_stage",
                True,
                f"{'Activated' if activate else 'Deactivated'} {actor_label}",
                request_id=request_id,
                changed=[actor_label],
                resolved_targets=[actor_ref(actor)],
                extra={"is_active": active, "reset": reset},
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call("activate_fx_stage", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
            return result
        except (ResolutionError, RuntimeError) as exc:
            return error_result("activate_fx_stage", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms)

    @toolset_registry.tool_call
    @staticmethod
    def fx_orchestration_notes() -> str:
        """How RE FX orchestration pairs with Epic Niagara MCP (roles, non-goals)."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        notes = {
            "re": [
                "plan_fx_stage_sheet / list_fx_stage_presets",
                "resolve_fx_systems",
                "wire_fx_stage / set_fx_stage_params / activate_fx_stage",
                "RENiagaraWorkflowTools place/assign/params/inspect",
                "RECaptureWorkflowTools for visual verify",
            ],
            "epic_niagara_mcp": [
                EPIC_NIAGARA_SYSTEM,
                EPIC_NIAGARA_COMPONENT,
                EPIC_NIAGARA_ASSETS,
                EPIC_NIAGARA_BLUEPRINT,
                EPIC_NIAGARA_INFO,
            ],
            "epic_owns": [
                "CreateNiagaraSystem / AddEmitter / AddModule / renderers",
                "continuous flame body authoring (mesh/volume/ribbon — not spark confetti)",
                "compile + stack issue fixes",
            ],
            "re_owns": [
                "8-beat (or custom) stage choreography",
                "place/wire/params/activate existing systems",
                "epic_handoff recipes when assets missing",
                "compact verify + capture loops",
            ],
            "non_goals": [
                "RE Niagara graph DSL",
                "replacing Epic Niagara toolsets",
                "particle-pepper as substitute for flame",
            ],
            "fireball_flow": [
                "1. plan_fx_stage_sheet(preset=fireball_8beat)",
                "2. For each missing system: get_epic_niagara_recipe / follow epic_handoff via mcp-unreal",
                "3. Author continuous flame look in Epic (template + emitters) — human/Epic, not RE",
                "4. wire_fx_stage per beat + montage notifies in game code/anim",
                "5. activate + RECapture capture_viewport_to_disk",
            ],
        }
        result = workflow_result(
            "fx_orchestration_notes",
            True,
            "RE↔Epic Niagara FX orchestration notes",
            request_id=request_id,
            extra={"notes": notes},
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("fx_orchestration_notes", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
        return result
