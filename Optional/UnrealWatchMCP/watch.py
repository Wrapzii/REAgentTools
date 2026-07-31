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
SW_RESTORE = 9
VK_RETURN = 0x0D
VK_ESCAPE = 0x1B
KEYEVENTF_KEYUP = 0x0002

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
)


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


def find_dialogs(process_names: list[str]) -> list[dict[str, Any]]:
    """Detect Win32 dialogs AND Unreal owned Slate UnrealWindow popups."""
    names = {n.lower() for n in process_names}
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
        if _pid_basename(pid.value).lower() not in names:
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

        is_std = cls == "#32770"
        is_owned_slate = cls == "UnrealWindow" and owner_i != 0
        # Owned Slate popup: Message Log, Blueprint Asset Compilation Error, etc.
        slate_popup = is_owned_slate and (
            title_hit or (100 <= w <= 1800 and 60 <= h <= 1400)
        )
        classic = is_std or (bool(buttons) and bool(title))

        if not (classic or slate_popup):
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

        dialogs.append(
            {
                "hwnd": hwnd_i,
                "pid": int(pid.value),
                "process": base,
                "class": cls,
                "title": title or "(untitled UnrealWindow)",
                "owner_hwnd": owner_i,
                "size": [w, h],
                "kind": "slate" if is_owned_slate else ("win32" if is_std else "other"),
                "buttons": button_rows,
                "_button_hwnds": {b["text"].lower(): b["hwnd"] for b in buttons},
                "_slate": bool(is_owned_slate),
            }
        )
        return True

    EnumWindows(_cb, 0)

    def _score(d: dict[str, Any]) -> tuple:
        t = (d.get("title") or "").lower()
        pri = 0
        if "error" in t or "compilation" in t or "compile" in t:
            pri = 3
        elif "warning" in t or "message" in t:
            pri = 2
        elif d.get("kind") == "slate":
            pri = 1
        area = (d.get("size") or [0, 0])[0] * (d.get("size") or [0, 0])[1]
        return (-pri, -area)

    dialogs.sort(key=_score)
    return dialogs


def click_button(dialog: dict[str, Any], choice: str) -> dict[str, Any]:
    mapping = {
        "accept": ["ok", "yes", "continue", "retry", "save", "apply"],
        "ok": ["ok"],
        "yes": ["yes"],
        "cancel": ["cancel", "close", "no"],
        "no": ["no"],
        "close": ["close", "cancel"],
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

    if wanted in ("cancel", "no", "close", "escape"):
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
    dialogs_public = []
    for d in dialogs_raw:
        dialogs_public.append(
            {
                "hwnd": d["hwnd"],
                "pid": d["pid"],
                "process": d["process"],
                "class": d["class"],
                "title": d["title"],
                "kind": d.get("kind"),
                "size": d.get("size"),
                "owner_hwnd": d.get("owner_hwnd"),
                "buttons": [
                    {"text": b.get("text"), "enabled": b.get("enabled"), "source": b.get("source")}
                    for b in (d.get("buttons") or [])
                    if b.get("source") != "keyboard"
                ]
                + [b for b in (d.get("buttons") or []) if b.get("source") == "keyboard"],
            }
        )

    mcp = _http_probe(
        str(cfg.get("mcp_probe_host", "127.0.0.1")),
        int(cfg.get("mcp_probe_port", 8000)),
        "/mcp",
        timeout,
    )
    proxy_host = str(cfg.get("proxy_probe_host", "127.0.0.1"))
    proxy_port = int(cfg.get("proxy_probe_port", 8001))
    # Ensure sidecar once (identity-aware; never kills/rebinds).
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
    # Owned UnrealWindows include harmless floating tabs (Message Log, Output Log).
    # A window only proves blockage when the editor also stops serving probes —
    # otherwise reporting it as modal stalls agents on a healthy editor.
    modal_blocking = modal_present and not editor_responsive

    unreal_running = bool(processes)
    abort_unreal_mcp = False
    if not unreal_running:
        status = "editor_offline"
        likely_blocked = True
        abort_unreal_mcp = True
    elif modal_blocking:
        status = "modal_blocked"
        likely_blocked = True
        abort_unreal_mcp = True
    elif ports_up_no_reply:
        status = "ports_wedged"
        likely_blocked = True
        abort_unreal_mcp = True
    elif not proxy.get("ok") and (mcp.get("listening") or rc.get("listening")):
        status = "proxy_unhealthy"
        likely_blocked = False
        abort_unreal_mcp = False
    else:
        status = "ok"
        likely_blocked = False
        abort_unreal_mcp = False

    advice: list[str] = []
    titles = [d.get("title") for d in dialogs_public]
    if not unreal_running:
        advice.append("UnrealEditor process not found — start the editor.")
    elif modal_blocking:
        advice.append(
            f"Owned Slate/Win32 dialog(s) detected: {titles} and MCP/RC are not "
            "answering. Do NOT spam Unreal MCP. Call dismiss_dialog(accept|cancel) "
            "or ask the user."
        )
    elif ports_up_no_reply:
        advice.append(
            "MCP/RC ports listen but probes timed out — editor thread likely busy or nested UI. "
            "Do NOT kill/rebind :8001."
        )
    elif modal_present:
        advice.append(
            f"Window(s) detected: {titles}, but MCP/RC answered — the editor is "
            "responsive and these are not blocking. Proceed with ONE batched MCP "
            "call; only dismiss_dialog if calls actually fail."
        )
    elif proxy.get("ok") and (mcp.get("listening") or rc.get("listening")):
        advice.append(
            "Anti-thrash proxy :8001 healthy and editor ports up — use Cursor MCP via :8001; "
            "never kill/rebind the proxy on WinError 10048."
        )
    elif mcp.get("listening") or rc.get("listening"):
        advice.append(
            "Editor MCP/RC listening but proxy identity not confirmed — "
            "run Optional/UnrealMcpProxy --ensure-http; do not point Cursor at raw :8000."
        )
    else:
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

    if status == "editor_offline":
        agent_instruction = (
            "STOP. Unreal Editor is not running (status=editor_offline). "
            "Do not retry Unreal MCP. Ask the user to launch the project, "
            "or call wait_for_editor after they start it."
        )
    elif status == "modal_blocked":
        agent_instruction = (
            "STOP Unreal MCP retries. status=modal_blocked — dismiss_dialog(accept|cancel) "
            "or ask the user. Never kill/rebind :8001."
        )
    elif status == "ports_wedged":
        agent_instruction = (
            "STOP Unreal MCP retries. status=ports_wedged — editor ports listen but do not "
            "answer. Wait / ask user; never kill/rebind :8001."
        )
    elif status == "proxy_unhealthy":
        agent_instruction = (
            "Editor appears up but anti-thrash proxy :8001 is not identified. "
            "Run Optional/UnrealMcpProxy --ensure-http; do not point Cursor at raw :8000; "
            "never kill/rebind an unknown :8001 listener."
        )
    elif (
        unreal_running
        and editor_responsive
        and not modal_blocking
        and status == "ok"
    ):
        agent_instruction = (
            "Editor watch clear — Unreal MCP via :8001 may be used. "
            "On address-in-use / WinError 10048: verify /health and reuse; never kill proxy."
        )
    else:
        agent_instruction = (
            f"status={status}. Follow advice[]; do not spam Unreal MCP. "
            "Never kill/rebind :8001."
        )

    auto_action = None
    mode = str(cfg.get("mode", "report")).lower()
    if mode == "auto_allowlist" and dialogs_raw:
        allow = {str(x).lower() for x in (cfg.get("auto_allowlist") or [])}
        never = {str(x).lower() for x in (cfg.get("never_auto") or [])}
        dlg = dialogs_raw[0]
        # Prefer real button labels; never auto-Enter on unknown destructive titles
        title_l = str(dlg.get("title") or "").lower()
        if any(n in title_l for n in ("save", "delete", "overwrite", "checkout", "discard")):
            auto_action = {"skipped": True, "reason": f"never auto title: {dlg.get('title')}"}
        else:
            clicked = False
            for b in dlg.get("buttons") or []:
                label = str(b.get("text", "")).lower()
                if b.get("source") == "keyboard":
                    continue
                if label in never:
                    auto_action = {"skipped": True, "reason": f"never_auto matched {b.get('text')}"}
                    clicked = True
                    break
                if label in allow and b.get("enabled", True):
                    auto_action = click_button(dlg, b["text"])
                    auto_action["mode"] = "auto_allowlist"
                    auto_action["dialog_title"] = dlg.get("title")
                    clicked = True
                    break
            if not clicked and auto_action is None:
                # Safe default for info/error OK dialogs: Enter
                if "error" in title_l or "message" in title_l or "compilation" in title_l:
                    if "ok" in allow or "close" in allow:
                        auto_action = click_button(dlg, "accept")
                        auto_action["mode"] = "auto_allowlist_keyboard"
                        auto_action["dialog_title"] = dlg.get("title")
                    else:
                        auto_action = {"skipped": True, "reason": "report mode would apply; OK not allowlisted"}
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
        "modal": {
            "present": modal_present,
            "blocking": modal_blocking,
            "count": len(dialogs_public),
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
        "advice",
        "agent_instruction",
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
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Poll until editor is running and MCP/RC answers, or timeout."""
    cfg = cfg or load_config()
    timeout_s = max(0.0, float(timeout_s))
    poll_s = max(0.2, float(poll_s))
    deadline = time.time() + timeout_s
    last: dict[str, Any] = {}
    while True:
        last = check_unreal(cfg)
        if last.get("status") == "ok" and last.get("unreal_running") and last.get(
            "editor_responsive"
        ):
            last["wait_result"] = "ready"
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

    # Immediate first write so agents have a file before the first interval.
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
    result["available_buttons"] = [b.get("text") for b in target.get("buttons") or []]
    return result
