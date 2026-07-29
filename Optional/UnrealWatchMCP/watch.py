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
OpenProcess = kernel32.OpenProcess
OpenProcess.restype = wintypes.HANDLE
QueryFullProcessImageNameW = kernel32.QueryFullProcessImageNameW
CloseHandle = kernel32.CloseHandle

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
            title = _window_text(hwnd)
            prev = found.get(pid.value, {})
            # Prefer main editor title
            if (not prev.get("main_title")) or ("unreal editor" in title.lower()):
                found[pid.value] = {
                    "pid": int(pid.value),
                    "process": base,
                    "main_title": title or prev.get("main_title", ""),
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

    advice: list[str] = []
    if not processes:
        advice.append("UnrealEditor process not found — start the editor.")
    elif modal_present:
        titles = [d.get("title") for d in dialogs_public]
        advice.append(
            f"Owned Slate/Win32 dialog(s) detected: {titles}. "
            "Do NOT spam Unreal MCP. Call dismiss_dialog(accept|cancel) or ask the user."
        )
    elif ports_up_no_reply:
        advice.append(
            "MCP/RC ports listen but probes timed out — editor thread likely busy or nested UI."
        )
    elif mcp.get("listening") or rc.get("listening"):
        advice.append("Editor appears responsive on probe — Unreal MCP should work.")
    else:
        advice.append("Editor running but MCP/RC not listening.")

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
            "STOP Unreal MCP retries. Modal/owned UnrealWindow detected — "
            "dismiss_dialog(accept|cancel) or ask user."
            if likely_blocked
            else "Editor watch clear — Unreal MCP may be used."
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
