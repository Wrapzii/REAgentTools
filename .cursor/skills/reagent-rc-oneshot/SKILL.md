---
name: reagent-rc-oneshot
description: >
  Invoke REAgentTools over Unreal Remote Control in ONE Cursor tool call when
  mcp-unreal failed live discovery (serverStatus error/loading, no tools).
  Trigger on Remote Control, Agents Window, mobile cloud, "MCP unavailable",
  _rc_exec.py, port 30010, or when the agent would otherwise skip REAgentTools.
  Prefer this over Epic SceneTools/ActorTools and over 3-hop rc_request files.
---

# REAgentTools RC one-shot

## When

- `mcp-unreal` is `error` / `loading` / has no tools
- Unreal Remote Control (`127.0.0.1:30010`) or editor `py` still works
- You need `get_editor_context`, `execute_editor_batch`, or any RE*WorkflowTools composite

## Do not

- Do not use a 3-step write `rc_request.json` → run → read `rc_response.json` unless one-shot capture fails
- Do not fall back to Epic SceneTools/ActorTools/ObjectTools
- Do not invent ad-hoc `_rc_exec.py` editor scripts for work RE covers
- Do not say REAgentTools are unreachable — use this bridge

## One Cursor tool call

Build a request dict, then execute **one** Python block via your host RC wrapper (`_rc_exec.py` → `:30010`) or Unreal `ExecutePythonCommand`:

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

Parse the RC log/stdout between markers:

```text
REAGENT_RC_RESULT_BEGIN
{...WorkflowResult JSON...}
REAGENT_RC_RESULT_END
```

That JSON is the same compact `WorkflowResult` MCP would return.

### Batch (still one RC call)

```python
from re_agent_tools.rc_bridge import oneshot_python
exec(oneshot_python({
    "action": "call",
    "toolset": "REBatchWorkflowTools",
    "tool": "execute_editor_batch",
    "arguments": {
        "dry_run": False,
        "stop_on_error": True,
        "operations_json": "[{\"id\":\"c1\",\"action\":\"get_editor_context\"}]",
    },
}))
```

### List toolsets

```python
from re_agent_tools.rc_bridge import oneshot_python
exec(oneshot_python({"action": "list_toolsets"}))
```

## Token note

One-shot ≈ **1 Cursor round-trip** (same order of magnitude as one MCP tool call). The old file protocol is ~3 round-trips — only use it if your RC wrapper cannot return log/stdout.

Prefer real MCP when `mcp-unreal` is ready.

## More docs

- `AGENTS.md` — new-chat bootstrap
- `Docs/REMOTE_CONTROL_MCP.md` — Cursor MCP vs RC transport
- `Docs/AGENT_DEFAULTS.md` — RE-first policy
- Code: `Content/Python/re_agent_tools/rc_bridge.py`, `Content/Python/_rc_reagent_exec.py`
