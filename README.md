# REAgentTools

Composite **Unreal Engine MCP** workflow toolsets. Wraps multi-step editor operations (spawn + configure + verify, batch transforms, asset bulk edit, Control Rig pose → montage, etc.) into single tool calls that return **compact JSON** — fewer MCP round-trips than chaining Epic `SceneTools` / `ObjectTools` / `ActorTools`.

Originally built for the [RE](https://github.com/Wrapzii/RE) project; usable in any UE 5.8 project that has Epic’s Model Context Protocol + ToolsetRegistry stack.

## Requirements

- Unreal Engine **5.8**
- Editor plugins: **Model Context Protocol**, **Toolset Registry** / Editor Toolsets (Epic)
- **No C++ module** — Python content plugin only

## Install

1. Copy this folder into your project as:
   ```
   YourProject/Plugins/REAgentTools/
   ```
2. Enable **RE Agent Tools** in **Edit → Plugins** (or list it in your `.uproject`).
3. Restart the editor if needed.
4. Console: `ModelContextProtocol.RefreshTools`
5. Confirm toolsets appear under `re_agent_tools.toolsets.*` in your MCP client (Cursor, Inspector, etc.).

## Toolsets

| Toolset | Purpose |
|---------|---------|
| `REContextTools` | Capabilities, editor context, resolve/inspect targets |
| `REActorWorkflowTools` | Actor set/verify, spawn, batch transform, delete, organize |
| `REAssetWorkflowTools` | Find, bulk edit, save assets |
| `REBlueprintWorkflowTools` | Inspect, create, defaults, compile |
| `REMaterialWorkflowTools` | MI create/configure/assign |
| `RELevelWorkflowTools` | Open/create level, place actors, map check |
| `REAnimWorkflowTools` | Control Rig pose timeline → AnimSequence / Montage |
| `REValidationWorkflowTools` | Compile/save/validate bundles |
| `REBatchWorkflowTools` | Allowlisted batch executor with `$ref` |
| `REProjectWorkflowTools` | Project architecture notes |

## Docs

- [Docs/USAGE_GUIDE.md](Docs/USAGE_GUIDE.md) — example agent prompts
- [Docs/TOOL_CATALOG.md](Docs/TOOL_CATALOG.md) — tool reference
- [Docs/CAPABILITY_MATRIX.md](Docs/CAPABILITY_MATRIX.md) — supported vs missing
- [Docs/RESEARCH.md](Docs/RESEARCH.md) — environment notes

## Logging

Tool calls append to `Saved/REAgentTools/tool_calls.jsonl` in the host project.

## Limits (`Config/DefaultREAgentTools.ini`)

| Setting | Default |
|---------|---------|
| SearchLimit | 25 |
| MutateLimit | 25 |
| BatchLimit | 20 |
| ResponseSoftLimitBytes | 51200 |

## License

Provided as-is for use with Unreal Editor automation. Adjust this section if you publish under a specific license.
