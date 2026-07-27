# REAgentTools — Usage Guide

**Start here for agents:** [`AGENT_DEFAULTS.md`](./AGENT_DEFAULTS.md) — trivia pushback, RE-first, no Epic manual fallback.

## Refresh after install

1. Enable plugin in editor.
2. Console: `ModelContextProtocol.RefreshTools` (or `REProjectWorkflowTools.reload_workflow_modules`)
3. Confirm toolsets appear (MCP Inspector or Cursor).

If Cursor **Remote Control** shows `mcp-unreal` discovery error, keep using RE composites via
[`REMOTE_CONTROL_MCP.md`](./REMOTE_CONTROL_MCP.md) (`_rc_reagent_exec.py`) — do not fall back to Epic tools.

## Example prompts (agent → tool)

### Trivia pushback (do not call MCP yet)

> User: "What level are we on?"  
> Agent: ask if they want a real `get_editor_context` pull or a follow-on edit task — each MCP call re-reads ~full chat context.

### Context first (real work)

> "Call REContextTools.get_editor_context with level and selection, then resolve_targets for actor `VW_RE_CaveEntrance`."

### Spawn and configure

> "Use REActorWorkflowTools.spawn_configure_attach_and_verify: class `/Script/Engine.StaticMeshActor`, label `RE_TestCube`, location [0,0,100], properties_json setting StaticMeshComponent mesh."

### Batch move

> "REActorWorkflowTools.batch_transform_actors with transforms_json: three actors RE_Light_A/B/C, move Z +50 each."

### Safe delete

> "REActorWorkflowTools.delete_actors_validated with dry_run true for labels [RE_Temp_01, RE_Temp_02]."

### Asset bulk edit

> "REAssetWorkflowTools.bulk_edit_asset_properties_and_save: edit `/Game/RE/Data/DA_Test` properties_json {...}."

### Blueprint chain

> "REBlueprintWorkflowTools.create_or_update_blueprint in /Game/RE/Blueprints, name BP_RE_TestActor, parent /Script/Engine.Actor, set defaults, compile."

### Material assign

> "REMaterialWorkflowTools.create_assign_material_instance: parent `/Game/Materials/M_Base`, assign to actor RE_Wall_01 slot 0."

### Animation (Control Rig → montage)

> "REAnimWorkflowTools.author_clip_from_pose_timeline: folder `/Game/RE/Combat/Anims/Sword`, clip_stem `Sword_Light_01`, poses_json with guard_r → coil_r → slash_contact → follow_through_low, notifies_json trail/hit frames."

> "REAnimWorkflowTools.list_pose_presets then author_controlrig_pose_timeline for a custom LevelSequence."

### Level place

> "RELevelWorkflowTools.place_configure_save_actors with StaticMeshActor placements at cave hub coords, save_level true."

### Validation bundle

> "REValidationWorkflowTools.run_validation_bundle for blueprint paths [/Game/RE/Blueprints/BP_RE_Player], run map check."

### Batch with refs

```json
[
  {"id":"a","action":"spawn_actor","class_path":"/Script/Engine.StaticMeshActor","label":"RE_Batch_Test","location":[0,0,0]},
  {"id":"b","action":"set_actor_properties","actor_label":"$a","properties_json":"{}"},
  {"id":"c","action":"save_level"}
]
```

Pass as `operations_json` string to `REBatchWorkflowTools.execute_editor_batch`.

## Response format

All tools return compact JSON `WorkflowResult`:

- `success`, `request_id`, `operation`, `summary`
- `changed`, `created`, `deleted`, `compiled`, `saved`
- `warnings`, `errors`, `truncated`, `duration_ms`

## Agent rules

- Read `Content/RE/UNREAL_MCP_TOOL_MAP.md` — RE section first for composites.
- ≤6 MCP calls per scoped task; prefer one composite over many low-level calls.
- `delete_actors_validated`: always dry_run first unless user confirms.
- No `list_toolsets` when tool is mapped.

## Project gaps

Call `REProjectWorkflowTools.get_plugin_project_notes` before inventing GAS/enemy/dungeon tools.
