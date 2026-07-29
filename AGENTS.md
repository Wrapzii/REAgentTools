# AGENTS.md — start here in a new chat

This repo is **REAgentTools**: composite Unreal editor workflows for the RE project.

## Read first

1. [`Docs/AGENT_DEFAULTS.md`](Docs/AGENT_DEFAULTS.md) — trivia pushback, RE-first, no Epic fallback
2. [`Docs/REMOTE_CONTROL_MCP.md`](Docs/REMOTE_CONTROL_MCP.md) — why Cursor Remote Control loses `mcp-unreal` + RC bridge
3. [`Docs/NIAGARA_BATCHING.md`](Docs/NIAGARA_BATCHING.md) — Niagara: Epic tools in **one** batch, compile once; **no** RE module-graph DSL
4. Skill [`.cursor/skills/reagent-rc-oneshot/SKILL.md`](.cursor/skills/reagent-rc-oneshot/SKILL.md) — **one** Cursor tool call over Unreal RC when MCP discovery fails

Always-on Cursor rules (auto-injected):

- `.cursor/rules/re-context-budget.mdc`
- `.cursor/rules/re-agent-tools.mdc`

## Transport choice

| Situation | What to do |
|-----------|------------|
| `mcp-unreal` ready (tools listed) | Call RE*WorkflowTools via MCP |
| `mcp-unreal` error/loading/empty, Unreal RC `:30010` up | Skill `reagent-rc-oneshot` → `oneshot_python({...})` → parse `REAGENT_RC_RESULT_BEGIN`…`END` |
| Either path fails once | One RE retry from `recovery`, then STOP — never Epic Scene/Actor/Object chains |

## One-shot RC (copy/paste)

```python
from re_agent_tools.rc_bridge import oneshot_python
exec(oneshot_python({
    "action": "call",
    "toolset": "REContextTools",
    "tool": "get_editor_context",
    "arguments": {
        "include_level": True,
        "include_selection": True,
        "include_pie": True,
        "include_dirty": True,
    },
}))
```

Code: `Content/Python/re_agent_tools/rc_bridge.py`, `Content/Python/_rc_reagent_exec.py`.

## Do not

- Claim REAgentTools “aren’t reachable” just because MCP discovery failed
- Fall back to ad-hoc `_rc_exec.py` Epic scripts for work RE composites cover
- Use the 3-hop file protocol (`rc_request.json` → run → `rc_response.json`) unless log/stdout capture is broken
- Build a Niagara module-graph DSL — use Epic `NiagaraToolsets.*` in **one** `execute_tool_script`, compile once at the end
- Chain per-emitter Niagara MCP calls with re-find / re-compile between each step
