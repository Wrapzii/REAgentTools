# REAgentTools

Composite MCP workflow toolsets for the **RE** Unreal project. Wraps multi-step editor operations (spawn + configure + verify, batch transforms, asset bulk edit) into single tool calls that return **compact JSON strings** — reducing MCP round-trips vs chaining Epic `SceneTools` / `ObjectTools` / `ActorTools`.

## Requirements

- UE **5.8** (`C:\Program Files\Epic Games\UE_5.8`)
- Project plugins: `ModelContextProtocol`, `ToolsetRegistry` (via `EditorToolset` / `AllToolsets`)
- **No C++ module** — Python-only content plugin

## Enable

1. Plugin is listed in `RE.uproject` → `Plugins` → `REAgentTools` (Enabled).
2. Restart Unreal Editor or enable **RE Agent Tools** in **Edit → Plugins**.
3. Console: `ModelContextProtocol.RefreshTools`
4. Verify in MCP Inspector or Cursor `unreal-mcp` — toolsets under `re_agent_tools.toolsets.*`

## Toolsets (v1.1.0 — 14)

| Toolset | Purpose |
|---------|---------|
| `REContextTools` | Capabilities, editor context, resolve/inspect targets |
| `REActorWorkflowTools` | Spawn/place/rotate/batch transform/delete/organize |
| `REDressWorkflowTools` | Cave/hub mesh place, ring scatter, snap-to-floor |
| `RENiagaraWorkflowTools` | Place/assign Niagara systems + user params |
| `RECharacterWorkflowTools` | Character mesh, combat montages, sockets |
| `RELightingWorkflowTools` | Environment light inventory + mood presets |
| `REAnimWorkflowTools` | Control Rig pose → AnimSequence/Montage |
| `REAssetWorkflowTools` | Find, bulk edit, save assets |
| `REBlueprintWorkflowTools` | Inspect, create, defaults, compile |
| `REMaterialWorkflowTools` | MI create/configure/assign |
| `RELevelWorkflowTools` | Open/create level, place actors, map check |
| `REValidationWorkflowTools` | Compile/save/validate bundles |
| `REBatchWorkflowTools` | Allowlisted batch executor with `$ref` |
| `REProjectWorkflowTools` | Project architecture notes (honest gaps) |

## Docs

- [EXPAND_PLAN.md](Docs/EXPAND_PLAN.md) — research + wave roadmap
- [CAPABILITY_MATRIX.md](Docs/CAPABILITY_MATRIX.md) — supported vs missing
- [USAGE_GUIDE.md](Docs/USAGE_GUIDE.md) — example prompts
- [TOOL_CATALOG.md](Docs/TOOL_CATALOG.md) — tool reference
- [BENCHMARK_REPORT.md](Docs/BENCHMARK_REPORT.md) — verified wire A/B
- [TEST_REPORT.md](Docs/TEST_REPORT.md) — manual smoke steps
- [RESEARCH.md](Docs/RESEARCH.md) — environment and discovery

## Logging

Tool calls append to `Saved/REAgentTools/tool_calls.jsonl`.

## Limits (DefaultREAgentTools.ini)

| Setting | Default |
|---------|---------|
| SearchLimit | 25 |
| MutateLimit | 25 |
| BatchLimit | 20 |
| ResponseSoftLimitBytes | 51200 |

## Agent preference

Prefer **RE*WorkflowTools** composites before chaining low-level Epic tools. See `Content/RE/UNREAL_MCP_TOOL_MAP.md` and `.cursor/rules/re-agent-tools.mdc`.
