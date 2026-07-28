# Niagara FX orchestration (RE + Epic MCP)

**Version:** 1.3.0  
**Toolset:** `re_agent_tools.toolsets.niagara_fx_orchestration_tools.RENiagaraFxOrchestrationTools`

## Roles

| Owner | Responsibility |
|-------|----------------|
| **Epic Niagara MCP** (`NiagaraToolsets.*`) | Create/edit systems, emitters, modules, compile — continuous **flame** look |
| **REAgentTools** | Stage sheets, resolve assets, wire/place/params/activate, `epic_handoff` recipes, capture verify |

RE does **not** author Niagara graphs. When systems are missing, RE returns `epic_handoff` for the agent to execute via `mcp-unreal` (`call_tool`).

## Agent flow (fireball example)

1. `list_fx_stage_presets` / `plan_fx_stage_sheet(preset="fireball_8beat")`
2. If `missing_systems`: follow `epic_handoff` (or `get_epic_niagara_recipe`) on Epic Niagara — build continuous flame bodies, not spark confetti
3. `wire_fx_stage` / `set_fx_stage_params` / `activate_fx_stage` per beat
4. `RECaptureWorkflowTools.capture_viewport_to_disk` to verify

## Tools

| tool | Purpose |
|------|---------|
| `list_fx_stage_presets` | Built-in sheets (e.g. `fireball_8beat`) |
| `plan_fx_stage_sheet` | Stages + required NS paths + missing + `epic_handoff` |
| `get_epic_niagara_recipe` | Ordered Epic steps for create/inspect/compile/find/add_emitter |
| `resolve_fx_systems` | Existence check for `{key:path}` |
| `wire_fx_stage` | Place existing NS for one stage (errors with handoff if missing) |
| `set_fx_stage_params` | User params on wired actor |
| `activate_fx_stage` | Activate / deactivate / reset |
| `fx_orchestration_notes` | Role split + fireball flow |

## Epic toolsets referenced

- `NiagaraToolsets.NiagaraToolset_System` — CreateNiagaraSystem, AddEmitter, GetSystemSummary, GetSystemCompileState, AddUserVariables, GetStackIssues, …
- `NiagaraToolsets.NiagaraToolset_Component` — GetUserVariables, …
- `NiagaraToolsets.NiagaraToolset_Assets` — FindNiagaraScripts, GetAssetDiscoveryInfo
- `NiagaraToolsets.NiagaraToolset_Blueprint` — BP wrappers (optional)
- `NiagaraToolsets.NiagaraToolset_Info`

Tool Search mode: use `call_tool` with `toolset_name` + `tool_name`. If a PascalCase name 404s, `describe_toolset` once for that Niagara toolset only (not a fishing expedition).

## Policy

- Still **forbid** Epic SceneTools / ActorTools / ObjectTools fallback.
- **Allow** Epic Niagara **only** when RE returned `epic_handoff` / recipe for missing FX assets.
- Prefer continuous flame (mesh/volume/ribbon) when authoring in Epic — not particle pepper as the main look.

## Related

- `RENiagaraWorkflowTools` — low-level place/assign/params/inspect
- `Docs/CAPABILITY_MATRIX.md` — FX orchestration row
- `Docs/EXPAND_PLAN.md` — Wave notes
