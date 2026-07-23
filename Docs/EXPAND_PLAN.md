# REAgentTools Expansion Plan (v1.1+)

**Date:** 2026-07-23  
**Goal:** Fewer MCP round-trips for the work RE actually does — cave dress, VFX, character wiring, lights — so agents open **one** composite instead of chaining 8–15 Epic tools that fail midway.

## Research summary

### What Epic already exposes (raw, chatty)

| Domain | Epic toolsets (count) | Pain today |
|--------|----------------------:|------------|
| Niagara | Info + Component + Blueprint + System + Assets (~56) | Create/tune/assign = many calls; schemas huge |
| PCG | PCGToolset (~30) + Spatial | Graph authoring is powerful but token-heavy |
| Physics | PhysicsAssetToolset (17) | Skeletal physics assets — **not** world rigid-body dress |
| Sequencer / Control Rig | 100s | Already partially wrapped (`REAnimWorkflowTools`) |
| Scene / Actor / Object | Core Epic | Placement works but agents invent 6-step recipes |

### What RE skills actually need

| Skill / loop | Missing composite |
|--------------|-------------------|
| `re-niagara-fx` / circles / bolts | Place NS + set User params + verify + optional save |
| `re-megascans-dress` / cave dress | Place mesh asset + label/folder/tags + scale + batch scatter |
| `re-level-craft` | Mood light/fog one-shot; don’t duplicate sun/sky |
| Character / combat visual | Set mesh/montage/component defaults + compile/save |
| Scale gate | Bounds readback baked into place tools |
| Procedural | PCG spawn graph instance + set params (wave 2) |

### Design rules (keep token burn low)

1. **One objective = one tool** with `set_*_and_verify` readback.
2. Compact JSON `WorkflowResult` only (paths, labels, counts, warnings).
3. **No** full Niagara stack dumps / PCG graph dumps unless `detail=verbose` later.
4. Prefer wrapping **proven Python** (`EditorActorSubsystem`, Niagara component APIs) over rediscovering Epic schemas.
5. Honest `unsupported` when architecture missing (GAS graph authoring stays out).

---

## Wave plan

### Wave 1 — ship now (v1.1.0)

| Toolset | Tools | Replaces ~N Epic calls |
|---------|-------|------------------------:|
| **RENiagaraWorkflowTools** | `place_niagara_system_and_verify`, `set_niagara_user_parameters_and_verify`, `assign_niagara_system_to_component`, `inspect_niagara_compact` | 4–10 |
| **REDressWorkflowTools** | `place_static_mesh_and_verify`, `batch_place_static_meshes`, `scatter_static_meshes_ring`, `snap_actors_to_floor` | 5–20 |
| **RECharacterWorkflowTools** | `inspect_character_compact`, `set_character_mesh_and_verify`, `set_visual_combat_montages`, `list_mesh_sockets_compact` | 4–12 |
| **RELightingWorkflowTools** | `get_environment_lights_compact`, `apply_mood_lighting`, `set_light_properties_and_verify` | 3–8 |

Also extend **REActorWorkflowTools**: `place_from_asset_and_verify`, `rotate_actors_and_verify` (manual entity place/rotate without dress-specific mesh logic).

### Wave 2a — shipped (v1.2.0)

| Toolset | Why |
|---------|-----|
| **RECaptureWorkflowTools** | Viewport / mat preview / PIE cast → `Saved/Screenshots` path-only + max_dimension/JPEG |

See [VISUAL_LOOP.md](VISUAL_LOOP.md) — Epic Logs / LiveCoding / Slate already work; do not rebuild them.

### Wave 2b — next

| Toolset | Why |
|---------|-----|
| **REPCGWorkflowTools** | `spawn_pcg_graph_instance`, `set_pcg_params_and_regenerate` — boss dress / scatter |
| **REPhysicsWorkflowTools** | Limited: enable simulate on component, set collision profile batch — **not** full PhysicsAsset editor |
| **REInventoryWorkflowTools** | Wrap `re_inventory_*` for dig→loot proofs |

### Wave 3 — only if proven pain

- Foliage/HISM batch instance toolset  
- Gameplay tag + DataTable row helpers  
- Soft object path bulk retarget on BP defaults  

### Explicit non-goals

- Niagara module-graph DSL (use Epic System tools + skill)  
- GAS ability graph authoring (architecture)  
- Landscape brush sculpt (project limit — see `re-landscape-limits`)  
- Moving/resizing the editor window  

---

## Acceptance

Wave 1 DONE when:

1. Toolsets register (`ModelContextProtocol.RefreshTools`)  
2. Catalog + capability matrix updated  
3. Smoke: place mesh, place Niagara, mood light — each ≤2 MCP calls  
4. Mirrored to https://github.com/Wrapzii/REAgentTools  

## Version

`1.0.x` → **`1.1.0`** Wave 1 → **`1.2.0`** RECapture / visual loop.
