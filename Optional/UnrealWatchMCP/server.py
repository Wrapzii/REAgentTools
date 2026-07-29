#!/usr/bin/env python3
"""Optional stdio MCP: Unreal dialog / lockup watcher (host-side).

Tools: check_unreal, dismiss_dialog, get_watch_config, set_watch_config
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import watch  # noqa: E402

SERVER_NAME = "unreal-watch"
SERVER_VERSION = "0.1.0"


def _read_message() -> dict[str, Any] | None:
    header = b""
    while True:
        ch = sys.stdin.buffer.read(1)
        if not ch:
            return None
        header += ch
        if header.endswith(b"\r\n\r\n") or header.endswith(b"\n\n"):
            break
        if header.startswith(b"{") and header.endswith(b"\n") and b"Content-Length" not in header:
            return json.loads(header.decode("utf-8"))

    headers: dict[str, str] = {}
    for line in header.decode("utf-8", "replace").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    length = int(headers.get("content-length", "0") or 0)
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    if not body:
        return None
    return json.loads(body.decode("utf-8"))


def _write_message(msg: dict[str, Any]) -> None:
    data = json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii") + data)
    sys.stdout.buffer.flush()


def _tool_result(obj: Any) -> dict[str, Any]:
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}], "structuredContent": obj}


TOOLS = [
    {
        "name": "check_unreal",
        "description": (
            "Host-side Unreal freeze/dialog detector. Does NOT use Unreal MCP. "
            "Call once when Unreal MCP times out or the editor seems stuck. "
            "Returns process status, MCP/RC probe (listening vs responded), modal dialogs "
            "(title + buttons), likely_blocked, and advice. "
            "If mode=auto_allowlist, may click OK/Close only."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "dismiss_dialog",
        "description": (
            "Click a button on the detected Unreal dialog. "
            "choice: accept|cancel|yes|no|close or exact button label. "
            "Optional hwnd from check_unreal.modal.dialogs[].hwnd."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "choice": {
                    "type": "string",
                    "description": "accept|cancel|yes|no|close or exact label",
                    "default": "accept",
                },
                "hwnd": {
                    "type": "integer",
                    "description": "Dialog hwnd from check_unreal (optional)",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_watch_config",
        "description": "Return UnrealWatch mode (report|auto_allowlist) and allowlists.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "set_watch_config",
        "description": (
            "Update watch mode. mode=report (agent stops / asks) or auto_allowlist "
            "(auto-click only safe OK/Close-style buttons)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["report", "auto_allowlist"],
                },
                "auto_allowlist": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Button labels safe to auto-click",
                },
            },
            "additionalProperties": False,
        },
    },
]


def _handle_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    args = arguments or {}
    if name == "check_unreal":
        report = watch.check_unreal()
        # Drop private fields from MCP payload
        public = {k: v for k, v in report.items() if not k.startswith("_")}
        return _tool_result(public)
    if name == "dismiss_dialog":
        choice = str(args.get("choice") or "accept")
        hwnd = args.get("hwnd")
        hwnd_i = int(hwnd) if hwnd is not None else None
        return _tool_result(watch.dismiss_dialog(choice=choice, hwnd=hwnd_i))
    if name == "get_watch_config":
        cfg = watch.load_config()
        return _tool_result(cfg)
    if name == "set_watch_config":
        cfg = watch.load_config()
        if "mode" in args and args["mode"] is not None:
            mode = str(args["mode"]).lower()
            if mode not in ("report", "auto_allowlist"):
                raise ValueError("mode must be report or auto_allowlist")
            cfg["mode"] = mode
        if isinstance(args.get("auto_allowlist"), list):
            cfg["auto_allowlist"] = [str(x) for x in args["auto_allowlist"]]
        path = watch.save_config(cfg)
        return _tool_result({"ok": True, "saved": str(path), "config": cfg})
    raise ValueError(f"Unknown tool: {name}")


def _handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    method = msg.get("method")
    msg_id = msg.get("id")
    # notifications (no id) — ignore
    if msg_id is None and method:
        if method == "notifications/initialized":
            return None
        return None

    try:
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
        if method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            result = _handle_tool(str(name), arguments if isinstance(arguments, dict) else {})
            return {"jsonrpc": "2.0", "id": msg_id, "result": result}
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {
                "code": -32000,
                "message": str(exc),
                "data": traceback.format_exc(limit=6),
            },
        }


def main() -> int:
    # CLI smoke: python server.py --check
    if len(sys.argv) > 1 and sys.argv[1] in ("--check", "check"):
        report = watch.check_unreal()
        public = {k: v for k, v in report.items() if not k.startswith("_")}
        print(json.dumps(public, indent=2))
        return 0

    while True:
        msg = _read_message()
        if msg is None:
            break
        if not isinstance(msg, dict):
            continue
        resp = _handle(msg)
        if resp is not None:
            _write_message(resp)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
