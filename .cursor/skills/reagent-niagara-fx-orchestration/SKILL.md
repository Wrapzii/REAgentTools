---
name: reagent-niagara-fx-orchestration
description: >
  Orchestrate multi-beat Unreal FX (fireball cast sheets, hand/projectile/impact stages)
  with REAgentTools while handing Niagara graph authoring to Epic MCP NiagaraToolsets.
  Use when building continuous flame effects, staging spell FX, or when NS assets are missing
  and epic_handoff is required. Do not invent particle-confetti as the main flame look.
---

# RE Niagara FX orchestration (+ Epic MCP)

## Read

`Docs/NIAGARA_FX_ORCHESTRATION.md`

## Flow

1. `RENiagaraFxOrchestrationTools.plan_fx_stage_sheet` (`preset=fireball_8beat` or custom `stages_json`)
2. If `missing_systems` / `epic_handoff`: call **Epic** `NiagaraToolsets.NiagaraToolset_System` via mcp-unreal (`CreateNiagaraSystem`, compile, optional `AddEmitter`). Prefer continuous flame (mesh/volume/ribbon), not spark pepper.
3. `wire_fx_stage` / `set_fx_stage_params` / `activate_fx_stage` for each beat
4. `RECaptureWorkflowTools.capture_viewport_to_disk` to verify

## Do not

- Author Niagara graphs inside RE
- Fall back to Epic SceneTools/ActorTools/ObjectTools
- Skip `epic_handoff` and claim FX cannot be built when Epic Niagara MCP is available

## Quick recipe

`get_epic_niagara_recipe(goal="create_flame_system", asset_name="NS_FX_FlameCore", asset_path="/Game/RE/FX")`
