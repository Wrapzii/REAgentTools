# REAgentTools — Tool Catalog

MCP `toolset_name` prefix: `re_agent_tools.toolsets.<module>.<ClassName>`

## REContextTools

| tool_name | Args (summary) | Returns |
|-----------|----------------|---------|
| get_plugin_capabilities | — | JSON: version, toolsets, limits |
| get_editor_context | include_level, include_selection, include_pie, include_dirty | JSON: context snapshot |
| resolve_targets | actor_queries_json, asset_paths_json | JSON: resolved_targets |
| inspect_targets_compact | actor_queries_json, asset_paths_json, properties_json | JSON: targets + properties |

## REActorWorkflowTools

| tool_name | Args (summary) | Returns |
|-----------|----------------|---------|
| set_actor_properties_and_verify | actor_label, properties_json, save_level | JSON WorkflowResult |
| set_component_properties_and_verify | actor_label, component_name, properties_json | JSON WorkflowResult |
| spawn_configure_attach_and_verify | class_path, location/rotation/scale JSON, properties_json, actor_label, folder_path, attach_parent_label | JSON WorkflowResult |
| place_from_asset_and_verify | asset_path, actor_label, location/rotation/scale JSON, folder_path | JSON WorkflowResult — StaticMesh / Blueprint / class |
| batch_transform_actors | transforms_json (list of {label, location?, rotation?, scale?}) | JSON WorkflowResult |
| rotate_actors_and_verify | rotations_json `[{label, rotation}|{label, add_yaw}]` | JSON WorkflowResult |
| delete_actors_validated | actor_labels_json, dry_run (default true) | JSON WorkflowResult |
| organize_actors | organization_json (folder, new_label, tags) | JSON WorkflowResult |

## RENiagaraWorkflowTools (v1.1)

| tool_name | Args | Returns |
|-----------|------|---------|
| place_niagara_system_and_verify | system_path, actor_label, loc/rot/scale JSON, folder_path, auto_activate, parameters_json | JSON WorkflowResult |
| assign_niagara_system_to_component | actor_label, system_path, component_name?, reset_overrides, auto_activate | JSON WorkflowResult |
| set_niagara_user_parameters_and_verify | actor_label, parameters_json, component_name? | JSON WorkflowResult |
| inspect_niagara_compact | actor_label, component_name? | JSON compact (no stack dump) |

## REDressWorkflowTools (v1.1)

| tool_name | Args | Returns |
|-----------|------|---------|
| place_static_mesh_and_verify | mesh_path, actor_label, loc/rot/scale JSON, folder_path, tags_json | JSON + bounds_cm |
| batch_place_static_meshes | placements_json, folder_path | JSON WorkflowResult |
| scatter_static_meshes_ring | mesh_path, label_prefix, center_json, radius_cm, count, scale_json, random_yaw, seed, folder_path, tag | JSON WorkflowResult |
| snap_actors_to_floor | actor_labels_json, trace_up_cm, trace_down_cm | JSON WorkflowResult |

## RECharacterWorkflowTools (v1.1)

| tool_name | Args | Returns |
|-----------|------|---------|
| inspect_character_compact | blueprint_path? (default BP_RECharacter) | mesh + montage summary |
| set_character_mesh_and_verify | skeletal_mesh_path, blueprint_path?, compile_save | JSON WorkflowResult |
| set_visual_combat_montages | montages_json, blueprint_path?, compile_save | JSON WorkflowResult |
| list_mesh_sockets_compact | skeletal_mesh_path? or blueprint_path?, limit | socket name list |

## RELightingWorkflowTools (v1.1)

| tool_name | Args | Returns |
|-----------|------|---------|
| get_environment_lights_compact | — | directional / sky / fog / atmosphere labels |
| list_mood_presets | — | cave_cool, bright_neutral, boss_dim |
| apply_mood_lighting | mood, create_if_missing | JSON WorkflowResult — edits existing lights |
| set_light_properties_and_verify | actor_label, properties_json | JSON WorkflowResult |

## REAnimWorkflowTools

| tool_name | Args (summary) | Returns |
|-----------|----------------|---------|
| list_pose_presets | — | JSON: named Control Rig presets (`guard_r`, `coil_r`, `slash_contact`, …) |
| get_animation_pipeline_notes | — | JSON: UE authoring path + Comfy skeletal pointers |
| author_controlrig_pose_timeline | folder_path, sequence_name, poses_json, binding_label?, control_rig_path?, character_class_path?, end_frame?, save | JSON WorkflowResult — keys Control Rig poses into a LevelSequence |
| export_sequence_to_anim_and_montage | level_sequence_path, anim_folder, anim_name, montage_name?, binding_label?, skeleton_path?, start/end_frame?, fps?, notifies_json?, save | JSON WorkflowResult — bake → AnimSequence + Montage |
| create_montage_from_anim | anim_sequence_path, montage_folder, montage_name, skeleton_path?, notifies_json?, save | JSON WorkflowResult |
| author_clip_from_pose_timeline | folder_path, clip_stem, poses_json, notifies_json?, end_frame?, fps?, save | JSON WorkflowResult — one-shot pose → LS → A_ → AM_ |

`poses_json` accepts presets and/or per-control transforms:
`[{"frame":0,"preset":"guard_r"},{"frame":9,"preset":"slash_contact","bones":{"hand_r_fk_ctrl":[0,0,0,10,-20,35]}}]`

`notifies_json` stamps a compact notify plan (e.g. `{"trail_on":5,"hit":9}`) onto the montage for follow-up wiring.

After plugin change: `ModelContextProtocol.RefreshTools` (or editor restart / `reload_workflow_modules`).

## REAssetWorkflowTools

| tool_name | Args | Returns |
|-----------|------|---------|
| find_assets_compact | path, class_name, name_filter, limit | JSON asset list |
| bulk_edit_asset_properties_and_save | edits_json [{path, properties_json}] | JSON WorkflowResult |
| save_assets | asset_paths_json | JSON WorkflowResult |

## REBlueprintWorkflowTools

| tool_name | Args | Returns |
|-----------|------|---------|
| inspect_blueprint_compact | blueprint_path, properties_json | JSON blueprint info |
| create_blueprint_from_class | folder_path, asset_name, parent_class_path | JSON WorkflowResult |
| set_class_defaults_compile_save | blueprint_path, properties_json | JSON WorkflowResult |
| compile_blueprints | blueprint_paths_json | JSON WorkflowResult |
| create_or_update_blueprint | folder_path, asset_name, parent_class_path, properties_json | JSON WorkflowResult |

## REMaterialWorkflowTools

| tool_name | Args | Returns |
|-----------|------|---------|
| create_material_instance_configure_save | folder_path, asset_name, parent_material_path, parameters_json | JSON WorkflowResult |
| update_material_instance_parameters | material_instance_path, parameters_json, save | JSON WorkflowResult |
| assign_materials_to_mesh_components | assignments_json | JSON WorkflowResult |
| create_assign_material_instance | folder + parent + params + actor_label + slot | JSON WorkflowResult |

## RELevelWorkflowTools

| tool_name | Args | Returns |
|-----------|------|---------|
| open_or_create_level | level_path, create_if_missing | JSON WorkflowResult |
| place_configure_save_actors | placements_json, save_level | JSON WorkflowResult |
| run_map_check | — | JSON map_check info |

## REValidationWorkflowTools

| tool_name | Args | Returns |
|-----------|------|---------|
| compile_save_validate | blueprint_paths_json, save_all_dirty | JSON WorkflowResult |
| get_recent_errors_compact | max_lines | JSON (unsupported note) |
| run_validation_bundle | blueprint_paths_json, run_map_check_flag, save_dirty | JSON WorkflowResult |

## REBatchWorkflowTools

| tool_name | Args | Returns |
|-----------|------|---------|
| execute_editor_batch | operations_json, dry_run, stop_on_error | JSON WorkflowResult |

Allowlisted actions: `resolve_actor`, `spawn_actor`, `set_actor_properties`, `set_actor_transform`, `save_level`, `compile_blueprint`, `set_asset_properties`, `save_asset`.

Actor id field aliases (any one): `label`, `actor_label`, `name`.

## REProjectWorkflowTools

| tool_name | Args | Returns |
|-----------|------|---------|
| reload_workflow_modules | — | Hot-reload Python + re-register (after first load of this tool) |
| get_plugin_project_notes | — | JSON architecture notes |
