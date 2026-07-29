# Agent defaults — REAgentTools first (sample)

**Audience:** Cursor / MCP agents working in an Unreal project that has this plugin.  
**New chat:** also open [`AGENTS.md`](../AGENTS.md) and skill `reagent-rc-oneshot` if `mcp-unreal` is down.  
**Goal:** Cut token burn. Every tool round-trip re-reads ~the full chat prefix (~150–200k counted tokens, mostly cache). Trivial “just checking” calls are not free.

---

## 1. Push back on trivia before calling tools

| User says | Agent does **not** | Agent does |
|-----------|--------------------|------------|
| “What level are we on?” | Call `get_current_level` or any MCP | Ask: do you want a real context pull, or is a task next? |
| “Is PIE on?” | Call a tool “just to see” | Same pushback |
| “Move that rock 50cm and save” | — | Call RE tools (batch / composite) |

**Sample reply (trivia):**

> Checking that costs a full Unreal MCP round-trip (whole context re-read). Want me to run `get_editor_context` via REAgentTools, or are you about to give an edit task I should batch?

**Sample reply (real work):** skip the lecture; call RE tools immediately.

---

## 2. When Unreal work is real — REAgentTools first

**Wrong (burns + trains Epic habit):**

```text
call_tool SceneTools.get_current_level
call_tool SceneTools.find_actors …
call_tool ActorTools.set_actor_transform …
```

**Right — single context pull:**

```json
{
  "toolset_name": "re_agent_tools.toolsets.context_tools.REContextTools",
  "tool_name": "get_editor_context",
  "arguments": {
    "include_level": true,
    "include_selection": true,
    "include_pie": true,
    "include_dirty": true
  }
}
```

Returns compact `context`: `level`, `selected_actors`, `selected_assets`, `pie_active`, `dirty_packages` / `dirty_count`.

**Right — one batch for a multi-step objective:**

```json
{
  "toolset_name": "re_agent_tools.toolsets.batch_workflow_tools.REBatchWorkflowTools",
  "tool_name": "execute_editor_batch",
  "arguments": {
    "dry_run": false,
    "stop_on_error": true,
    "operations_json": "[{\"id\":\"c1\",\"action\":\"get_editor_context\"},{\"id\":\"f1\",\"action\":\"find_actors\",\"name\":\"PlayerStart\"},{\"id\":\"r1\",\"action\":\"resolve_actor\",\"label\":\"$f1\"},{\"id\":\"t1\",\"action\":\"set_actor_transform\",\"actor_label\":\"$r1\",\"location\":[0,0,100]},{\"id\":\"s1\",\"action\":\"save_level\"}]"
  }
}
```

Allowlisted batch actions include: `get_editor_context`, `find_actors`, `resolve_actor`, `resolve_asset`, `spawn_actor`, `set_actor_transform`, `set_actor_properties`, `compile_blueprint`, `set_asset_properties`, `save_asset`, `save_level`.

### 2b. Cursor Remote Control — MCP down ≠ REAgentTools down

If `mcp-unreal` is `serverStatus: error` / no tools (common in Remote Control), **do not** say composites are unreachable and **do not** fall back to Epic or ad-hoc `_rc_exec.py` scripts.

Use **one** RC/Python exec (skill `reagent-rc-oneshot`):

```python
from re_agent_tools.rc_bridge import oneshot_python
exec(oneshot_python({
  "action": "call",
  "toolset": "REContextTools",
  "tool": "get_editor_context",
  "arguments": {"include_level": true, "include_selection": true}
}))
```

Parse `REAGENT_RC_RESULT_BEGIN`…`END` from the RC log/stdout. That is ~1 Cursor round-trip (same order as one MCP call). Avoid the 3-hop file protocol unless capture is broken.

Full write-up: [`REMOTE_CONTROL_MCP.md`](./REMOTE_CONTROL_MCP.md).

### 2c. Niagara system authoring — Epic tools, batched (no RE DSL)

**Do not build a Niagara module-graph DSL in REAgentTools.** Epic already has `NiagaraToolsets.*`.

| Job | Tooling |
|-----|---------|
| Place / assign / User params / compact inspect | `RENiagaraWorkflowTools` (one composite) |
| Create system, emitters, renderers, stack edits | Epic Niagara toolsets via **one** `ProgrammaticToolset.execute_tool_script` |

**Wrong:** N separate MCP calls — create → find → add emitter → find → set data → compile → … (console spam = context cost).

**Right:** one script that does all planned mutations, **compile once at the end**, save once, return compact summary. Then RE place/params if needed.

Canonical: [`NIAGARA_BATCHING.md`](./NIAGARA_BATCHING.md).

---

## 3. On failure — stay in RE (no Epic “manual mode”)

Every WorkflowResult includes `agent_policy`:

- `forbid_epic_manual_fallback: true`
- `max_recovery_mcp_calls: 1`
- On error: use `recovery.candidates` / `recovery.suggested_ops` → **one** more `execute_editor_batch` (or same composite) → then **STOP**

**Forbidden after a RE failure:** Epic `SceneTools` / `ActorTools` / `ObjectTools`, `list_toolsets`, `describe_toolset`.

---

## 4. Cursor wiring (host project)

Keep this light — prefer one rule, not a new skill:

| Artifact | Role |
|----------|------|
| `AGENTS.md` | New-chat bootstrap (transport table + oneshot copy/paste) |
| `.cursor/rules/re-context-budget.mdc` | Always-on one-liner pointing at RE composites + RC skill |
| `.cursor/rules/re-agent-tools.mdc` | Always-on: trivia pushback + RE-first + RC oneshot |
| `.cursor/skills/reagent-rc-oneshot/SKILL.md` | One-shot RC transport when MCP discovery fails |
| `Content/RE/UNREAL_MCP_TOOL_MAP.md` | Signatures; RE section before Epic chains |
| `Docs/REMOTE_CONTROL_MCP.md` | Why Remote Control loses MCP + RC bridge protocol |
| This file (`Docs/AGENT_DEFAULTS.md`) | Human + agent sample (canonical in this repo) |
| `Docs/NIAGARA_BATCHING.md` | Epic Niagara batch + compile-once; no RE module-graph DSL |

Do **not** add a dedicated skill just for “use REAgentTools.”

---

## 5. Quick checklist

- [ ] Trivia? Push back — no tool yet  
- [ ] Real work? `get_editor_context` or `execute_editor_batch` first  
- [ ] MCP discovery failed in Remote Control? `oneshot_python` / skill `reagent-rc-oneshot` — not Epic  
- [ ] Never Epic `get_current_level` alone  
- [ ] Niagara *authoring*? Epic tools in **one** `execute_tool_script`; compile once at end — no RE DSL  
- [ ] Niagara *placement/params*? `RENiagaraWorkflowTools`  
- [ ] Failure? One RE retry from `recovery`, then stop  
- [ ] Returns stay compact (paths / counts / warnings / errors)
