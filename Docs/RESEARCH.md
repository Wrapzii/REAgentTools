# REAgentTools — Research

## Environment (verified)

| Item | Value |
|------|-------|
| Engine | UE **5.8** binary at `C:\Program Files\Epic Games\UE_5.8` |
| Project | `RE.uproject`, `EngineAssociation` 5.8 |
| Game C++ | **None** — Blueprint + Python |
| Project plugins (pre-REAgentTools) | `VoxelFree` only |
| MCP stack | `ModelContextProtocol`, `AllToolsets`, `EditorToolset`, `MCPClientToolset`, engine toolsets |
| Toolset Registry | `Engine/Plugins/Experimental/ToolsetRegistry` |

## Discovery pattern

1. Plugin `Content/Python/init_unreal.py` runs on editor Python init.
2. Imports `re_agent_tools.toolsets` and calls `_registration.register()`.
3. `Registration([...])` from `toolset_registry.registration` registers each `@unreal.uclass()` `ToolsetDefinition`.
4. MCP server exposes tools after `ModelContextProtocol.RefreshTools`.

Full MCP tool name example:

```
re_agent_tools.toolsets.context_tools.REContextTools.get_editor_context
```

## Epic EditorToolset pattern (reference)

- Class: `@unreal.uclass() class X(unreal.ToolsetDefinition)`
- Public tools: `@toolset_registry.tool_call` + `@staticmethod` with full type hints
- Helpers: `toolset_registry.helpers.require_editable`
- Properties: `unreal.ToolsetLibrary.get_object_properties` / `set_object_properties` (JSON string)
- Returns: **str JSON** preferred for workflow composites (tiny MCP schemas)

## REAgentTools design

- **Content-only** plugin: `"CanContainContent": true`, `"EditorOnly": true`, no C++ module
- Depends on `ToolsetRegistry` (pulls `PythonScriptPlugin`, `EditorScriptingUtilities`)
- Does **not** fork Epic MCP / ToolsetRegistry
- Composite tools = one MCP call for multi-step editor work + unified `WorkflowResult` JSON

## RE naming conventions

| Prefix / path | Use |
|---------------|-----|
| `RE_` | Authored actors, BPs, data |
| `SM_` | Static meshes |
| `VW_RE_` | Voxel world actors |
| `Content/RE/` | Project design docs, tool maps, runbooks |

## Limits config

`Plugins/REAgentTools/Config/DefaultREAgentTools.ini` — search/mutate/batch caps and response soft limit.
