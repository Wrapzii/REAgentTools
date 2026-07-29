# Niagara authoring — Epic tools, one batch, compile once

**Do not build a Niagara module-graph DSL inside REAgentTools.** Epic already ships `NiagaraToolsets.*`. Rebuilding that surface burns tokens and duplicates Engine work.

## Split of responsibility

| Job | Use | MCP round-trips |
|-----|-----|-----------------|
| Place / assign system / set User params / compact inspect | `RENiagaraWorkflowTools` | 1 composite |
| Create system, add emitters, set renderer/stack data, rename, enable/disable | **Epic** `NiagaraToolsets.NiagaraToolset_*` | **1** via `ProgrammaticToolset.execute_tool_script` |
| Save package | Epic AssetTools or RE save helpers | Prefer last step of same script |

## Hard rules (token burn)

1. **Never** chain one Epic Niagara `call_tool` per emitter / property / find.
2. **Never** re-`GetSystemSummary` / re-find the system between every mutation when you already know the `refPath`.
3. **Never** compile / request compile after each small edit — **compile once at the end** of the batch (or once after the script).
4. **Never** invent RE “module graph DSL” / custom graph writers for Niagara stacks.
5. Prefer **one** `editor_toolset.toolsets.programmatic.ProgrammaticToolset` → `execute_tool_script` that runs the whole authoring plan, then save.

## Anti-pattern (expensive)

```text
call_tool CreateNiagaraSystem
call_tool GetSystemSummary          # again
call_tool AddEmitterFromAsset       # emitter 1
call_tool GetSystemSummary          # again
call_tool SetEmitterEnabled         # …
call_tool Compile…                  # every time
… × N emitters × M properties
```

Each hop re-reads a huge chat prefix. Console spam = money.

## Correct pattern (one script)

```json
{
  "toolset_name": "editor_toolset.toolsets.programmatic.ProgrammaticToolset",
  "tool_name": "execute_tool_script",
  "arguments": {
    "script": "// create system once\n// add all emitters\n// set all known renderer/material/user data\n// optional: one compile / request compile\n// save asset once\nreturn { ok: true, system: '...' };"
  }
}
```

Sketch of intent inside the script (tool names must match the live Epic schemas — discover once, then batch):

1. `CreateNiagaraSystem` (or load existing `refPath`)
2. Resolve emitters you need (keep names/refs in locals — do not re-query the world between steps unless a step failed)
3. Apply **all** planned emitter/renderer/parameter edits
4. **One** compile / dirty flush at the end
5. Save once
6. Return a compact `{ system, emitters, warnings }` object

Then, for level proof: **one** `RENiagaraWorkflowTools.place_niagara_system_and_verify` (or assign + set params).

## When MCP discovery is down

Same batching rules over RC (`reagent-rc-oneshot` / host `Scripts/rc_exec.py`). Still one script / one composite — not a loop of Epic one-shots.

## Out of scope for REAgentTools

- Full Niagara module-graph DSL
- Dumping entire stacks into chat
- Replacing Epic System / Emitter / Renderer toolsets

See also: [`CAPABILITY_MATRIX.md`](./CAPABILITY_MATRIX.md), [`AGENT_DEFAULTS.md`](./AGENT_DEFAULTS.md), [`EXPAND_PLAN.md`](./EXPAND_PLAN.md).
