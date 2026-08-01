# AGENTS.md — start here in a new chat

This repo is **REAgentTools**: composite Unreal editor workflows for the RE project.

## Read first

1. [`Docs/AGENT_DEFAULTS.md`](Docs/AGENT_DEFAULTS.md) — trivia pushback, RE-first, no Epic fallback
2. [`Docs/REMOTE_CONTROL_MCP.md`](Docs/REMOTE_CONTROL_MCP.md) — MCP vs RC; local proxy `:8001`
3. [`Docs/NIAGARA_BATCHING.md`](Docs/NIAGARA_BATCHING.md) — Niagara: Epic tools in **one** batch, compile once; **no** RE module-graph DSL
4. Skill [`.cursor/skills/reagent-rc-oneshot/SKILL.md`](.cursor/skills/reagent-rc-oneshot/SKILL.md) — **one** Cursor tool call over Unreal RC when MCP discovery fails
5. Optional [`Optional/UnrealMcpProxy/README.md`](Optional/UnrealMcpProxy/README.md) — anti-thrash HTTP sidecar (`:8001`)
6. Optional [`Optional/UnrealWatchMCP/README.md`](Optional/UnrealWatchMCP/README.md) — host-side dialog/lockup MCP when Unreal freezes

Always-on Cursor rules (auto-injected):

- `.cursor/rules/re-context-budget.mdc`
- `.cursor/rules/re-agent-tools.mdc`
- `.cursor/rules/unreal-mcp-proxy.mdc`

## Transport choice

| Situation | What to do |
|-----------|------------|
| Local Editor Agent | Cursor `type: http` → `http://127.0.0.1:8001/mcp` (proxy), not raw `:8000` |
| `mcp-unreal` ready (tools listed) | Call RE*WorkflowTools via MCP |
| Mid-session MCP timeout / “dropped” while Unreal looks open | `unreal-watch.check_unreal` **once** → one batched MCP resubmit → only then RC |
| “Unreal MCP returned an empty reply” | **Resubmit the same call once** — the proxy re-establishes the session on the second empty. Fresh session still empty → `check_unreal`, then RC |
| `call_tool` → “Toolset not found” | Use the registry name from `list_toolsets` (e.g. `re_agent_tools.toolsets.context_tools.REContextTools`), not the bare class name |
| WinError 10048 / address in use on `:8001` | `GET /health` — if proxy identity OK, **reuse**. Never kill/rebind |
| Proxy `/health` says `outdated: true` | Keep using it. Upgrade with one `--restart` only when the editor is idle |
| `mcp-unreal` error/loading/empty, Unreal RC `:30010` up | Skill `reagent-rc-oneshot` → `oneshot_python({...})` → parse `REAGENT_RC_RESULT_BEGIN`…`END` |
| Either path fails once | One RE retry from `recovery`, then STOP — never Epic Scene/Actor/Object chains |

## Local MCP ports

| Port | Role |
|------|------|
| `:8000` | Unreal native `ModelContextProtocol` |
| `:8001` | Anti-thrash proxy (Cursor should use this) |
| `:30010` | Unreal Remote Control (RC oneshot fallback) |

`GET /mcp` on the proxy returns **405** (no SSE) — that is correct. Health is `GET /health`.

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

- Claim REAgentTools aren't reachable just because MCP discovery failed
- Fall back to ad-hoc `_rc_exec.py` Epic scripts for work RE composites cover
- Use the 3-hop file protocol (`rc_request.json` → run → `rc_response.json`) unless log/stdout capture is broken
- Kill / rebind / “fix” the `:8001` proxy on timeout or WinError 10048
- Treat one empty MCP reply as “MCP is wedged, switch to RC” — resubmit once first
- Send `Connection: close` upstream to Unreal — it aborts the SSE stream that carries every `tools/call` result
- Point local Cursor at raw `:8000` when the proxy is the configured path
- Build a Niagara module-graph DSL — use Epic `NiagaraToolsets.*` in **one** `execute_tool_script`, compile once at the end
- Chain per-emitter Niagara MCP calls with re-find / re-compile between each step
- Spam Unreal MCP on timeout — use `unreal-watch.check_unreal` instead
