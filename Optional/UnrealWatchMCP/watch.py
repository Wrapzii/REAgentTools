"""Win32 Unreal Editor dialog / lockup probe (host-side, no game thread).

Unreal message boxes are usually Slate `UnrealWindow` owned popups — they do NOT
expose classic Win32 `Button` children. Detection must treat owned UnrealWindows
as dialogs; dismiss uses UI Automation when available, else Enter/Escape.
"""

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
GetWindow = user32.GetWindow
GetWindow.restype = wintypes.HWND
GetWindowRect = user32.GetWindowRect
SetForegroundWindow = user32.SetForegroundWindow
ShowWindow = user32.ShowWindow
keybd_event = user32.keybd_event

GW_OWNER = 4
BM_CLICK = 0x00F5
WM_CLOSE = 0x0010
SW_RESTORE = 9
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
KEYEVENTF_KEYUP = 0x0002

# Status values returned to agents (STOP / RECOVER semantics).
STATUS_OK = "ok"
STATUS_EDITOR_OFFLINE = "editor_offline"
STATUS_MODAL_BLOCKED = "modal_blocked"
STATUS_CRASH_REPORTER = "crash_reporter"
STATUS_RESTORE_PACKAGES = "restore_packages"
STATUS_PORTS_WEDGED = "ports_wedged"
STATUS_PROXY_UNHEALTHY = "proxy_unhealthy"

BLOCKER_STATUSES = frozenset(
    {
        STATUS_MODAL_BLOCKED,
        STATUS_CRASH_REPORTER,
        STATUS_RESTORE_PACKAGES,
        STATUS_PORTS_WEDGED,
    }
)

_DESTRUCTIVE_LABELS = frozenset(
    {
        "delete",
        "don't save",
        "dont save",
        "overwrite",
        "checkout",
        "discard",
        "abort",
        "force delete",
        "yes to all",
        "delete all",
    }
)

_RESTORE_SKIP_LABELS = (
    "don't restore",
    "dont restore",
    "do not restore",
    "cancel",
    "no",
    "close",
)

_CRASH_CLOSE_LABELS = (
    "close",
    "cancel",
    "don't send",
    "dont send",
    "no",
    "quit",
)

_DEFAULT_CRASH_PROCESS_NAMES = (
    "CrashReportClient",
    "CrashReportClientEditor",
)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002
OpenProcess = kernel32.OpenProcess
OpenProcess.restype = wintypes.HANDLE
QueryFullProcessImageNameW = kernel32.QueryFullProcessImageNameW
CloseHandle = kernel32.CloseHandle
CreateToolhelp32Snapshot = kernel32.CreateToolhelp32Snapshot
CreateToolhelp32Snapshot.restype = wintypes.HANDLE
Process32FirstW = kernel32.Process32FirstW
Process32NextW = kernel32.Process32NextW


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]

_TITLE_HINTS = (
    "error",
    "warning",
    "compilation",
    "compile",
    "message",
    "confirm",
    "save",
    "checkout",
    "failed",
    "unable",
    "cannot",
    "assert",
    "crash",
    "plugin",
    "missing",
    "overwrite",
    "discard",
    "reload",
    "hot reload",
    "live coding",
    "dialogue",
    "dialog",
    "restore",
    "package",
    "packages",
    "context menu",
    "message box",
    "message dialog",
)

_RESTORE_TITLE_HINTS = (
    "restore package",
    "restore packages",
    "packages to restore",
    "restore selected",
)

_CRASH_TITLE_HINTS = (
    "crash report",
    "crashreporter",
    "unreal engine crash",
    "send unattended",
    "bug report",
    "callstack",
)


def _title_matches(title: str, hints: tuple[str, ...]) -> bool:
    t = (title or "").lower()
    return bool(t) and any(h in t for h in hints)


def _is_destructive_label(label: str) -> bool:
    low = (label or "").strip().lower()
    if not low:
        return False
    if low in _DESTRUCTIVE_LABELS:
        return True
    return any(d in low for d in ("delete", "overwrite", "checkout", "discard"))


def _classify_blocker_kind(title: str, process: str = "") -> str:
    """Classify a dialog/process into a blocker kind for status + dismiss policy."""
    proc = (process or "").lower()
    if "crashreport" in proc.replace(" ", ""):
        return "crash_reporter"
    if _title_matches(title, _CRASH_TITLE_HINTS):
        return "crash_reporter"
    if _title_matches(title, _RESTORE_TITLE_HINTS):
        return "restore_packages"
    title_l = (title or "").lower()
    if "context menu" in title_l:
        return "context_menu"
    return "modal"


def _window_text(hwnd: int) -> str:
    n = GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer((n + 1) if n > 0 else 512)
    GetWindowTextW(hwnd, buf, len(buf))
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
    return Path(path).stem if path else ""


def _window_size(hwnd: int) -> tuple[int, int]:
    rect = wintypes.RECT()
    if not GetWindowRect(hwnd, ctypes.byref(rect)):
        return (0, 0)
    return (max(0, rect.right - rect.left), max(0, rect.bottom - rect.top))


def _send_key(vk: int) -> None:
    keybd_event(vk, 0, 0, 0)
    time.sleep(0.03)
    keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


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
        req = f"GET {path} HTTP/1.1\r\nHost: {host}\r\nConnection: close\r\n\r\n".encode("ascii")
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect((host, port))
        sock.sendall(req)
        data = sock.recv(64)
        sock.close()
        return {
            "listening": True,
            "responded": bool(data),
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


def resolve_proxy_script() -> Path | None:
    """Locate canonical unreal_mcp_proxy.py next to this Optional/ tree or via env."""
    env = os.environ.get("UNREAL_MCP_PROXY_SCRIPT", "").strip()
    if env:
        p = Path(env)
        return p if p.is_file() else None
    here = Path(__file__).resolve()
    candidates = [
        here.parents[1] / "UnrealMcpProxy" / "unreal_mcp_proxy.py",
        here.parent / "UnrealMcpProxy" / "unreal_mcp_proxy.py",
    ]
    project = os.environ.get("UNREAL_WATCH_PROJECT", "").strip()
    if project:
        candidates.insert(
            0,
            Path(project)
            / "Plugins"
            / "REAgentTools"
            / "Optional"
            / "UnrealMcpProxy"
            / "unreal_mcp_proxy.py",
        )
    for c in candidates:
        if c.is_file():
            return c
    return None


def probe_proxy_health(
    host: str = "127.0.0.1",
    port: int = 8001,
    timeout: float = 1.0,
) -> dict[str, Any]:
    """Identify anti-thrash proxy via /health. Never kills/rebinds the listener."""
    script = resolve_proxy_script()
    if script is not None:
        try:
            import importlib.util

            spec = importlib.util.spec_from_file_location("unreal_mcp_proxy", script)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                if hasattr(mod, "probe_proxy_health"):
                    return mod.probe_proxy_health(host, port)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "listening": _port_listening(host, port, min(timeout, 1.0)),
                "error": f"proxy_module_load: {exc}",
                "url": f"http://{host}:{port}/mcp",
            }
    # Fallback without importing proxy module
    basic = _http_probe(host, port, "/health", timeout)
    preview = str(basic.get("preview") or "")
    identity_ok = basic.get("responded") and "unreal-mcp-http-proxy" in preview
    return {
        "ok": bool(identity_ok),
        "listening": bool(basic.get("listening")),
        "health_probe": basic,
        "url": f"http://{host}:{port}/mcp",
        "advice": (
            "Proxy healthy — reuse; never kill/rebind."
            if identity_ok
            else "Proxy not identified. Do NOT kill :8001; run --ensure-http or inspect owner."
        ),
    }


def ensure_http_proxy_sidecar(
    host: str = "127.0.0.1",
    port: int = 8001,
) -> dict[str, Any]:
    """Bring up anti-thrash HTTP proxy if missing. Never kills an existing listener."""
    health = probe_proxy_health(host, port)
    if health.get("ok"):
        return {"ok": True, "already_up": True, "url": f"http://{host}:{port}/mcp", **health}
    script = resolve_proxy_script()
    if script is None:
        return {
            "ok": False,
            "error": "proxy script missing",
            "advice": "Install Optional/UnrealMcpProxy or set UNREAL_MCP_PROXY_SCRIPT",
        }
    try:
        import subprocess
        import sys

        creation = 0
        if sys.platform == "win32":
            creation = getattr(os, "DETACHED_PROCESS", 0x00000008) | getattr(
                os, "CREATE_NEW_PROCESS_GROUP", 0x00000200
            ) | getattr(os, "CREATE_NO_WINDOW", 0x08000000)
        subprocess.Popen(
            [sys.executable, str(script), "--ensure-http", f"{host}:{port}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation,
            close_fds=True,
        )
        for _ in range(40):
            health = probe_proxy_health(host, port)
            if health.get("ok"):
                return {
                    "ok": True,
                    "spawned": True,
                    "url": f"http://{host}:{port}/mcp",
                    **health,
                }
            time.sleep(0.1)
        return {"ok": False, "error": f"proxy did not bind :{port}", **health}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}


def find_unreal_pids_via_snapshot(process_names: list[str]) -> list[dict[str, Any]]:
    """Process-table scan (CreateToolhelp32Snapshot) — sees headless / no-HWND PIDs."""
    names = {n.lower() for n in process_names}
    found: dict[int, dict[str, Any]] = {}
    snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap or snap == wintypes.HANDLE(-1).value:
        return []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not Process32FirstW(snap, ctypes.byref(entry)):
            return []
        while True:
            exe = (entry.szExeFile or "").strip()
            stem = Path(exe).stem if exe else ""
            if stem.lower() in names:
                pid = int(entry.th32ProcessID)
                found[pid] = {
                    "pid": pid,
                    "process": stem,
                    "main_title": "",
                    "source": "process_snapshot",
                    "has_window": False,
                }
            if not Process32NextW(snap, ctypes.byref(entry)):
                break
    finally:
        CloseHandle(snap)
    return list(found.values())


def find_unreal_pids(process_names: list[str]) -> list[dict[str, Any]]:
    """Merge process snapshot + visible-window enum.

    Snapshot is authoritative for 'running'; window titles enrich the report.
    """
    names = {n.lower() for n in process_names}
    found: dict[int, dict[str, Any]] = {
        int(p["pid"]): dict(p) for p in find_unreal_pids_via_snapshot(process_names)
    }

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
        title = _window_text(hwnd)
        prev = found.get(pid.value, {})
        prefer_title = (not prev.get("main_title")) or ("unreal editor" in title.lower())
        found[pid.value] = {
            "pid": int(pid.value),
            "process": base or prev.get("process", ""),
            "main_title": (title if prefer_title else prev.get("main_title", "")) or title,
            "source": "window+snapshot" if prev else "window",
            "has_window": True,
        }
        return True

    EnumWindows(_cb, 0)
    return list(found.values())


def _child_buttons(hwnd: int) -> list[dict[str, Any]]:
    buttons: list[dict[str, Any]] = []
    action_labels = {
        "ok", "cancel", "yes", "no", "close", "retry", "ignore",
        "continue", "save", "don't save", "dont save", "apply",
    }

    @EnumWindowsProc
    def _cb(child, _lparam):
        cls = _class_name(child).lower()
        text = _window_text(child)
        if not text:
            return True
        if cls in ("button", "sbutton") or text.lower() in action_labels:
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


def _uia_button_names(hwnd: int) -> list[str]:
    try:
        import comtypes.client
    except Exception:
        return []
    try:
        mod = comtypes.client.GetModule("UIAutomationCore.dll")
        uia = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=mod.IUIAutomation,
        )
        element = uia.ElementFromHandle(hwnd)
        if not element:
            return []
        cond = uia.CreatePropertyCondition(
            mod.UIA_ControlTypePropertyId, mod.UIA_ButtonControlTypeId
        )
        found = element.FindAll(mod.TreeScope_Descendants, cond)
        names: list[str] = []
        for i in range(int(found.Length)):
            el = found.GetElement(i)
            try:
                name = str(el.CurrentName or "").strip()
            except Exception:
                name = ""
            if name:
                names.append(name)
        return names
    except Exception:
        return []


def _uia_invoke_button(hwnd: int, wanted_labels: list[str]) -> dict[str, Any] | None:
    try:
        import comtypes.client

        mod = comtypes.client.GetModule("UIAutomationCore.dll")
        uia = comtypes.client.CreateObject(
            "{ff48dba4-60ef-4201-aa87-54103eef594e}",
            interface=mod.IUIAutomation,
        )
        element = uia.ElementFromHandle(hwnd)
        if not element:
            return None
        cond = uia.CreatePropertyCondition(
            mod.UIA_ControlTypePropertyId, mod.UIA_ButtonControlTypeId
        )
        found = element.FindAll(mod.TreeScope_Descendants, cond)
        wanted = [w.lower() for w in wanted_labels]
        for i in range(int(found.Length)):
            el = found.GetElement(i)
            try:
                name = str(el.CurrentName or "").strip()
            except Exception:
                continue
            if not name:
                continue
            low = name.lower()
            if low in wanted or any(w == low or w in low for w in wanted):
                iface = el.GetCurrentPattern(mod.UIA_InvokePatternId)
                if iface:
                    pattern = iface.QueryInterface(mod.IUIAutomationInvokePattern)
                    pattern.Invoke()
                    return {"ok": True, "clicked": name, "method": "uia_invoke"}
        return None
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"uia: {exc}", "method": "uia_invoke"}


def find_crash_reporter_pids(process_names: list[str] | None = None) -> list[dict[str, Any]]:
    """Find CrashReportClient / CrashReportClientEditor via process snapshot + windows."""
    names = list(process_names or _DEFAULT_CRASH_PROCESS_NAMES)
    return find_unreal_pids(names)


def find_dialogs(
    process_names: list[str],
    extra_process_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Detect Win32 dialogs AND Unreal owned Slate UnrealWindow popups.

    Also scans CrashReportClient windows when ``extra_process_names`` is set.
    """
    names = {n.lower() for n in process_names}
    if extra_process_names:
        names |= {n.lower() for n in extra_process_names}
    dialogs: list[dict[str, Any]] = []
    main_hwnds: set[int] = set()

    @EnumWindowsProc
    def _mark_main(hwnd, _lparam):
        if not IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return True
        base = _pid_basename(pid.value)
        if base.lower() not in names:
            return True
        # Crash reporter windows are all "dialogs" — never treat as main editor.
        if "crashreport" in base.lower().replace(" ", ""):
            return True
        owner = GetWindow(hwnd, GW_OWNER)
        w, h = _window_size(hwnd)
        title = _window_text(hwnd)
        cls = _class_name(hwnd)
        if not owner and w >= 800 and h >= 600 and (
            cls == "UnrealWindow" or "unreal editor" in title.lower()
        ):
            main_hwnds.add(int(hwnd))
        return True

    EnumWindows(_mark_main, 0)

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

        hwnd_i = int(hwnd)
        if hwnd_i in main_hwnds:
            return True

        cls = _class_name(hwnd)
        title = _window_text(hwnd)
        owner = GetWindow(hwnd, GW_OWNER)
        owner_i = int(owner) if owner else 0
        w, h = _window_size(hwnd)
        buttons = _child_buttons(hwnd)
        title_l = title.lower()
        title_hit = any(h in title_l for h in _TITLE_HINTS) if title_l else False
        is_crash_proc = "crashreport" in base.lower().replace(" ", "")

        is_std = cls == "#32770"
        is_owned_slate = cls == "UnrealWindow" and owner_i != 0
        # Owned Slate popup: Message Log, Blueprint Asset Compilation Error, etc.
        # Also catch compact context-menu-like owned windows (often untitled).
        slate_popup = is_owned_slate and (
            title_hit
            or (100 <= w <= 1800 and 60 <= h <= 1400)
            or (not title and 40 <= w <= 800 and 40 <= h <= 600)
        )
        classic = is_std or (bool(buttons) and bool(title))
        crash_window = is_crash_proc and (is_std or cls == "UnrealWindow" or bool(title) or bool(buttons))

        if not (classic or slate_popup or crash_window):
            return True

        uia_names = _uia_button_names(hwnd_i)
        button_rows = [
            {"text": b["text"], "enabled": b["enabled"], "source": "win32"} for b in buttons
        ]
        for name in uia_names:
            if name.lower() not in {b["text"].lower() for b in button_rows}:
                button_rows.append({"text": name, "enabled": True, "source": "uia"})
        button_rows.extend(
            [
                {"text": "OK/Enter (keyboard)", "enabled": True, "source": "keyboard"},
                {"text": "Cancel/Esc (keyboard)", "enabled": True, "source": "keyboard"},
            ]
        )

        blocker_kind = _classify_blocker_kind(title, base)
        dialogs.append(
            {
                "hwnd": hwnd_i,
                "pid": int(pid.value),
                "process": base,
                "class": cls,
                "title": title or (
                    "(CrashReportClient)" if is_crash_proc else "(untitled UnrealWindow)"
                ),
                "owner_hwnd": owner_i,
                "size": [w, h],
                "kind": (
                    "crash_reporter"
                    if is_crash_proc or blocker_kind == "crash_reporter"
                    else ("slate" if is_owned_slate else ("win32" if is_std else "other"))
                ),
                "blocker_kind": blocker_kind,
                "buttons": button_rows,
                "_button_hwnds": {b["text"].lower(): b["hwnd"] for b in buttons},
                "_slate": bool(is_owned_slate),
            }
        )
        return True

    EnumWindows(_cb, 0)

    def _score(d: dict[str, Any]) -> tuple:
        kind = d.get("blocker_kind") or ""
        t = (d.get("title") or "").lower()
        pri = 0
        if kind == "crash_reporter" or "crash" in t:
            pri = 5
        elif kind == "restore_packages":
            pri = 4
        elif "error" in t or "compilation" in t or "compile" in t:
            pri = 3
        elif "warning" in t or "message" in t:
            pri = 2
        elif d.get("kind") == "slate":
            pri = 1
        area = (d.get("size") or [0, 0])[0] * (d.get("size") or [0, 0])[1]
        return (-pri, -area)

    dialogs.sort(key=_score)
    return dialogs


def _close_hwnd(hwnd: int) -> dict[str, Any]:
    try:
        ShowWindow(hwnd, SW_RESTORE)
        SetForegroundWindow(hwnd)
        time.sleep(0.03)
    except Exception:
        pass
    SendMessageW(hwnd, WM_CLOSE, 0, 0)
    return {"ok": True, "clicked": "WM_CLOSE", "method": "wm_close", "hwnd": int(hwnd)}


def click_button(dialog: dict[str, Any], choice: str) -> dict[str, Any]:
    mapping = {
        "accept": ["ok", "yes", "continue", "retry", "save", "apply"],
        "ok": ["ok"],
        "yes": ["yes"],
        "cancel": ["cancel", "close", "no", "don't restore", "dont restore", "don't send", "dont send"],
        "no": ["no"],
        "close": ["close", "cancel"],
        "dont_restore": ["don't restore", "dont restore", "do not restore", "cancel", "no"],
        "don't_restore": ["don't restore", "dont restore", "do not restore", "cancel", "no"],
    }
    wanted = (choice or "").strip().lower()
    candidates = mapping.get(wanted, [wanted])
    hwnd = int(dialog["hwnd"])
    hwnds: dict[str, int] = dict(dialog.get("_button_hwnds") or {})
    if not hwnds:
        hwnds = {b["text"].lower(): b["hwnd"] for b in _child_buttons(hwnd)}

    for label in candidates:
        if label in hwnds:
            SendMessageW(hwnds[label], BM_CLICK, 0, 0)
            return {"ok": True, "clicked": label, "method": "win32_bm_click"}

    uia = _uia_invoke_button(hwnd, candidates if wanted in mapping else [wanted])
    if uia and uia.get("ok"):
        return uia

    try:
        ShowWindow(hwnd, SW_RESTORE)
        SetForegroundWindow(hwnd)
        time.sleep(0.05)
    except Exception:
        pass

    if wanted in ("cancel", "no", "close", "escape", "dont_restore", "don't_restore"):
        _send_key(VK_ESCAPE)
        return {
            "ok": True,
            "clicked": "Escape",
            "method": "keyboard",
            "hwnd": hwnd,
            "note": "Slate/owned UnrealWindow — sent Escape",
        }
    _send_key(VK_RETURN)
    return {
        "ok": True,
        "clicked": "Enter",
        "method": "keyboard",
        "hwnd": hwnd,
        "note": "Slate/owned UnrealWindow — sent Enter (default accept)",
        "uia_error": (uia or {}).get("error") if isinstance(uia, dict) else None,
    }


def load_config(path: Path | None = None) -> dict[str, Any]:
    cfg_path = path or Path(__file__).with_name("config.json")
    defaults = {
        "mode": os.environ.get("UNREAL_WATCH_MODE", "report"),
        "auto_allowlist": ["OK", "Close"],
        "never_auto": [
            "Don't Save", "Dont Save", "Delete", "Overwrite",
            "Checkout", "Discard", "Abort", "No",
        ],
        "unreal_process_names": [
            "UnrealEditor",
            "UnrealEditor-Win64-DebugGame",
            "UnrealEditor-Win64-Debug",
        ],
        "crash_reporter_process_names": list(_DEFAULT_CRASH_PROCESS_NAMES),
        # Safe default for post-crash package restore: skip restore (Cancel / Don't Restore).
        "restore_packages_policy": os.environ.get(
            "UNREAL_WATCH_RESTORE_POLICY", "dont_restore"
        ),
        "mcp_probe_host": "127.0.0.1",
        "mcp_probe_port": 8000,
        "proxy_probe_host": "127.0.0.1",
        "proxy_probe_port": 8001,
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
    env_restore = os.environ.get("UNREAL_WATCH_RESTORE_POLICY")
    if env_restore:
        defaults["restore_packages_policy"] = env_restore
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

def _public_dialog(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "hwnd": d["hwnd"],
        "pid": d["pid"],
        "process": d["process"],
        "class": d["class"],
        "title": d["title"],
        "kind": d.get("kind"),
        "blocker_kind": d.get("blocker_kind")
        or _classify_blocker_kind(str(d.get("title") or ""), str(d.get("process") or "")),
        "size": d.get("size"),
        "owner_hwnd": d.get("owner_hwnd"),
        "buttons": [
            {"text": b.get("text"), "enabled": b.get("enabled"), "source": b.get("source")}
            for b in (d.get("buttons") or [])
            if b.get("source") != "keyboard"
        ]
        + [b for b in (d.get("buttons") or []) if b.get("source") == "keyboard"],
    }


def _instruction_for_status(
    status: str,
    *,
    titles: list[Any] | None = None,
) -> str:
    """STOP / RECOVER agent instructions keyed by status enum."""
    titles = titles or []
    if status == STATUS_EDITOR_OFFLINE:
        return (
            "STOP. Unreal Editor is not running (status=editor_offline). "
            "Do not retry Unreal MCP. RECOVER: ask the user to launch the project, "
            "then call wait_for_editor (returns early on modal/crash)."
        )
    if status == STATUS_CRASH_REPORTER:
        return (
            "STOP Unreal MCP retries. status=crash_reporter — Crash Report Client "
            f"and/or crash UI present ({titles}). RECOVER: call dismiss_unreal_blocker "
            "(safe_cancel closes/cancels; never auto-sends reports). Then wait_for_editor "
            "or ask the user to relaunch if the editor exited."
        )
    if status == STATUS_RESTORE_PACKAGES:
        return (
            "STOP Unreal MCP retries. status=restore_packages — Restore Packages dialog "
            f"detected ({titles}). RECOVER: call dismiss_unreal_blocker "
            "(default policy=dont_restore / Cancel). Never click Delete without "
            "allow_destructive=true."
        )
    if status == STATUS_MODAL_BLOCKED:
        return (
            "STOP Unreal MCP retries. status=modal_blocked — blocking Slate/Win32 dialog "
            f"or context menu ({titles}). RECOVER: call dismiss_unreal_blocker "
            "(safe_cancel → Escape/Cancel) or ask the user. Never kill/rebind :8001."
        )
    if status == STATUS_PORTS_WEDGED:
        return (
            "STOP Unreal MCP retries. status=ports_wedged — editor ports listen but do not "
            "answer (nested UI / busy game thread). RECOVER: call get_editor_status; if a "
            "modal appeared use dismiss_unreal_blocker; else wait / ask user. "
            "Never kill/rebind :8001."
        )
    if status == STATUS_PROXY_UNHEALTHY:
        return (
            "Editor appears up but anti-thrash proxy :8001 is not identified "
            "(status=proxy_unhealthy). RECOVER: run Optional/UnrealMcpProxy --ensure-http; "
            "do not point Cursor at raw :8000; never kill/rebind an unknown :8001 listener."
        )
    if status == STATUS_OK:
        return (
            "Editor watch clear — Unreal MCP via :8001 may be used. "
            "On address-in-use / WinError 10048: verify /health and reuse; never kill proxy. "
            "Contract: call get_editor_status before Unreal MCP batches; on modal_blocked / "
            "crash_reporter / restore_packages call dismiss_unreal_blocker."
        )
    return (
        f"status={status}. Follow advice[]; do not spam Unreal MCP. "
        "RECOVER: get_editor_status → dismiss_unreal_blocker if blocked. "
        "Never kill/rebind :8001."
    )


def check_unreal(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg or load_config()
    timeout = float(cfg.get("probe_timeout_s", 2.0))
    names = list(cfg.get("unreal_process_names") or ["UnrealEditor"])
    crash_names = list(
        cfg.get("crash_reporter_process_names") or _DEFAULT_CRASH_PROCESS_NAMES
    )

    processes = find_unreal_pids(names)
    crash_processes = find_crash_reporter_pids(crash_names)
    dialogs_raw = find_dialogs(names, extra_process_names=crash_names)
    dialogs_public = [_public_dialog(d) for d in dialogs_raw]

    mcp = _http_probe(
        str(cfg.get("mcp_probe_host", "127.0.0.1")),
        int(cfg.get("mcp_probe_port", 8000)),
        "/mcp",
        timeout,
    )
    proxy_host = str(cfg.get("proxy_probe_host", "127.0.0.1"))
    proxy_port = int(cfg.get("proxy_probe_port", 8001))
    http_proxy = ensure_http_proxy_sidecar(proxy_host, proxy_port)
    proxy = probe_proxy_health(proxy_host, proxy_port, timeout)
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
    editor_responsive = bool(mcp.get("responded") or rc.get("responded"))
    blocker_kinds = {str(d.get("blocker_kind") or "") for d in dialogs_public}
    has_crash_dialog = "crash_reporter" in blocker_kinds
    visible_crc = [p for p in crash_processes if p.get("has_window")]
    # Orphan CrashReportClientEditor PIDs with no HWND are common after a prior
    # crash; do not block a responsive editor on those alone.
    has_crash_ui = bool(has_crash_dialog) or bool(visible_crc) or (
        bool(crash_processes) and not bool(processes)
    )
    has_restore = "restore_packages" in blocker_kinds
    modal_blocking = (modal_present and not editor_responsive) or has_crash_ui or has_restore

    unreal_running = bool(processes)
    abort_unreal_mcp = False
    # Priority: crash_reporter > restore_packages > offline > modal > wedged > proxy > ok
    if has_crash_ui:
        status = STATUS_CRASH_REPORTER
        likely_blocked = True
        abort_unreal_mcp = True
    elif has_restore:
        status = STATUS_RESTORE_PACKAGES
        likely_blocked = True
        abort_unreal_mcp = True
    elif not unreal_running:
        status = STATUS_EDITOR_OFFLINE
        likely_blocked = True
        abort_unreal_mcp = True
    elif modal_present and not editor_responsive:
        status = STATUS_MODAL_BLOCKED
        likely_blocked = True
        abort_unreal_mcp = True
    elif ports_up_no_reply:
        status = STATUS_PORTS_WEDGED
        likely_blocked = True
        abort_unreal_mcp = True
    elif not proxy.get("ok") and (mcp.get("listening") or rc.get("listening")):
        status = STATUS_PROXY_UNHEALTHY
        likely_blocked = False
        abort_unreal_mcp = False
    else:
        status = STATUS_OK
        likely_blocked = False
        abort_unreal_mcp = False

    advice: list[str] = []
    titles = [d.get("title") for d in dialogs_public]
    if has_crash_ui:
        advice.append(
            f"Crash reporter process/UI detected (pids={[p.get('pid') for p in crash_processes]}, "
            f"dialogs={titles}). Call dismiss_unreal_blocker; do not spam Unreal MCP."
        )
    if has_restore:
        policy = str(cfg.get("restore_packages_policy") or "dont_restore")
        advice.append(
            f"Restore Packages dialog detected: {titles}. "
            f"Policy={policy} — dismiss_unreal_blocker skips restore by default."
        )
    if not unreal_running and not has_crash_ui:
        advice.append("UnrealEditor process not found — start the editor.")
    elif status == STATUS_MODAL_BLOCKED:
        advice.append(
            f"Owned Slate/Win32 dialog(s) detected: {titles} and MCP/RC are not "
            "answering. Do NOT spam Unreal MCP. Call dismiss_unreal_blocker "
            "or ask the user."
        )
    elif ports_up_no_reply and status == STATUS_PORTS_WEDGED:
        advice.append(
            "MCP/RC ports listen but probes timed out — editor thread likely busy or nested UI. "
            "Do NOT kill/rebind :8001."
        )
    elif modal_present and status == STATUS_OK:
        advice.append(
            f"Window(s) detected: {titles}, but MCP/RC answered — the editor is "
            "responsive and these are not blocking. Proceed with ONE batched MCP "
            "call; only dismiss_unreal_blocker if calls actually fail."
        )
    elif status == STATUS_OK and proxy.get("ok") and (mcp.get("listening") or rc.get("listening")):
        advice.append(
            "Anti-thrash proxy :8001 healthy and editor ports up — use Cursor MCP via :8001; "
            "never kill/rebind the proxy on WinError 10048."
        )
    elif status == STATUS_PROXY_UNHEALTHY:
        advice.append(
            "Editor MCP/RC listening but proxy identity not confirmed — "
            "run Optional/UnrealMcpProxy --ensure-http; do not point Cursor at raw :8000."
        )
    elif unreal_running and not editor_responsive and status == STATUS_OK:
        advice.append("Editor running but MCP/RC not listening.")

    proxy_health = proxy.get("health") if isinstance(proxy.get("health"), dict) else {}
    if proxy.get("outdated"):
        advice.append(
            f"Proxy is running {proxy.get('running_version')} but canonical is "
            f"{proxy.get('canonical_version')} — keep using it. Upgrade with one "
            "`unreal_mcp_proxy.py --restart 127.0.0.1:8001` when the editor is idle."
        )
    if int(proxy_health.get("consecutive_empty") or 0) > 0:
        advice.append(
            "Proxy saw consecutive empty replies from Unreal — resubmit the pending "
            "MCP call ONCE; the proxy re-establishes the session itself. "
            "Do not switch to RC yet and do not kill :8001."
        )

    agent_instruction = _instruction_for_status(status, titles=titles)

    auto_action = None
    mode = str(cfg.get("mode", "report")).lower()
    if mode == "auto_allowlist" and dialogs_raw and status not in (
        STATUS_CRASH_REPORTER,
        STATUS_RESTORE_PACKAGES,
    ):
        allow = {str(x).lower() for x in (cfg.get("auto_allowlist") or [])}
        never = {str(x).lower() for x in (cfg.get("never_auto") or [])}
        dlg = dialogs_raw[0]
        title_l = str(dlg.get("title") or "").lower()
        if any(
            n in title_l
            for n in ("save", "delete", "overwrite", "checkout", "discard", "restore")
        ):
            auto_action = {"skipped": True, "reason": f"never auto title: {dlg.get('title')}"}
        else:
            clicked = False
            for b in dlg.get("buttons") or []:
                label = str(b.get("text", "")).lower()
                if b.get("source") == "keyboard":
                    continue
                if label in never or _is_destructive_label(label):
                    auto_action = {
                        "skipped": True,
                        "reason": f"never_auto matched {b.get('text')}",
                    }
                    clicked = True
                    break
                if label in allow and b.get("enabled", True):
                    auto_action = click_button(dlg, b["text"])
                    auto_action["mode"] = "auto_allowlist"
                    auto_action["dialog_title"] = dlg.get("title")
                    clicked = True
                    break
            if not clicked and auto_action is None:
                if "error" in title_l or "message" in title_l or "compilation" in title_l:
                    if "ok" in allow or "close" in allow:
                        auto_action = click_button(dlg, "accept")
                        auto_action["mode"] = "auto_allowlist_keyboard"
                        auto_action["dialog_title"] = dlg.get("title")
                    else:
                        auto_action = {
                            "skipped": True,
                            "reason": "report mode would apply; OK not allowlisted",
                        }
                else:
                    auto_action = {
                        "skipped": True,
                        "reason": "no allowlisted button",
                        "buttons": [b.get("text") for b in dlg.get("buttons") or []],
                    }

    report = {
        "ok": True,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": mode,
        "status": status,
        "unreal_running": unreal_running,
        "abort_unreal_mcp": abort_unreal_mcp,
        "processes": processes,
        "crash_reporter_processes": crash_processes,
        "modal": {
            "present": modal_present,
            "blocking": modal_blocking,
            "count": len(dialogs_public),
            "blocker_kinds": sorted(k for k in blocker_kinds if k),
            "dialogs": dialogs_public,
        },
        "editor_responsive": editor_responsive,
        "mcp_probe": mcp,
        "proxy_probe": proxy,
        "http_proxy": http_proxy,
        "rc_probe": rc,
        "likely_blocked": likely_blocked,
        "advice": advice,
        "agent_instruction": agent_instruction,
        "recover_tool": (
            "dismiss_unreal_blocker"
            if status
            in (STATUS_MODAL_BLOCKED, STATUS_CRASH_REPORTER, STATUS_RESTORE_PACKAGES)
            else ("wait_for_editor" if status == STATUS_EDITOR_OFFLINE else None)
        ),
        "auto_action": auto_action,
        "_dialogs_raw": dialogs_raw,
    }

    alert = project_alert_path()
    if alert:
        public = {k: v for k, v in report.items() if not k.startswith("_")}
        alert.write_text(json.dumps(public, indent=2) + "\n", encoding="utf-8")
        report["alert_path"] = str(alert)
    return report


def get_editor_status(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Compact structured status for agents (same check, trimmed fields)."""
    report = check_unreal(cfg)
    keys = (
        "ok",
        "ts",
        "status",
        "unreal_running",
        "abort_unreal_mcp",
        "editor_responsive",
        "likely_blocked",
        "modal",
        "crash_reporter_processes",
        "advice",
        "agent_instruction",
        "recover_tool",
        "alert_path",
        "processes",
        "mcp_probe",
        "proxy_probe",
        "rc_probe",
    )
    return {k: report[k] for k in keys if k in report}


def wait_for_editor(
    timeout_s: float = 120.0,
    poll_s: float = 2.0,
    return_on_blocker: bool = True,
    cfg: dict[str, Any] | None = None,
    progress_cb: Any | None = None,
) -> dict[str, Any]:
    """Poll until editor is ready, a blocker appears, or timeout.

    Returns early on modal/crash/restore when ``return_on_blocker`` is True so
    agents can call ``dismiss_unreal_blocker`` instead of waiting out the clock.
    ``editor_offline`` keeps polling (user may still be launching).
    """
    cfg = cfg or load_config()
    timeout_s = max(0.0, float(timeout_s))
    poll_s = max(0.2, float(poll_s))
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    ticks = 0
    while True:
        last = check_unreal(cfg)
        ticks += 1
        status = str(last.get("status") or "")
        if progress_cb is not None:
            try:
                progress_cb(ticks, status, last)
            except Exception:  # noqa: BLE001
                pass
        if (
            status == STATUS_OK
            and last.get("unreal_running")
            and last.get("editor_responsive")
        ):
            last["wait_result"] = "ready"
            return {k: v for k, v in last.items() if not k.startswith("_")}
        if return_on_blocker and status in BLOCKER_STATUSES:
            last["wait_result"] = f"blocker:{status}"
            return {k: v for k, v in last.items() if not k.startswith("_")}
        if time.time() >= deadline:
            last["wait_result"] = "timeout"
            return {k: v for k, v in last.items() if not k.startswith("_")}
        time.sleep(poll_s)


def run_heartbeat_once(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """Single heartbeat tick — writes modal_alert.json when project path is set."""
    report = check_unreal(cfg)
    return {k: v for k, v in report.items() if not k.startswith("_")}


def start_heartbeat_thread(interval_s: float) -> Any:
    """Daemon thread that periodically runs check_unreal (alert file + process scan)."""
    import threading

    interval_s = max(1.0, float(interval_s))
    stop = threading.Event()

    def _loop() -> None:
        while not stop.wait(interval_s):
            try:
                run_heartbeat_once()
            except Exception:  # noqa: BLE001
                pass

    try:
        run_heartbeat_once()
    except Exception:  # noqa: BLE001
        pass
    t = threading.Thread(target=_loop, name="unreal-watch-heartbeat", daemon=True)
    t.start()
    return stop


def dismiss_dialog(
    choice: str = "accept",
    hwnd: int | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = cfg or load_config()
    names = list(cfg.get("unreal_process_names") or ["UnrealEditor"])
    crash_names = list(
        cfg.get("crash_reporter_process_names") or _DEFAULT_CRASH_PROCESS_NAMES
    )
    dialogs = find_dialogs(names, extra_process_names=crash_names)
    if not dialogs:
        return {"ok": False, "error": "No Unreal dialog detected"}
    target = None
    if hwnd:
        for d in dialogs:
            if int(d["hwnd"]) == int(hwnd):
                target = d
                break
        if target is None:
            return {
                "ok": False,
                "error": f"Dialog hwnd {hwnd} not found",
                "dialogs": [
                    {"hwnd": d["hwnd"], "title": d["title"], "kind": d.get("kind")}
                    for d in dialogs
                ],
            }
    else:
        target = dialogs[0]
    result = click_button(target, choice)
    result["dialog_title"] = target.get("title")
    result["dialog_hwnd"] = target.get("hwnd")
    result["kind"] = target.get("kind")
    result["blocker_kind"] = target.get("blocker_kind")
    result["available_buttons"] = [b.get("text") for b in target.get("buttons") or []]
    return result


def dismiss_unreal_blocker(
    policy: str = "safe_cancel",
    allow_destructive: bool = False,
    hwnd: int | None = None,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Safely dismiss known Unreal blockers (dialogs, crash reporter, restore packages).

    Policies:
      - safe_cancel (default): Escape / Cancel / Close / Don't Restore / Don't Send
      - accept: OK / Yes / Enter (still refuses Delete unless allow_destructive)
      - restore_packages_skip: force Don't Restore / Cancel for Restore Packages
      - crash_reporter_close: close Crash Report Client (Cancel / WM_CLOSE)

    Never auto-clicks destructive labels (Delete, Overwrite, …) unless
    ``allow_destructive=True``.
    """
    cfg = cfg or load_config()
    policy_l = (policy or "safe_cancel").strip().lower()
    names = list(cfg.get("unreal_process_names") or ["UnrealEditor"])
    crash_names = list(
        cfg.get("crash_reporter_process_names") or _DEFAULT_CRASH_PROCESS_NAMES
    )
    dialogs = find_dialogs(names, extra_process_names=crash_names)
    crash_procs = find_crash_reporter_pids(crash_names)

    if not dialogs and not crash_procs:
        return {
            "ok": False,
            "error": "No Unreal blocker detected",
            "hint": "Call get_editor_status; if status=ok there is nothing to dismiss.",
        }

    target: dict[str, Any] | None = None
    if hwnd:
        for d in dialogs:
            if int(d["hwnd"]) == int(hwnd):
                target = d
                break
        if target is None:
            return {
                "ok": False,
                "error": f"Dialog hwnd {hwnd} not found",
                "dialogs": [
                    {
                        "hwnd": d["hwnd"],
                        "title": d["title"],
                        "blocker_kind": d.get("blocker_kind"),
                    }
                    for d in dialogs
                ],
            }
    elif dialogs:
        target = dialogs[0]

    restore_policy = str(cfg.get("restore_packages_policy") or "dont_restore").lower()
    blocker_kind = (target or {}).get("blocker_kind") or (
        "crash_reporter" if crash_procs else "modal"
    )

    if policy_l in ("restore_packages_skip", "dont_restore", "don't_restore") or (
        blocker_kind == "restore_packages"
        and policy_l == "safe_cancel"
        and restore_policy in ("dont_restore", "don't_restore", "cancel", "skip")
    ):
        choice = "dont_restore"
    elif policy_l in ("crash_reporter_close",) or (
        blocker_kind == "crash_reporter" and policy_l == "safe_cancel"
    ):
        choice = "close"
    elif policy_l in ("accept", "ok", "yes"):
        choice = "accept"
    else:
        choice = "cancel"

    if target and not allow_destructive:
        real = [
            str(b.get("text") or "")
            for b in (target.get("buttons") or [])
            if b.get("source") != "keyboard" and b.get("enabled", True)
        ]
        destructive = [t for t in real if _is_destructive_label(t)]
        if choice in ("accept", "yes", "ok"):
            safe_accept = [
                t
                for t in real
                if t.lower() in ("ok", "yes", "continue", "close", "retry")
                and not _is_destructive_label(t)
            ]
            title_l = str(target.get("title") or "").lower()
            title_destructive = any(
                w in title_l for w in ("delete", "overwrite", "checkout", "discard")
            )
            if destructive and (not safe_accept or title_destructive):
                return {
                    "ok": False,
                    "error": "Refused destructive dismiss without allow_destructive=true",
                    "destructive_buttons": destructive,
                    "dialog_title": target.get("title"),
                    "blocker_kind": blocker_kind,
                    "hint": "Pass allow_destructive=true only when the user explicitly confirmed.",
                }
        for t in real:
            if _is_destructive_label(t) and choice == t.lower():
                return {
                    "ok": False,
                    "error": (
                        f"Refused to click destructive '{t}' without "
                        "allow_destructive=true"
                    ),
                    "dialog_title": target.get("title"),
                    "blocker_kind": blocker_kind,
                }

    actions: list[dict[str, Any]] = []
    if target:
        if blocker_kind == "restore_packages" and choice == "dont_restore":
            result = click_button(target, "dont_restore")
            if result.get("method") == "keyboard":
                uia = _uia_invoke_button(int(target["hwnd"]), list(_RESTORE_SKIP_LABELS))
                if uia and uia.get("ok"):
                    result = uia
            actions.append(result)
        elif blocker_kind == "crash_reporter":
            result = click_button(target, choice if choice != "accept" else "close")
            if not result.get("ok") or result.get("method") == "keyboard":
                uia = _uia_invoke_button(int(target["hwnd"]), list(_CRASH_CLOSE_LABELS))
                if uia and uia.get("ok"):
                    result = uia
                else:
                    result = _close_hwnd(int(target["hwnd"]))
            actions.append(result)
        else:
            if choice == "accept":
                actions.append(click_button(target, "accept"))
            else:
                actions.append(click_button(target, choice))
        actions[-1]["dialog_title"] = target.get("title")
        actions[-1]["dialog_hwnd"] = target.get("hwnd")
        actions[-1]["blocker_kind"] = blocker_kind
    elif crash_procs:
        crc_dialogs = find_dialogs(["__none__"], extra_process_names=crash_names)
        if crc_dialogs:
            actions.append(_close_hwnd(int(crc_dialogs[0]["hwnd"])))
            actions[-1]["dialog_title"] = crc_dialogs[0].get("title")
            actions[-1]["blocker_kind"] = "crash_reporter"
        else:
            return {
                "ok": False,
                "error": "CrashReportClient process found but no dismissible window",
                "crash_reporter_processes": crash_procs,
                "hint": "Ask the user to close the crash reporter, then relaunch the editor.",
            }

    primary = actions[0] if actions else {"ok": False, "error": "no action"}
    followup = get_editor_status(cfg)
    return {
        "ok": bool(primary.get("ok")),
        "policy": policy_l,
        "allow_destructive": bool(allow_destructive),
        "blocker_kind": blocker_kind,
        "action": primary,
        "actions": actions,
        "status_after": followup.get("status"),
        "agent_instruction": followup.get("agent_instruction"),
        "followup": followup,
    }
