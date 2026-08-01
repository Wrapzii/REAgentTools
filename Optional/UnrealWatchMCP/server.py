#!/usr/bin/env python3
"""Optional stdio MCP: Unreal dialog / lockup watcher (host-side).

Tools: check_unreal, get_editor_status, wait_for_editor, dismiss_dialog,
       dismiss_unreal_blocker, get_watch_config, set_watch_config

Prefer the official ``mcp`` Python SDK (FastMCP) for Cursor discovery.
CLI ``--check`` works with stdlib + ctypes only (no mcp package required).

check_unreal also ensures the :8001 anti-thrash HTTP proxy (identity-aware;
never kills/rebinds).
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path
from typing import Any, Optional

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import watch  # noqa: E402

SERVER_NAME = "unreal-watch"
SERVER_VERSION = "0.6.0"

_STATUS_ENUM = (
    "ok|editor_offline|modal_blocked|crash_reporter|restore_packages|"
    "import_dialog|ports_wedged|proxy_unhealthy"
)


def _public(report: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in report.items() if not k.startswith("_")}


def _cli_check() -> int:
    report = watch.check_unreal()
    print(json.dumps(_public(report), indent=2))
    return 0


def _heartbeat_interval() -> float:
    raw = os.environ.get("UNREAL_WATCH_HEARTBEAT_S", "").strip()
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        return 0.0


def _maybe_start_heartbeat() -> None:
    interval = _heartbeat_interval()
    if interval <= 0:
        return
    if not os.environ.get("UNREAL_WATCH_PROJECT", "").strip():
        return
    watch.start_heartbeat_thread(interval)


# ---------------------------------------------------------------------------
# Official SDK path (Cursor)
# ---------------------------------------------------------------------------


def _run_fastmcp() -> int:
    from mcp.server.fastmcp import Context, FastMCP

    mcp = FastMCP(SERVER_NAME)

    @mcp.tool(
        name="check_unreal",
        description=(
            "Host-side Unreal freeze/dialog detector. Does NOT use Unreal MCP tools. "
            "Call once when Unreal MCP times out, WinError 10061, or the editor seems stuck. "
            f"Returns status ({_STATUS_ENUM}), abort_unreal_mcp, CrashReportClient, "
            "Restore Packages / Slate modals, recover_tool, and agent_instruction "
            "(STOP/RECOVER). If status=editor_offline: STOP — do not retry Unreal MCP. "
            "Ensures :8001 proxy if missing — never kills/rebinds."
        ),
    )
    def check_unreal() -> dict[str, Any]:
        return _public(watch.check_unreal())

    @mcp.tool(
        name="get_editor_status",
        description=(
            "Compact editor status JSON (status, unreal_running, abort_unreal_mcp, "
            "modal, crash_reporter_processes, recover_tool, agent_instruction). "
            "Agent contract: call this BEFORE Unreal MCP batches; on modal_blocked / "
            "import_dialog / crash_reporter / restore_packages call dismiss_unreal_blocker."
        ),
    )
    def get_editor_status() -> dict[str, Any]:
        return watch.get_editor_status()

    @mcp.tool(
        name="wait_for_editor",
        description=(
            "Block until Unreal Editor is ready, a blocker appears, or timeout. "
            "Returns early on modal_blocked / import_dialog / crash_reporter / "
            "restore_packages / ports_wedged (wait_result=blocker:…) so agents can dismiss. "
            "editor_offline keeps polling. Default timeout 120s. Emits MCP progress "
            "notifications when the client supports them."
        ),
    )
    def wait_for_editor(
        timeout_s: float = 120.0,
        poll_s: float = 2.0,
        return_on_blocker: bool = True,
        ctx: Context | None = None,
    ) -> dict[str, Any]:
        progress_cb = None
        if ctx is not None:

            def progress_cb(ticks: int, status: str, _last: dict[str, Any]) -> None:
                # Best-effort MCP progress notify; Cursor may ignore (still pull-based).
                try:
                    import asyncio

                    loop = asyncio.get_running_loop()
                    loop.create_task(
                        ctx.report_progress(
                            progress=float(ticks),
                            total=None,
                            message=f"unreal-watch status={status}",
                        )
                    )
                except Exception:
                    pass

        return watch.wait_for_editor(
            timeout_s=timeout_s,
            poll_s=poll_s,
            return_on_blocker=return_on_blocker,
            progress_cb=progress_cb,
        )

    @mcp.tool(
        name="dismiss_unreal_blocker",
        description=(
            "Safely dismiss Crash Reporter / Restore Packages / Import Content / "
            "blocking Slate-Win32 dialogs. policy=safe_cancel (default: Escape/Cancel/"
            "Don't Restore/close CRC; Import Content clicks Import), "
            "import, cancel (abort import), accept, restore_packages_skip, "
            "or crash_reporter_close. Never clicks Delete / destructive buttons unless "
            "allow_destructive=true. Returns status_after + followup get_editor_status."
        ),
    )
    def dismiss_unreal_blocker(
        policy: str = "safe_cancel",
        allow_destructive: bool = False,
        hwnd: Optional[int] = None,
    ) -> dict[str, Any]:
        return watch.dismiss_unreal_blocker(
            policy=policy,
            allow_destructive=allow_destructive,
            hwnd=hwnd,
        )

    @mcp.tool(
        name="dismiss_dialog",
        description=(
            "Low-level: click a button on the detected Unreal dialog. "
            "Prefer dismiss_unreal_blocker for safe defaults. "
            "choice: accept|import|cancel|yes|no|close|dont_restore or exact button label. "
            "Optional hwnd from check_unreal.modal.dialogs[].hwnd."
        ),
    )
    def dismiss_dialog(choice: str = "accept", hwnd: Optional[int] = None) -> dict[str, Any]:
        return watch.dismiss_dialog(choice=choice, hwnd=hwnd)

    @mcp.tool(
        name="get_watch_config",
        description="Return UnrealWatch mode (report|auto_allowlist), restore policy, allowlists.",
    )
    def get_watch_config() -> dict[str, Any]:
        return watch.load_config()

    @mcp.tool(
        name="set_watch_config",
        description=(
            "Update watch mode. mode=report (agent stops / asks) or auto_allowlist "
            "(auto-click only safe OK/Close-style buttons). "
            "Optional restore_packages_policy=dont_restore|cancel|ask."
        ),
    )
    def set_watch_config(
        mode: Optional[str] = None,
        auto_allowlist: Optional[list[str]] = None,
        restore_packages_policy: Optional[str] = None,
    ) -> dict[str, Any]:
        cfg = watch.load_config()
        if mode is not None:
            mode_l = str(mode).lower()
            if mode_l not in ("report", "auto_allowlist"):
                raise ValueError("mode must be report or auto_allowlist")
            cfg["mode"] = mode_l
        if isinstance(auto_allowlist, list):
            cfg["auto_allowlist"] = [str(x) for x in auto_allowlist]
        if restore_packages_policy is not None:
            pol = str(restore_packages_policy).lower()
            if pol not in ("dont_restore", "don't_restore", "cancel", "ask", "skip"):
                raise ValueError(
                    "restore_packages_policy must be dont_restore|cancel|ask|skip"
                )
            cfg["restore_packages_policy"] = pol
        path = watch.save_config(cfg)
        return {"ok": True, "saved": str(path), "config": cfg}

    _maybe_start_heartbeat()
    mcp.run(transport="stdio")
    return 0


# ---------------------------------------------------------------------------
# Hardened hand-rolled stdio (fallback if mcp package missing)
# ---------------------------------------------------------------------------


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
            try:
                return json.loads(header.decode("utf-8"))
            except json.JSONDecodeError:
                return None

    headers: dict[str, str] = {}
    for line in header.decode("utf-8", "replace").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    try:
        length = int(headers.get("content-length", "0") or 0)
    except ValueError:
        return None
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except json.JSONDecodeError:
        return None


def _write_message(msg: dict[str, Any]) -> None:
    data = json.dumps(msg, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii") + data)
    sys.stdout.buffer.flush()


def _tool_result(obj: Any) -> dict[str, Any]:
    text = json.dumps(obj, indent=2, ensure_ascii=False)
    return {"content": [{"type": "text", "text": text}]}


TOOLS = [
    {
        "name": "check_unreal",
        "description": (
            "Host-side Unreal freeze/dialog detector. Call on MCP timeout / 10061. "
            f"Returns status ({_STATUS_ENUM}) and abort_unreal_mcp. "
            "Never treat offline as clear."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "get_editor_status",
        "description": "Compact editor status JSON for quick polls.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "wait_for_editor",
        "description": (
            "Block until editor ready, blocker, or timeout. "
            "Returns early on modal/crash when return_on_blocker=true."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "timeout_s": {"type": "number", "default": 120.0},
                "poll_s": {"type": "number", "default": 2.0},
                "return_on_blocker": {"type": "boolean", "default": True},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "dismiss_unreal_blocker",
        "description": (
            "Safe dismiss for crash reporter / restore packages / Import Content / modals. "
            "policy=safe_cancel|import|cancel|accept|restore_packages_skip|crash_reporter_close. "
            "Import Content defaults to Import; policy=cancel aborts. "
            "Never Delete without allow_destructive=true."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "policy": {"type": "string", "default": "safe_cancel"},
                "allow_destructive": {"type": "boolean", "default": False},
                "hwnd": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "dismiss_dialog",
        "description": "Low-level click on detected Unreal dialog (prefer dismiss_unreal_blocker).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "choice": {"type": "string", "default": "accept"},
                "hwnd": {"type": "integer"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_watch_config",
        "description": "Return UnrealWatch mode and allowlists.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "set_watch_config",
        "description": "Update watch mode / restore_packages_policy.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["report", "auto_allowlist"]},
                "auto_allowlist": {"type": "array", "items": {"type": "string"}},
                "restore_packages_policy": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
]


def _handle_tool(name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
    args = arguments or {}
    if name == "check_unreal":
        return _tool_result(_public(watch.check_unreal()))
    if name == "get_editor_status":
        return _tool_result(watch.get_editor_status())
    if name == "wait_for_editor":
        return _tool_result(
            watch.wait_for_editor(
                timeout_s=float(args.get("timeout_s", 120.0)),
                poll_s=float(args.get("poll_s", 2.0)),
                return_on_blocker=bool(args.get("return_on_blocker", True)),
            )
        )
    if name == "dismiss_unreal_blocker":
        hwnd = args.get("hwnd")
        hwnd_i = int(hwnd) if hwnd is not None else None
        return _tool_result(
            watch.dismiss_unreal_blocker(
                policy=str(args.get("policy") or "safe_cancel"),
                allow_destructive=bool(args.get("allow_destructive", False)),
                hwnd=hwnd_i,
            )
        )
    if name == "dismiss_dialog":
        choice = str(args.get("choice") or "accept")
        hwnd = args.get("hwnd")
        hwnd_i = int(hwnd) if hwnd is not None else None
        return _tool_result(watch.dismiss_dialog(choice=choice, hwnd=hwnd_i))
    if name == "get_watch_config":
        return _tool_result(watch.load_config())
    if name == "set_watch_config":
        cfg = watch.load_config()
        if "mode" in args and args["mode"] is not None:
            mode = str(args["mode"]).lower()
            if mode not in ("report", "auto_allowlist"):
                raise ValueError("mode must be report or auto_allowlist")
            cfg["mode"] = mode
        if isinstance(args.get("auto_allowlist"), list):
            cfg["auto_allowlist"] = [str(x) for x in args["auto_allowlist"]]
        if args.get("restore_packages_policy") is not None:
            cfg["restore_packages_policy"] = str(args["restore_packages_policy"]).lower()
        path = watch.save_config(cfg)
        return _tool_result({"ok": True, "saved": str(path), "config": cfg})
    raise ValueError(f"Unknown tool: {name}")


def _handle(msg: dict[str, Any]) -> dict[str, Any] | None:
    method = msg.get("method")
    msg_id = msg.get("id")
    if msg_id is None and method:
        return None

    try:
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        if method == "tools/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
        if method == "prompts/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"prompts": []}}
        if method == "resources/list":
            return {"jsonrpc": "2.0", "id": msg_id, "result": {"resources": []}}
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


def _run_stdio_legacy() -> int:
    _maybe_start_heartbeat()
    while True:
        try:
            msg = _read_message()
        except Exception:
            continue
        if msg is None:
            break
        if not isinstance(msg, dict):
            continue
        resp = _handle(msg)
        if resp is not None:
            _write_message(resp)
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in ("--check", "check"):
        return _cli_check()
    if len(sys.argv) > 1 and sys.argv[1] in ("--heartbeat", "heartbeat"):
        interval = _heartbeat_interval() or 5.0
        watch.start_heartbeat_thread(interval)
        try:
            while True:
                import time

                time.sleep(3600)
        except KeyboardInterrupt:
            return 0

    try:
        import mcp  # noqa: F401
    except ImportError:
        return _run_stdio_legacy()
    return _run_fastmcp()


if __name__ == "__main__":
    raise SystemExit(main())
