"""Win32 Unreal Editor dialog / lockup probe (host-side, no game thread)."""

from __future__ import annotations

import ctypes
import json
import os
import socket
import time
from ctypes import wintypes
from pathlib import Path
from typing import Any

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

EnumWindows = user32.EnumWindows
EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
EnumChildWindows = user32.EnumChildWindows
GetWindowTextW = user32.GetWindowTextW
GetWindowTextLengthW = user32.GetWindowTextLengthW
GetClassNameW = user32.GetClassNameW
IsWindowVisible = user32.IsWindowVisible
GetWindowThreadProcessId = user32.GetWindowThreadProcessId
IsWindowEnabled = user32.IsWindowEnabled
SendMessageW = user32.SendMessageW
PostMessageW = user32.PostMessageW
GetWindow = user32.GetWindow
GetWindow.restype = wintypes.HWND

GW_OWNER = 4
BM_CLICK = 0x00F5
WM_CLOSE = 0x0010

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

OpenProcess = kernel32.OpenProcess
OpenProcess.restype = wintypes.HANDLE
QueryFullProcessImageNameW = kernel32.QueryFullProcessImageNameW
CloseHandle = kernel32.CloseHandle


def _window_text(hwnd: int) -> str:
    n = GetWindowTextLengthW(hwnd)
    if n <= 0:
        buf = ctypes.create_unicode_buffer(512)
        GetWindowTextW(hwnd, buf, 512)
        return buf.value.strip()
    buf = ctypes.create_unicode_buffer(n + 1)
    GetWindowTextW(hwnd, buf, n + 1)
    return buf.value.strip()


def _class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    GetClassNameW(hwnd, buf, 256)
    return buf.value


def _process_image(pid: int) -> str:
    handle = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(32768)
        buf = ctypes.create_unicode_buffer(size.value)
        if QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        CloseHandle(handle)


def _pid_basename(pid: int) -> str:
    path = _process_image(pid)
    if not path:
        return ""
    return Path(path).stem


def _port_listening(host: str, port: int, timeout: float) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        return sock.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        sock.close()


def _http_probe(host: str, port: int, path: str, timeout: float) -> dict[str, Any]:
    """Return {listening, responded, elapsed_ms, error?} without depending on full MCP protocol."""
    t0 = time.perf_counter()
    listening = _port_listening(host, port, min(timeout, 1.0))
    if not listening:
        return {
            "listening": False,
            "responded": False,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
            "error": "not_listening",
        }
    try:
        # Minimal HTTP GET — Unreal may return 404/405; any HTTP response = thread progressing.
        req = (
            f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n"
        ).encode("ascii")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.sendall(req)
        data = sock.recv(64)
        sock.close()
        ok = bool(data)
        return {
            "listening": True,
            "responded": ok,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
            "preview": data[:40].decode("latin-1", "replace") if data else "",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "listening": True,
            "responded": False,
            "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
            "error": f"{type(exc).__name__}: {exc}",
        }


def find_unreal_pids(process_names: list[str]) -> list[dict[str, Any]]:
    names = {n.lower() for n in process_names}
    found: dict[int, dict[str, Any]] = {}

    @EnumWindowsProc
    def _cb(hwnd, _lparam):
        if not IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return True
        base = _pid_basename(pid.value)
        if base.lower() in names:
            found[pid.value] = {
                "pid": int(pid.value),
                "process": base,
                "main_title": _window_text(hwnd) or found.get(pid.value, {}).get("main_title", ""),
            }
        return True

    EnumWindows(_cb, 0)
    return list(found.values())


def _child_buttons(hwnd: int) -> list[dict[str, Any]]:
    buttons: list[dict[str, Any]] = []
    action_labels = {
        "ok",
        "cancel",
        "yes",
        "no",
        "close",
        "retry",
        "ignore",
        "continue",
        "save",
        "don't save",
        "dont save",
        "apply",
    }

    @EnumWindowsProc
    def _cb(child, _lparam):
        cls = _class_name(child).lower()
        text = _window_text(child)
        if not text:
            return True
        is_button = cls in ("button", "sbutton") or text.lower() in action_labels
        if is_button:
            buttons.append(
                {
                    "hwnd": int(child),
                    "text": text,
                    "enabled": bool(IsWindowEnabled(child)),
                    "class": cls,
                }
            )
        return True

    EnumChildWindows(hwnd, _cb, 0)
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for b in buttons:
        key = b["text"].lower()
        if key in seen:
            continue
        seen.add(key)
        uniq.append(b)
    return uniq


def find_dialogs(process_names: list[str]) -> list[dict[str, Any]]:
    names = {n.lower() for n in process_names}
    dialogs: list[dict[str, Any]] = []

    @EnumWindowsProc
    def _cb(hwnd, _lparam):
        if not IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return True
        base = _pid_basename(pid.value)
        if base.lower() not in names:
            return True

        cls = _class_name(hwnd)
        title = _window_text(hwnd)
        owner = GetWindow(hwnd, GW_OWNER)
        is_std_dialog = cls == "#32770"
        # Owned visible windows with buttons are often UE message boxes / Slate dialogs
        buttons = _child_buttons(hwnd)
        looks_modal = is_std_dialog or (bool(owner) and bool(buttons) and title)
        if not looks_modal and buttons and title and title.lower() not in {
            "unreal editor",
            "cursor",
        }:
            # Heuristic: small titled window with OK/Cancel-like buttons
            labels = {b["text"].lower() for b in buttons}
            if labels & {"ok", "cancel", "yes", "no", "close", "retry", "don't save", "dont save"}:
                looks_modal = True

        if looks_modal and (title or buttons):
            dialogs.append(
                {
                    "hwnd": int(hwnd),
                    "pid": int(pid.value),
                    "process": base,
                    "class": cls,
                    "title": title,
                    "owner_hwnd": int(owner) if owner else 0,
                    "buttons": [{"text": b["text"], "enabled": b["enabled"]} for b in buttons],
                    "_button_hwnds": {b["text"].lower(): b["hwnd"] for b in buttons},
                }
            )
        return True

    EnumWindows(_cb, 0)
    return dialogs


def click_button(dialog: dict[str, Any], choice: str) -> dict[str, Any]:
    """choice: accept|cancel|yes|no|close or exact button label."""
    mapping = {
        "accept": ["ok", "yes", "continue", "retry", "save", "apply"],
        "ok": ["ok"],
        "yes": ["yes"],
        "cancel": ["cancel", "no", "close"],
        "no": ["no"],
        "close": ["close", "cancel"],
    }
    wanted = (choice or "").strip().lower()
    candidates = mapping.get(wanted, [wanted])
    hwnds: dict[str, int] = dialog.get("_button_hwnds") or {}
    # Refresh buttons if stripped report lacked hwnd map
    if not hwnds:
        fresh = _child_buttons(int(dialog["hwnd"]))
        hwnds = {b["text"].lower(): b["hwnd"] for b in fresh}

    target = None
    matched = None
    for label in candidates:
        if label in hwnds:
            target = hwnds[label]
            matched = label
            break
    # exact / substring
    if target is None:
        for label, hwnd in hwnds.items():
            if wanted == label or wanted in label:
                target = hwnd
                matched = label
                break
    if target is None:
        return {
            "ok": False,
            "error": f"No button matching {choice!r}",
            "available": list(hwnds.keys()),
        }
    # Prefer BM_CLICK on button hwnd
    SendMessageW(target, BM_CLICK, 0, 0)
    return {"ok": True, "clicked": matched, "hwnd": int(target)}


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or Path(__file__).with_name("config.json")
    defaults = {
        "mode": os.environ.get("UNREAL_WATCH_MODE", "report"),
        "auto_allowlist": ["OK", "Close"],
        "never_auto": [
            "Don't Save",
            "Dont Save",
            "Delete",
            "Overwrite",
            "Checkout",
            "Discard",
            "Abort",
            "No",
        ],
        "unreal_process_names": ["UnrealEditor"],
        "mcp_probe_host": "127.0.0.1",
        "mcp_probe_port": 8000,
        "rc_probe_host": "127.0.0.1",
        "rc_probe_port": 30010,
        "probe_timeout_s": 2.0,
    }
    if cfg_path.is_file():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                defaults.update(data)
        except Exception:
            pass
    env_mode = os.environ.get("UNREAL_WATCH_MODE")
    if env_mode:
        defaults["mode"] = env_mode
    return defaults


def save_config(cfg: dict[str, Any], path: Path | None = None) -> Path:
    cfg_path = path or Path(__file__).with_name("config.json")
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    return cfg_path


def project_alert_path() -> Path | None:
    root = os.environ.get("UNREAL_WATCH_PROJECT", "").strip()
    if not root:
        return None
    p = Path(root) / "Saved" / "REAgentTools" / "modal_alert.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def check_unreal(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    timeout = float(cfg.get("probe_timeout_s", 2.0))
    names = list(cfg.get("unreal_process_names") or ["UnrealEditor"])

    processes = find_unreal_pids(names)
    dialogs_raw = find_dialogs(names)
    # Strip private hwnd maps for JSON report copies
    dialogs_public = []
    for d in dialogs_raw:
        dialogs_public.append(
            {
                "hwnd": d["hwnd"],
                "pid": d["pid"],
                "process": d["process"],
                "class": d["class"],
                "title": d["title"],
                "buttons": d["buttons"],
            }
        )

    mcp = _http_probe(
        str(cfg.get("mcp_probe_host", "127.0.0.1")),
        int(cfg.get("mcp_probe_port", 8000)),
        "/mcp",
        timeout,
    )
    rc = _http_probe(
        str(cfg.get("rc_probe_host", "127.0.0.1")),
        int(cfg.get("rc_probe_port", 30010)),
        "/remote/info",
        timeout,
    )

    modal_present = bool(dialogs_public)
    ports_up_no_reply = (mcp.get("listening") and not mcp.get("responded")) or (
        rc.get("listening") and not rc.get("responded")
    )
    likely_blocked = modal_present or ports_up_no_reply

    advice = []
    if not processes:
        advice.append("UnrealEditor process not found — start the editor.")
    elif modal_present:
        advice.append(
            "Modal/dialog detected. Do NOT spam Unreal MCP. "
            "Call dismiss_dialog (accept/cancel/yes/no) or ask the user."
        )
    elif ports_up_no_reply:
        advice.append(
            "MCP/RC ports listen but probes timed out — editor thread likely busy or nested UI. "
            "Wait; do not open new Unreal MCP sessions."
        )
    elif mcp.get("listening") or rc.get("listening"):
        advice.append("Editor appears responsive on probe — Unreal MCP should work.")
    else:
        advice.append("Editor running but MCP/RC not listening — start MCP / WebControl.StartServer.")

    auto_action = None
    mode = str(cfg.get("mode", "report")).lower()
    if mode == "auto_allowlist" and dialogs_raw:
        allow = {str(x).lower() for x in (cfg.get("auto_allowlist") or [])}
        never = {str(x).lower() for x in (cfg.get("never_auto") or [])}
        dlg = dialogs_raw[0]
        for b in dlg.get("buttons") or []:
            label = str(b.get("text", "")).lower()
            if label in never:
                auto_action = {"skipped": True, "reason": f"never_auto matched {b.get('text')}"}
                break
            if label in allow and b.get("enabled", True):
                auto_action = click_button(dlg, b["text"])
                auto_action["mode"] = "auto_allowlist"
                auto_action["dialog_title"] = dlg.get("title")
                break
        if auto_action is None:
            auto_action = {
                "skipped": True,
                "reason": "no allowlisted button on dialog",
                "buttons": [b.get("text") for b in dlg.get("buttons") or []],
            }

    report = {
        "ok": True,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": mode,
        "unreal_running": bool(processes),
        "processes": processes,
        "modal": {
            "present": modal_present,
            "count": len(dialogs_public),
            "dialogs": dialogs_public,
        },
        "mcp_probe": mcp,
        "rc_probe": rc,
        "likely_blocked": likely_blocked,
        "advice": advice,
        "agent_instruction": (
            "STOP Unreal MCP retries. Use unreal-watch dismiss_dialog or ask the user "
            "if modal.present. If only likely_blocked, wait then re-check once."
            if likely_blocked
            else "Editor watch clear — Unreal MCP may be used."
        ),
        "auto_action": auto_action,
        # private for dismiss_dialog in same process — not written to alert file
        "_dialogs_raw": dialogs_raw,
    }

    alert = project_alert_path()
    if alert:
        public = {k: v for k, v in report.items() if not k.startswith("_")}
        alert.write_text(json.dumps(public, indent=2) + "\n", encoding="utf-8")
        report["alert_path"] = str(alert)

    return report


def dismiss_dialog(
    choice: str = "accept",
    hwnd: int | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = cfg or load_config()
    names = list(cfg.get("unreal_process_names") or ["UnrealEditor"])
    dialogs = find_dialogs(names)
    if not dialogs:
        return {"ok": False, "error": "No Unreal dialog detected"}
    target = None
    if hwnd:
        for d in dialogs:
            if int(d["hwnd"]) == int(hwnd):
                target = d
                break
        if target is None:
            return {"ok": False, "error": f"Dialog hwnd {hwnd} not found", "dialogs": [
                {"hwnd": d["hwnd"], "title": d["title"], "buttons": d["buttons"]} for d in dialogs
            ]}
    else:
        target = dialogs[0]
    result = click_button(target, choice)
    result["dialog_title"] = target.get("title")
    result["dialog_hwnd"] = target.get("hwnd")
    result["available_buttons"] = [b.get("text") for b in target.get("buttons") or []]
    return result
