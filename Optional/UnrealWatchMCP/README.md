# UnrealWatchMCP (optional)

Tiny **host-side** MCP server that watches the Unreal Editor process from outside the game thread.

Use when Unreal MCP / RC **time out but ports are still open** — almost always a modal dialog or a stuck editor UI loop.

This folder is **optional**. It is not registered by Unreal’s ToolsetRegistry. Enable it in Cursor MCP config when you want agents to diagnose freezes.

## Why not an in-editor RE tool?

Modal dialogs block the editor game thread. In-editor MCP tools need that thread, so they cannot run while frozen. This watcher uses Win32 window enumeration and short HTTP probes instead.

## Tools

| Tool | Purpose |
|------|---------|
| `check_unreal` | Report: process up? MCP/RC listening? probe timed out? modal dialog title/buttons? |
| `dismiss_dialog` | Click a button on the detected dialog (`accept` / `cancel` / `yes` / `no` / exact label) |
| `get_watch_config` | Read mode + allowlist |
| `set_watch_config` | Set `mode` to `report` or `auto_allowlist` (and optional allowlist) |

## Modes

| Mode | Behavior |
|------|----------|
| `report` (default) | Detect only. Agent should **stop** and tell the user / ask before dismissing. |
| `auto_allowlist` | On `check_unreal`, if dialog button text is in allowlist (default `OK`, `Close`), click it and report what happened. |

Destructive prompts (`Don't Save`, `Delete`, checkout, overwrite) are **never** auto-clicked.

## Cursor MCP wiring (example)

```json
{
  "mcpServers": {
    "unreal-watch": {
      "command": "C:\\Program Files\\Python39\\python.exe",
      "args": [
        "C:\\Users\\WhiteWidow\\Documents\\Unreal Projects\\visualtest\\Plugins\\REAgentTools\\Optional\\UnrealWatchMCP\\server.py"
      ],
      "env": {
        "UNREAL_WATCH_PROJECT": "C:\\Users\\WhiteWidow\\Documents\\Unreal Projects\\visualtest",
        "UNREAL_WATCH_MODE": "report"
      }
    }
  }
}
```

Stdlib only (ctypes + urllib). No `pip install`.

## Agent habit

When Unreal MCP errors with timeouts / “dead” but you suspect the editor is open:

1. Call `unreal-watch.check_unreal` **once** (do not spam Unreal MCP retries).
2. If `modal.present` → read title/buttons → `dismiss_dialog` or ask the user.
3. If `likely_blocked` and no modal → editor busy or nested UI; wait / ask user; do not hammer tools.

Optional alert file (when project path set):

`Saved/REAgentTools/modal_alert.json`

## Push vs pull

MCP tools are **pull**: the agent must call `check_unreal`. Cursor will not auto-inject mid-turn. Treat this as the freeze detector the agent calls instead of burning more Unreal MCP tokens.
