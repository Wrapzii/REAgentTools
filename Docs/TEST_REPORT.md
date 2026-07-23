# REAgentTools — Test Report

**Status (v1.0.3):** Validated live via Remote Control + MCP HTTP (2026-07-21).

Prior full smoke v1.0.1: **23 pass / 6 fail / 1 skip**.  
Focused retest after reload: component ✓, material ✓, Blueprint create+inspect parent=`/Script/Engine.Actor` ✓.

### Fixes

| Issue | Fix |
|-------|-----|
| `get_component_by_name` missing | `common/component_helpers.py` |
| Blueprint create parent | factory `ParentClass` / `BlueprintEditorLibrary.create_blueprint_asset_with_parent` |
| Blueprint inspect parent | `BlueprintEditorLibrary.get_blueprint_parent_class` + AssetRegistry `ParentClass` tag (not `get_super_class`) |
| Batch smoke ordering | stay on Hub; batch before level ops |

Hot-reload (no full restart): Remote Control `ExecutePythonCommand` → `_reload_re_agent_tools.py`, or console `py "…/_reload_re_agent_tools.py"`, then `ModelContextProtocol.RefreshTools`.

## Prerequisites

- UE 5.8 editor open on `RE.uproject`
- `REAgentTools` plugin enabled
- `ModelContextProtocol` server running (port 8000)
- Console: `ModelContextProtocol.RefreshTools`

## Smoke steps

### 1. Registration

- [ ] Output Log contains `[REAgentTools] Workflow toolsets registered`
- [ ] No Python traceback on editor start

### 2. MCP discovery

- [ ] MCP Inspector or Cursor lists `re_agent_tools.toolsets.context_tools.REContextTools`
- [ ] `get_plugin_capabilities` returns 9 toolsets

### 3. Context

- [ ] `get_editor_context` returns current level path
- [ ] `resolve_targets` with known actor label returns single match
- [ ] Ambiguous label returns error + candidates

### 4. Actor workflow (non-destructive)

- [ ] `spawn_configure_attach_and_verify` spawns `RE_AgentTools_SmokeTest` actor
- [ ] `set_actor_properties_and_verify` readback matches
- [ ] `delete_actors_validated` with `dry_run=true` lists candidate without destroying

### 5. Asset workflow

- [ ] `find_assets_compact` under `/Game/RE` returns ≤25 paths
- [ ] `save_assets` on known clean asset succeeds

### 6. Batch

- [ ] `execute_editor_batch` dry_run=true completes without mutation
- [ ] stop_on_error=true halts on invalid action name

### 7. Logging

- [ ] `Saved/REAgentTools/tool_calls.jsonl` receives entries after calls

### 8. Cleanup

- [ ] Delete smoke test actor (dry_run=false after confirm)

## Known limitations (expected)

- `get_recent_errors_compact` returns unsupported — no log tail API
- `run_map_check` may return unsupported note on some builds
- GAS/enemy/dungeon workflows not present (by design)

## Regression

After UE or plugin updates, re-run steps 1–3 and one actor workflow test.
