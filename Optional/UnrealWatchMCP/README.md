# UnrealWatchMCP (optional)

Tiny **host-side** MCP server that detects Unreal Editor process state, freezes, and dialogs from outside the game thread.

Use when Unreal MCP / RC **time out but ports are still open** — almost always a modal dialog or a stuck editor UI loop — or when agents need a **fail-fast `editor_offline`** instead of hammering a dead `:8000`.

This folder is **optional**. It is not registered by Unreal’s ToolsetRegistry. Enable it in Cursor MCP config when you want agents to diagnose freezes.

**Pull + heartbeat:** Tools are pull-based. Set `UNREAL_WATCH_HEARTBEAT_S` (seconds) and `UNREAL_WATCH_PROJECT` to also write `Saved/REAgentTools/modal_alert.json` on a background timer while the MCP server is running. That is still not a Cursor push notification — agents must call `check_unreal` / `get_editor_status` or read the alert file.

## Why not an in-editor RE tool?

Modal dialogs block the editor game thread. In-editor MCP tools need that thread, so they cannot run while frozen. This watcher uses Win32 process/window enumeration and short HTTP probes instead.

## Tools

| Tool | Purpose |
|------|---------|
| `check_unreal` | Full report: `status`, process up?, native MCP `:8000`?, proxy `:8001`?, RC?, Slate/Win32 dialogs?, `abort_unreal_mcp`, `agent_instruction`. Ensures `:8001` if missing (never kills/rebinds). |
| `get_editor_status` | Compact status poll (same `status` / instruction, fewer probe fields). |
| `wait_for_editor` | Block until editor ready or timeout (after asking the user to launch). |
| `dismiss_dialog` | Dismiss detected dialog (`accept`/`cancel`/`yes`/`no`, or exact label). |
| `get_watch_config` | Read mode + allowlist |
| `set_watch_config` | Set `mode` to `report` or `auto_allowlist` (and optional allowlist) |

### `status` values

| status | Meaning | Agent action |
|--------|---------|--------------|
| `ok` | Editor up, probes answering, no blocking modal | Unreal MCP via `:8001` may be used |
| `editor_offline` | No UnrealEditor process | **STOP** — do not retry Unreal MCP |
| `modal_blocked` | Dialog + probes failing | `dismiss_dialog` or ask user |
| `ports_wedged` | Ports listen, no reply | Wait / ask user; never kill `:8001` |
| `proxy_unhealthy` | Editor up, proxy identity missing | Ensure proxy; do not use raw `:8000` |

**Important:** Unreal “Message dialog” / compile-error popups are usually owned `UnrealWindow` Slate windows — **not** classic `#32770` Win32 dialogs.

## Proxy pairing

Cursor should use the anti-thrash sidecar (`Optional/UnrealMcpProxy`) on **`:8001`**, not raw Unreal `:8000`:

```json
{
  "mcpServers": {
    "unreal-mcp": {
      "type": "http",
      "url": "http://127.0.0.1:8001/mcp"
    }
  }
}
```

- `GET /health` on `:8001` → service identity
- `GET /mcp` → **405** (protocol-correct; not a failure)
- WinError **10048** / address in use → verify `/health` and **reuse**. Never kill/rebind `:8001`.

## Modes

| Mode | Behavior |
|------|----------|
| `report` (default) | Detect only. Agent should **stop** and tell the user / ask before dismissing. |
| `auto_allowlist` | On `check_unreal`, if dialog button text is in allowlist (default `OK`, `Close`), click it and report what happened. |

Destructive prompts (`Don't Save`, `Delete`, checkout, overwrite) are **never** auto-clicked.

## Cursor MCP wiring (recommended)

Uses `uv` + official `mcp` SDK (same pattern as blender-mcp) so Cursor stdio discovery is reliable:

```json
{
  "mcpServers": {
    "unreal-watch": {
      "command": "C:\\Users\\<you>\\.local\\bin\\uv.exe",
      "args": [
        "run",
        "--python", "3.11",
        "--with", "mcp>=1.9,<2",
        "python",
        "C:\\path\\to\\REAgentTools\\Optional\\UnrealWatchMCP\\server.py"
      ],
      "env": {
        "PYTHONUNBUFFERED": "1",
        "PYTHONIOENCODING": "utf-8",
        "UNREAL_WATCH_PROJECT": "C:\\path\\to\\UnrealProject",
        "UNREAL_WATCH_MODE": "report",
        "UNREAL_WATCH_HEARTBEAT_S": "5",
        "UNREAL_MCP_PROXY_SCRIPT": "C:\\path\\to\\REAgentTools\\Optional\\UnrealMcpProxy\\unreal_mcp_proxy.py"
      }
    }
  }
}
```

CLI smoke (no MCP package needed):

```bat
python server.py --check
```

## Agent habit

When Unreal MCP errors with timeouts / WinError 10061 / “dead” upstream:

1. Call `unreal-watch.check_unreal` **once** (or `get_editor_status`).
2. If `status=editor_offline` / `abort_unreal_mcp` → **STOP**. Ask user to launch; optionally `wait_for_editor`.
3. If `modal.blocking` → `dismiss_dialog` or ask the user.
4. If `modal.present` but `editor_responsive` → floating tab, not a block. One batched MCP call.
5. If `proxy_probe.ok` and `status=ok` → resubmit **one** batched MCP call via `:8001`.
6. Never kill/rebind `:8001` on WinError 10048.

Optional alert file (when project path set):

`Saved/REAgentTools/modal_alert.json`
