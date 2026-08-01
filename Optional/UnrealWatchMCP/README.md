# UnrealWatchMCP (optional)

Tiny **host-side** MCP server that detects Unreal Editor process state, freezes, dialogs, Crash Report Client, Restore Packages, and Interchange **Import Content** from outside the game thread.

Use when Unreal MCP / RC **time out but ports are still open** — almost always a modal dialog or a stuck editor UI loop — or when agents need a **fail-fast `editor_offline` / `crash_reporter`** instead of hammering a dead `:8000`.

This folder is **optional**. It is not registered by Unreal’s ToolsetRegistry. Enable it in Cursor MCP config when you want agents to diagnose freezes.

**Pull + heartbeat:** Tools are pull-based. Set `UNREAL_WATCH_HEARTBEAT_S` (seconds) and `UNREAL_WATCH_PROJECT` to also write `Saved/REAgentTools/modal_alert.json` on a background timer while the MCP server is running. That is still not a Cursor push notification — agents must call `get_editor_status` / `check_unreal` / `wait_for_editor` or read the alert file. `wait_for_editor` returns early on modal/crash and best-effort emits MCP progress notifications.

## Why not an in-editor RE tool?

Modal dialogs block the editor game thread. In-editor MCP tools need that thread, so they cannot run while frozen. This watcher uses Win32 process/window enumeration and short HTTP probes instead.

## Tools

| Tool | Purpose |
|------|---------|
| `check_unreal` | Full report: `status`, processes, CrashReportClient, MCP/proxy/RC probes, Slate/Win32 dialogs, `abort_unreal_mcp`, `recover_tool`, `agent_instruction`. Ensures `:8001` if missing (never kills/rebinds). |
| `get_editor_status` | Compact status poll. **Call before Unreal MCP batches.** |
| `wait_for_editor` | Block until ready, **blocker**, or timeout (after asking the user to launch). |
| `dismiss_unreal_blocker` | Safe dismiss: Escape/Cancel/Don't Restore/close CRC; **Import Content → Import**. Never Delete without `allow_destructive=true`. |
| `dismiss_dialog` | Low-level button click (prefer `dismiss_unreal_blocker`). |
| `get_watch_config` | Read mode + restore policy + allowlist |
| `set_watch_config` | Set `mode`, allowlist, `restore_packages_policy` |

### `status` values (STOP / RECOVER)

| status | Meaning | Agent action |
|--------|---------|--------------|
| `ok` | Editor up, probes answering, no blocking modal | Unreal MCP via `:8001` may be used |
| `editor_offline` | No UnrealEditor process | **STOP** — ask user to launch; `wait_for_editor` |
| `crash_reporter` | CrashReportClient and/or crash UI | **STOP** — `dismiss_unreal_blocker` then relaunch/wait |
| `restore_packages` | Restore Packages dialog | **STOP** — `dismiss_unreal_blocker` (default Don't Restore) |
| `import_dialog` | Interchange **Import Content** / FBX import | **STOP** — `dismiss_unreal_blocker` (default **Import**; `policy=cancel` to abort) |
| `modal_blocked` | Dialog/context menu + probes failing | **STOP** — `dismiss_unreal_blocker` or ask user |
| `ports_wedged` | Ports listen, no reply | Wait / re-check; never kill `:8001` |
| `proxy_unhealthy` | Editor up, proxy identity missing | Ensure proxy; do not use raw `:8000` |

**Important:** Unreal “Message dialog” / compile-error popups are usually owned `UnrealWindow` Slate windows — **not** classic `#32770` Win32 dialogs. Crash reporter is a **separate process**. Interchange **Import Content** is also an owned Slate window and **always** aborts Unreal MCP until dismissed.

## Agent contract

1. Before Unreal MCP batches (or after timeout / WinError 10061): `get_editor_status` (or `check_unreal`).
2. If `status=editor_offline` / `abort_unreal_mcp` → **STOP**. Ask user; optionally `wait_for_editor`.
3. If `modal_blocked` / `import_dialog` / `crash_reporter` / `restore_packages` → `dismiss_unreal_blocker` (safe defaults; Import Content clicks **Import**).
4. If `modal.present` but `editor_responsive` and status is still `ok` → floating tab, not a block. One batched MCP call.
5. If `status=ok` → resubmit **one** batched MCP call via `:8001`.
6. Never kill/rebind `:8001` on WinError 10048.
7. Never pass `allow_destructive=true` unless the user explicitly confirmed Delete/Overwrite.

Optional alert file (when project path set):

`Saved/REAgentTools/modal_alert.json`

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

Destructive prompts (`Don't Save`, `Delete`, checkout, overwrite), Restore Packages, Crash Reporter, and Import Content are **never** auto-clicked in allowlist mode (use `dismiss_unreal_blocker` for Import).

`restore_packages_policy` (config / `UNREAL_WATCH_RESTORE_POLICY`): default `dont_restore` (Cancel / Don't Restore).
Import Content: `dismiss_unreal_blocker` default clicks **Import**; pass `policy=cancel` only to abort.

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
        "UNREAL_WATCH_RESTORE_POLICY": "dont_restore",
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

## Tests

```bat
python test_offline_semantics.py
```
