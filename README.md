# REAgentTools

Composite MCP workflow toolsets for the **RE** Unreal project. Wraps multi-step editor operations (spawn + configure + verify, batch transforms, asset bulk edit) into single tool calls that return **compact JSON strings** — reducing MCP round-trips vs chaining Epic `SceneTools` / `ObjectTools` / `ActorTools`.

**Agents (new chat):** start at [`AGENTS.md`](AGENTS.md) → [`Docs/AGENT_DEFAULTS.md`](Docs/AGENT_DEFAULTS.md). Prefer REAgentTools over Epic one-shots. If Cursor Remote Control fails `mcp-unreal` discovery, use skill **`reagent-rc-oneshot`** ([docs](Docs/REMOTE_CONTROL_MCP.md)) — one RC exec via `oneshot_python`, not three file hops.

## Requirements

- UE **5.8** (`C:\Program Files\Epic Games\UE_5.8`)
- Project plugins: `ModelContextProtocol`, `ToolsetRegistry` (via `EditorToolset` / `AllToolsets`)
- **No C++ module** — Python-only content plugin

## Enable

1. Plugin is listed in `RE.uproject` → `Plugins` → `REAgentTools` (Enabled).
2. Restart Unreal Editor or enable **RE Agent Tools** in **Edit → Plugins**.
3. Console: `ModelContextProtocol.RefreshTools`
4. Verify in MCP Inspector or Cursor `unreal-mcp` — toolsets under `re_agent_tools.toolsets.*`

## Toolsets (v1.2.2 — 15)

| Toolset | Purpose |
|---------|---------|
| `REContextTools` | Capabilities, editor context, resolve/inspect targets |
| `REActorWorkflowTools` | Spawn/place/rotate/batch transform/delete/organize |
| `REDressWorkflowTools` | Cave/hub mesh place, ring scatter, snap-to-floor |
| `RENiagaraWorkflowTools` | Place/assign Niagara systems + user params (not module-graph DSL) |
| `RECharacterWorkflowTools` | Character mesh, combat montages, sockets |
| `RELightingWorkflowTools` | Environment light inventory + mood presets |
| `RECaptureWorkflowTools` | Path-only screenshots, FX mat preview, PIE cast+capture |
| `REAnimWorkflowTools` | Control Rig pose → AnimSequence/Montage |
| `REAssetWorkflowTools` | Find, bulk edit, save assets |
| `REBlueprintWorkflowTools` | Inspect, create, defaults, compile |
| `REMaterialWorkflowTools` | MI create/configure/assign |
| `RELevelWorkflowTools` | Open/create level, place actors, map check |
| `REValidationWorkflowTools` | Compile/save/validate bundles |
| `REBatchWorkflowTools` | Allowlisted batch executor with `$ref` |
| `REProjectWorkflowTools` | Project architecture notes (honest gaps) |

## Docs

- [AGENTS.md](AGENTS.md) — new-chat bootstrap for Cursor agents
- [REMOTE_CONTROL_MCP.md](Docs/REMOTE_CONTROL_MCP.md) — Cursor Remote Control MCP failures + RC oneshot bridge
- [NIAGARA_BATCHING.md](Docs/NIAGARA_BATCHING.md) — Epic Niagara in one batch; compile once; no RE DSL
- [Optional/UnrealMcpProxy](Optional/UnrealMcpProxy/README.md) — anti-thrash HTTP/stdio sidecar (`:8001` → Unreal `:8000`)
- [Optional/UnrealWatchMCP](Optional/UnrealWatchMCP/README.md) — host-side dialog/lockup MCP (`check_unreal` / `dismiss_dialog`)
- [VISUAL_LOOP.md](Docs/VISUAL_LOOP.md) — Epic Logs/LiveCoding/Slate vs RECapture
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

Prefer **RE*WorkflowTools** composites before chaining low-level Epic tools. See `Content/RE/UNREAL_MCP_TOOL_MAP.md` and `.cursor/rules/re-agent-tools.mdc`. When MCP is down in Remote Control, use skill `reagent-rc-oneshot` (`rc_bridge.oneshot_python`) — one RC exec, not three file hops.
