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
| get_recent_errors_compact | 🚫 | No stable Python log-tail API |
| run_validation_bundle | ✅ | compile + map check + save |

## Batch

| Workflow | Status | Notes |
|----------|--------|-------|
| execute_editor_batch | ✅ | 8 allowlisted actions, $ref, dry_run |

## Project-specific (RE game)

| Domain | Status | Notes |
|--------|--------|-------|
| GAS / abilities | 🚫 | **Project architecture missing** |
| Enemy AI / spawners | 🚫 | **Project architecture missing** |
| Dungeon proc-gen | 🚫 | Manual craft + `re-voxel-world` only |
| Inventory | 🔶 | `re_inventory_bplibrary` bridge exists; no REAgentTools wrapper yet |
| Niagara authoring | 🚫 | **Not implemented yet** — next high-value toolset candidate (`RENiagaraWorkflowTools`) |
| Animation / montage | ✅ | `REAnimWorkflowTools` — Control Rig pose timeline → AnimSequence + Montage |

## Recommended next toolsets (priority)

| Toolset | Why |
|---------|-----|
| **`RENiagaraWorkflowTools`** | Circles, bolts, trails — RE already has many NS assets; agents currently chain Epic Niagara tools or RC. Composites: set system on component + verify, tune user params + save, spawn preview actor, compact emitter/param inspect. |
| `REInventoryWorkflowTools` | Thin wrapper over existing `re_inventory_*` Python — set counts, refresh UI, compact dump (game-specific). |
| `REVoxelWorkflowTools` | Optional later — carve/dig helpers are project scripts today; only wrap if MCP call count hurts. |

**Still out of scope until architecture exists:** GAS ability authoring, enemy AI systems, full Niagara graph node editing (param/tune/assign first; not a Niagara node DSL).

## When to use Epic vs RE

| Need | Use |
|------|-----|
| Single property read | `ObjectTools.get_properties` |
| Single actor transform | `ActorTools.set_actor_transform` |
| Spawn + props + label + save | `REActorWorkflowTools.spawn_configure_attach_and_verify` |
| 10 actor moves | `REActorWorkflowTools.batch_transform_actors` |
| Find + edit + save 5 assets | `REAssetWorkflowTools.bulk_edit_asset_properties_and_save` |
