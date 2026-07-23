# REAgentTools — Capability Matrix

Legend: ✅ Implemented | 🔶 Partial | ❌ Missing / use Epic low-level | 🚫 Project architecture missing

## Context & resolution

| Workflow | Status | Notes |
|----------|--------|-------|
| get_plugin_capabilities | ✅ | Lists toolsets + limits |
| get_editor_context | ✅ | level, selection, PIE, dirty |
| resolve_targets | ✅ | actors by label, assets by path |
| inspect_targets_compact | ✅ | explicit property list only |

## Actors

| Workflow | Status | Notes |
|----------|--------|-------|
| set_actor_properties_and_verify | ✅ | JSON props + readback |
| set_component_properties_and_verify | ✅ | by component name |
| spawn_configure_attach_and_verify | ✅ | class + transform + attach |
| batch_transform_actors | ✅ | one transaction |
| delete_actors_validated | ✅ | dry_run default true |
| organize_actors | ✅ | folder, label, tags |
| Low-level find/transform | ❌ | Use Epic `SceneTools` / `ActorTools` |

## Assets

| Workflow | Status | Notes |
|----------|--------|-------|
| find_assets_compact | ✅ | path/class/name, max 25 |
| bulk_edit_asset_properties_and_save | ✅ | |
| save_assets | ✅ | exact paths only |
| Import from disk | ❌ | Use Epic `AssetTools.import_asset` |

## Blueprints

| Workflow | Status | Notes |
|----------|--------|-------|
| inspect_blueprint_compact | ✅ | |
| create_blueprint_from_class | ✅ | |
| set_class_defaults_compile_save | ✅ | |
| compile_blueprints | ✅ | |
| create_or_update_blueprint | 🔶 | defaults only; no graph/node authoring |
| Blueprint graph DSL | ❌ | Epic `BlueprintTools` / `BlueprintNodeTools` |

## Materials

| Workflow | Status | Notes |
|----------|--------|-------|
| create_material_instance_configure_save | ✅ | scalar/vector/texture params |
| update_material_instance_parameters | ✅ | |
| assign_materials_to_mesh_components | ✅ | |
| create_assign_material_instance | ✅ | |
| Master material graph edit | ❌ | Epic `MaterialTools` |

## Levels

| Workflow | Status | Notes |
|----------|--------|-------|
| open_or_create_level | ✅ | |
| place_configure_save_actors | ✅ | |
| run_map_check | 🔶 | API may be unavailable; honest fallback |

## Validation

| Workflow | Status | Notes |
|----------|--------|-------|
| compile_save_validate | ✅ | |
| get_recent_errors_compact | 🔶 | Prefer Epic `LogsToolset.GetLogEntries` (works); RECapture has Saved/Logs fallback |
| run_validation_bundle | ✅ | compile + map check + save |

## Batch

| Workflow | Status | Notes |
|----------|--------|-------|
| execute_editor_batch | ✅ | 8 allowlisted actions, $ref, dry_run |

## Project-specific (RE game)

| Domain | Status | Notes |
|--------|--------|-------|
| GAS / abilities | 🔶 | No full GAS graph authoring — but `DT_Abilities` + `CastAbility` exist; `pie_cast_and_capture` drives that |
| Enemy AI / spawners | 🚫 | **Project architecture missing** |
| Dungeon proc-gen | 🚫 | Manual craft + `re-voxel-world` only |
| Inventory | 🔶 | `re_inventory_bplibrary` bridge exists; no REAgentTools wrapper yet |
| Niagara place / assign / user params | ✅ | `RENiagaraWorkflowTools` (v1.1) — not full module-graph DSL |
| Cave / level dress place+scatter | ✅ | `REDressWorkflowTools` (v1.1) |
| Character mesh / combat montages | ✅ | `RECharacterWorkflowTools` (v1.1) |
| Mood lighting | ✅ | `RELightingWorkflowTools` (v1.1) |
| Animation / montage | ✅ | `REAnimWorkflowTools` — Control Rig pose timeline → AnimSequence + Montage |
| Viewport / FX material capture | ✅ | `RECaptureWorkflowTools` (v1.2) — path + downscale/JPEG; not Epic base64 |
| Log tail / Live Coding / Slate keys | ✅ | **Epic** `LogsToolset` / `LiveCodingToolset` / `SlateInspector` — do not rebuild |

## Recommended next toolsets (Wave 2 remainder)

| Toolset | Why |
|---------|-----|
| `REPCGWorkflowTools` | Spawn graph instance + set params + regenerate for boss dress |
| `REInventoryWorkflowTools` | Wrap `re_inventory_*` for dig→loot proofs |
| `REPhysicsWorkflowTools` | Collision profile / simulate toggles (not PhysicsAsset editor) |

**Still out of scope:** GAS ability graph authoring; full Niagara module-graph DSL; landscape brush sculpt; rebuilding Logs/LiveCoding/Slate.

## When to use Epic vs RE

| Need | Use |
|------|-----|
| Single property read | `ObjectTools.get_properties` |
| Single actor transform | `ActorTools.set_actor_transform` |
| Spawn + props + label + save | `REActorWorkflowTools.spawn_configure_attach_and_verify` |
| 10 actor moves | `REActorWorkflowTools.batch_transform_actors` |
| Find + edit + save 5 assets | `REAssetWorkflowTools.bulk_edit_asset_properties_and_save` |
