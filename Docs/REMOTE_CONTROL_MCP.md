# Remote Control vs Unreal MCP (REAgentTools)

## What’s going wrong

Agents in **Cursor Remote Control / Agents Window / mobile cloud** often report:

> `mcp-unreal` → `serverStatus: error` — “failed during live tool discovery”  
> then fall back to Unreal Remote Control Python (`_rc_exec.py` → `127.0.0.1:30010`) and skip REAgentTools.

**Local Editor Agent/Chat** usually sees `mcp-unreal` fine. Remote sessions assemble MCP tools differently.

This is a **Cursor session / MCP routing limitation**, not a missing plugin. REAgentTools still register in-editor; MCP is only one transport.

### Cursor facts (product)

| Surface | Local `~/.cursor/mcp.json` / IDE MCP | Unreal on your PC |
|---------|--------------------------------------|-------------------|
| Editor Agent/Chat | Honored | Works (stdio or localhost HTTP from your machine) |
| Remote Control / Agents Window | Known rough edge — local MCP often not exposed to the agent | Editor may still be reachable via Unreal RC `:30010` |
| Cloud Agent on managed VM | Dashboard MCP only | `localhost` Unreal is **not** on that VM |
| My Machines worker | Dashboard MCP: **stdio** runs on your machine; **HTTP** is called from Cursor’s backend (so `localhost` Unreal fails) | Use **stdio** MCP on the worker, or the RC bridge below |

Staff guidance (forum): until Remote Control MCP parity lands, Editor Agent/Chat is the reliable path for local MCP. For Cloud/My Machines, register MCP in the [agents MCP dropdown](https://cursor.com/agents) — not Desktop-only config.

## Fix the Cursor side (when you want MCP in remote sessions)

1. Keep Unreal Editor open with `ModelContextProtocol` + `ModelContextProtocol.RefreshTools`.
2. Add/enable **mcp-unreal** under Cloud Agents MCP (cursor.com/agents), not only Desktop Settings.
3. Prefer **stdio** transport on a **My Machines** worker that shares the PC with Unreal. Avoid dashboard **HTTP → `http://127.0.0.1:8000`** for cloud/remote — Cursor’s backend cannot see your loopback.
4. Toggle MCP off/on or start a fresh agent if discovery stuck on `loading`/`error`.
5. For guaranteed Unreal tool use today: use **Editor** Agent/Chat (not Remote Control), or the RC bridge below.

## Fix the agent side — RC bridge (prefer one-shot)

| File | Role |
|------|------|
| `Content/Python/re_agent_tools/rc_bridge.py` | `call_tool` / `run_request` / `oneshot_python` |
| `Content/Python/_rc_reagent_exec.py` | RC / `py` entrypoint |
| `.cursor/skills/reagent-rc-oneshot/SKILL.md` | Agent procedure for one Cursor turn |

### Preferred protocol — one Cursor tool call

Execute a single Python block via host `_rc_exec.py` / Unreal `ExecutePythonCommand`:

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

Parse log/stdout between:

```text
REAGENT_RC_RESULT_BEGIN
{...compact WorkflowResult...}
REAGENT_RC_RESULT_END
```

**Token cost:** ~1 Cursor round-trip — same ballpark as one MCP `CallMcpTool`. Not a free path; just not 3× worse.

Batch / list examples live in the skill. Same composites as MCP; only the transport differs.

### Fallback protocol — file hop (avoid)

Only if your RC wrapper cannot return log/stdout:

1. Write `Saved/REAgentTools/rc_request.json`
2. Run `_rc_reagent_exec.py`
3. Read `Saved/REAgentTools/rc_response.json`

That is **3** Cursor turns for one composite — worse token burn than MCP.

## Agent policy (non-negotiable)

| Situation | Do | Don’t |
|-----------|----|-------|
| `mcp-unreal` ready | Call RE*WorkflowTools via MCP | Chain Epic one-shots |
| MCP `error` / no tools, RC up | `oneshot_python` / skill `reagent-rc-oneshot` | Claim composites unreachable; Epic fallback; random RC scripts |
| RE call fails | One RE retry from `recovery`, then STOP | Open Epic manual mode |

## Verify (on a machine with Unreal — not cloud CI)

- Editor log: `[REAgentTools] Workflow toolsets registered`
- MCP path: Cursor lists `re_agent_tools.toolsets.*`
- RC one-shot: `oneshot_python({"action":"list_toolsets"})` returns all 15 toolsets between markers
