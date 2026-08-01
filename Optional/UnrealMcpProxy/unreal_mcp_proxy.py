#!/usr/bin/env python3
"""Unreal MCP anti-thrash proxy (canonical, REAgentTools).

Two fronts (same sticky/coalesce/serialize logic):

1) **HTTP sidecar (preferred)** — Cursor `type: http` → this process on
   `:8001`, which forwards to Unreal `:8000`.
2) **Stdio** — `python unreal_mcp_proxy.py` for hosts that only do stdio.

Hardening goals:
- GET /mcp returns 405 (Streamable HTTP: SSE or 405 — never short JSON 200)
- GET /health returns service identity JSON
- Sticky upstream session on disk; timeouts do NOT open new UE sessions
- Owner/token-aware locks (never steal from a live process)
- Single-instance ensure-http: verify /health, never kill/rebind :8001
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import socket
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

PROXY_VERSION = "1.5.0"
SERVICE_NAME = "unreal-mcp-http-proxy"

def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return float(default)
    text = str(raw).strip().strip("\x00").strip()
    if not text:
        return float(default)
    try:
        return float(text)
    except ValueError:
        return float(default)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return int(default)
    text = str(raw).strip().strip("\x00").strip()
    if not text:
        return int(default)
    try:
        return int(float(text))
    except ValueError:
        return int(default)


MCP_URL = os.environ.get("UNREAL_MCP_URL", "http://127.0.0.1:8000/mcp")
_PARSED = urlparse(MCP_URL)
HOST = _PARSED.hostname or "127.0.0.1"
PORT = int(_PARSED.port or 8000)
PATH = _PARSED.path or "/mcp"

CONNECT_TIMEOUT = _env_float("UNREAL_MCP_CONNECT_TIMEOUT", 5.0)
REQUEST_TIMEOUT = _env_float("UNREAL_MCP_REQUEST_TIMEOUT", 180.0)
READY_WAIT_S = _env_float("UNREAL_MCP_READY_WAIT", 90.0)
PROTOCOL_VERSION = os.environ.get("UNREAL_MCP_PROTOCOL", "2025-11-25")
CACHE_TTL_S = _env_float("UNREAL_MCP_CACHE_TTL", 60.0)
LIST_CACHE_TTL_S = _env_float("UNREAL_MCP_LIST_CACHE_TTL", 180.0)
MIN_NEW_SESSION_GAP_S = _env_float("UNREAL_MCP_MIN_SESSION_GAP", 30.0)
LIST_TIMEOUT = _env_float("UNREAL_MCP_LIST_TIMEOUT", 25.0)
# Unreal answers a stale/unknown session with HTTP 200 + empty body on some paths
# (instead of 404). Treat repeated empties as a wedged session, not a busy editor.
EMPTY_REPLY_REINIT_AFTER = _env_int("UNREAL_MCP_EMPTY_REINIT_AFTER", 2)


class EmptyUpstreamReply(RuntimeError):
    """Upstream returned a non-error HTTP status with no JSON-RPC payload."""

    def __init__(self, status: int, session: str | None) -> None:
        self.status = status
        self.session = session
        super().__init__(
            f"Unreal MCP returned HTTP {status} with an empty body for session "
            f"{session or '<none>'} — session is wedged/unknown upstream."
        )

_STARTED_AT = time.time()
_INSTANCE_ID = uuid.uuid4().hex[:12]


def _default_state_dir() -> Path:
    env = os.environ.get("UNREAL_MCP_STATE_DIR")
    if env:
        return Path(env)
    # Prefer Unreal project Saved/ when this file lives under Plugins/REAgentTools/...
    here = Path(__file__).resolve()
    for parent in here.parents:
        if parent.name == "Plugins" and (parent.parent / "Saved").is_dir():
            return parent.parent / "Saved" / "REAgentTools" / "mcp_proxy"
        if parent.name == "REAgentTools" and parent.parent.name == "Plugins":
            project = parent.parent.parent
            return project / "Saved" / "REAgentTools" / "mcp_proxy"
    # Fallback: per-user dir keyed by upstream host:port
    safe = f"{HOST}_{PORT}".replace(":", "_")
    base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".cache")
    return base / "REAgentTools" / "mcp_proxy" / safe


_STATE_DIR = _default_state_dir()
_STATE_DIR.mkdir(parents=True, exist_ok=True)
_STICKY_PATH = _STATE_DIR / "sticky_session.json"
_INFLIGHT_DIR = _STATE_DIR / "inflight"
_INFLIGHT_DIR.mkdir(parents=True, exist_ok=True)
_CALL_LOCK_PATH = _STATE_DIR / "tools_call.lock"
_SESSION_LOCK_PATH = _STATE_DIR / "session.lock"
_SPAWN_LOCK_PATH = _STATE_DIR / "spawn.lock"
_PID_PATH = _STATE_DIR / "http_proxy.pid"

_state_lock = threading.Lock()
_session_id: str | None = None
_initialized_params: dict[str, Any] | None = None
_cached_init_result: dict[str, Any] | None = None
_req_counter = 100
_last_new_session_at = 0.0
_session_generation = 0
_last_upstream_error: str | None = None
_active_calls = 0
_consecutive_empty = 0
_last_wedge_reinit_at = 0.0
_diag = {
    "get_mcp": 0,
    "get_health": 0,
    "get_other": 0,
    "post": 0,
    "post_ok": 0,
    "post_err": 0,
    "cache_hits": 0,
    "coalesce_waits": 0,
    "session_reinits": 0,
    "empty_upstream": 0,
    "wedge_recoveries": 0,
    "degenerate_not_cached": 0,
}

_inflight: dict[str, dict[str, Any]] = {}
_cache: dict[str, tuple[float, Any]] = {}

_PROBE_WINDOW_S = _env_float("UNREAL_MCP_PROBE_WINDOW", 90.0)
_PROBE_WARN_AFTER = _env_int("UNREAL_MCP_PROBE_MAX", 4)
_probe_hits: list[tuple[float, str]] = []

_BP_BATCH_OK = frozenset(
    {
        "write_graph_dsl",
        "read_graph_dsl",
        "compile_blueprint",
        "add_function_graph",
        "get_graph_dsl_docs",
        "get_blueprint_info",
        "list_graphs",
    }
)

_BATCH_BANNER = (
    "!!! STOP - BATCH THIS WORK. DO NOT KEEP ONE-SHOTTING. !!!\n"
    "You just paid a FULL context tool round-trip for a SINGLE search/edit hop.\n"
    "HOW TO SEARCH (Epic find_* is one query each - wrap many queries):\n"
    "  ONE ProgrammaticToolset.execute_tool_script that loops your search terms\n"
    "  e.g. ['CastFirebolt','IA_Cast','ControlRotation','GetControlRotation']\n"
    "  and returns {term: results}. Misses are fine. ONE MCP hop.\n"
    "Do NOT fire find_nodes twice for near-duplicates. Prefer one broader term,\n"
    "or dump ALL candidate terms in that script.\n"
    "FOR EDITS: write_graph_dsl (whole graph) + compile once; Niagara: ONE script.\n"
    "--- real tool result follows (not blocked - use the data, then BATCH) ---\n"
)

_stderr = sys.stderr
_log_get_until = 0.0
_GET_LOG_EVERY_S = _env_float("UNREAL_MCP_GET_LOG_EVERY", 30.0)


def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%S")
    print(f"[unreal-mcp-proxy {ts} pid={os.getpid()}] {msg}", file=_stderr, flush=True)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_lock_meta(path: Path) -> dict[str, Any] | None:
    try:
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8").strip()
        parts = text.split()
        if len(parts) < 2:
            return None
        return {
            "pid": int(parts[0]),
            "token": parts[1],
            "started": float(parts[2]) if len(parts) > 2 else 0.0,
        }
    except Exception:
        return None


def _acquire_file_lock(path: Path, timeout: float) -> tuple[int, str]:
    """Exclusive create lock. Returns (fd, token). Raises TimeoutError.

    Only breaks a lock when the owning PID is dead. Never age-steals a live lock.
    """
    deadline = time.time() + timeout
    token = uuid.uuid4().hex
    while time.time() < deadline:
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
            os.write(fd, f"{os.getpid()} {token} {time.time():.3f}\n".encode("ascii"))
            return fd, token
        except FileExistsError:
            meta = _read_lock_meta(path)
            if meta and not _pid_alive(meta["pid"]):
                _log(f"breaking dead-owner lock {path.name} pid={meta['pid']}")
                try:
                    # Only unlink if still the same dead owner's token
                    cur = _read_lock_meta(path)
                    if cur and cur.get("token") == meta["token"]:
                        path.unlink(missing_ok=True)
                except OSError:
                    pass
                continue
            time.sleep(0.05)
    raise TimeoutError(f"lock timeout: {path.name}")


def _release_file_lock(fd: int, path: Path, token: str) -> None:
    try:
        os.close(fd)
    except OSError:
        pass
    meta = _read_lock_meta(path)
    if meta and meta.get("token") == token:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def _call_tool_parts(params: Any) -> tuple[str, str, str]:
    if not isinstance(params, dict):
        return "", "", ""
    name = str(params.get("name") or "").lower()
    args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
    if name != "call_tool":
        args = params if isinstance(params, dict) else {}
    toolset = str(
        args.get("toolset_name")
        or args.get("toolsetName")
        or args.get("toolset")
        or ""
    ).lower()
    tool = str(
        args.get("tool_name") or args.get("toolName") or args.get("tool") or ""
    ).lower()
    if toolset and tool:
        return f"{toolset}.{tool}", toolset, tool
    nested = str(args.get("name") or "").lower()
    return nested, toolset, tool


def _oneshot_batch_warning(params: Any) -> str | None:
    global _probe_hits
    fp, toolset, tool = _call_tool_parts(params)
    if not fp and not tool:
        return None

    is_bp = "blueprinttools" in toolset or "blueprinttools" in fp
    is_niagara = "niagara" in toolset or "niagara" in fp
    is_prog = "programmatictoolset" in toolset or "programmatictoolset" in fp
    is_re = "re_agent_tools" in toolset or toolset.startswith("re_")

    if is_re:
        return None
    if is_prog and tool in ("execute_tool_script", "execute_script"):
        return None
    if is_bp and tool in _BP_BATCH_OK:
        return None
    if not (is_bp or is_niagara):
        return None

    now = time.time()
    with _state_lock:
        _probe_hits = [(t, f) for t, f in _probe_hits if now - t <= _PROBE_WINDOW_S]
        _probe_hits.append((now, fp or tool))
        count = len(_probe_hits)

    _log(f"BATCH WARN (pass-through) #{count}: {fp or tool}")
    extra = ""
    if count >= _PROBE_WARN_AFTER:
        extra = (
            f"\n!!! THIS IS ONE-SHOT #{count} IN {_PROBE_WINDOW_S:.0f}s — "
            f"YOU ARE THRASHING. STOP PROBING. WRITE THE FULL DSL / SCRIPT NOW. !!!\n"
        )
    return _BATCH_BANNER + extra


def _inject_batch_warning(response: dict[str, Any], warning: str | None) -> dict[str, Any]:
    if not warning or not isinstance(response, dict):
        return response
    if "error" in response and "result" not in response:
        err = dict(response.get("error") or {})
        err["message"] = warning + "\n" + str(err.get("message") or "")
        return {**response, "error": err}
    result = response.get("result")
    banner_block = {"type": "text", "text": warning}
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        return {
            **response,
            "result": {**result, "content": [banner_block, *list(result["content"])]},
        }
    return {
        **response,
        "result": {
            "content": [
                banner_block,
                {"type": "text", "text": json.dumps(result, ensure_ascii=False)},
            ]
        },
    }


def _next_id() -> int:
    global _req_counter
    with _state_lock:
        _req_counter += 1
        return _req_counter


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(path)


def _load_sticky() -> dict[str, Any] | None:
    try:
        if not _STICKY_PATH.is_file():
            return None
        data = json.loads(_STICKY_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("session_id"):
            return data
    except Exception:
        return None
    return None


def _save_sticky(session_id: str, init_wrapped: dict[str, Any], params: dict[str, Any]) -> None:
    payload = {
        "session_id": session_id,
        "init_result": init_wrapped,
        "params": params,
        "updated_at": time.time(),
        "pid": os.getpid(),
        "proxy_version": PROXY_VERSION,
    }
    _atomic_write_json(_STICKY_PATH, payload)


def _clear_sticky() -> None:
    try:
        _STICKY_PATH.unlink(missing_ok=True)
    except OSError:
        pass


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
    sys.stdout.buffer.write(
        f"Content-Length: {len(data)}\r\n\r\n".encode("ascii") + data
    )
    sys.stdout.buffer.flush()


def _extract_sse_json(text: str) -> Any | None:
    parts = text.replace("\r\n", "\n").split("\n\n")
    for part in parts:
        data_lines: list[str] = []
        for line in part.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
        if not data_lines:
            continue
        payload_text = "\n".join(data_lines).strip()
        if not payload_text:
            continue
        try:
            obj = json.loads(payload_text)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and ("result" in obj or "error" in obj or "id" in obj):
            return obj
    return None


def _http_post(
    payload: dict[str, Any],
    session: str | None,
    *,
    allow_empty: bool = False,
    timeout: float | None = None,
) -> tuple[Any, str | None, int]:
    data = json.dumps(payload).encode("utf-8")
    lines = [
        f"POST {PATH} HTTP/1.1",
        f"Host: {HOST}:{PORT}",
        "Content-Type: application/json",
        "Accept: application/json, text/event-stream",
        f"Content-Length: {len(data)}",
        # Must be keep-alive: Unreal answers tools/call with a streaming SSE body
        # on the POST response and tears that stream down (empty body, no result)
        # if the client asked to close. We still close the socket ourselves once
        # the JSON-RPC result has been read.
        "Connection: keep-alive",
    ]
    if session:
        lines.append(f"Mcp-Session-Id: {session}")
    raw = ("\r\n".join(lines) + "\r\n\r\n").encode("ascii") + data

    req_timeout = float(timeout if timeout is not None else REQUEST_TIMEOUT)
    sock = socket.create_connection((HOST, PORT), timeout=CONNECT_TIMEOUT)
    sock.settimeout(min(30.0, req_timeout))
    try:
        sock.sendall(raw)
        buf = b""
        deadline = time.time() + req_timeout
        header_map: dict[str, str] = {}
        headers_done = False
        body = b""
        status = 0
        sid = session

        while time.time() < deadline:
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                if headers_done:
                    text = body.decode("utf-8", "replace")
                    obj = _extract_sse_json(text)
                    if obj is not None:
                        return obj, sid, status
                    if text.strip():
                        try:
                            return json.loads(text), sid, status
                        except json.JSONDecodeError:
                            pass
                continue
            if not chunk:
                break
            buf += chunk

            if not headers_done:
                if b"\r\n\r\n" not in buf:
                    continue
                head, body = buf.split(b"\r\n\r\n", 1)
                headers_done = True
                status_line = head.decode("utf-8", "replace").split("\r\n", 1)[0]
                try:
                    status = int(status_line.split()[1])
                except Exception:
                    status = 0
                for line in head.decode("utf-8", "replace").split("\r\n")[1:]:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        header_map[k.strip().lower()] = v.strip()
                sid = header_map.get("mcp-session-id") or session
            else:
                body = buf.split(b"\r\n\r\n", 1)[1]

            text = body.decode("utf-8", "replace")
            ctype = header_map.get("content-type", "")

            if "text/event-stream" in ctype or text.lstrip().startswith("event:") or "data:" in text:
                obj = _extract_sse_json(text)
                if obj is not None:
                    return obj, sid, status
                continue

            cl = header_map.get("content-length")
            if cl is not None:
                if int(cl) == 0 and status < 400:
                    # Terminal, unambiguous: server declared a zero-length body.
                    # Do not spin to the deadline waiting for bytes that never come.
                    if allow_empty:
                        return {}, sid, status
                    raise EmptyUpstreamReply(status, session)
                if len(body) < int(cl):
                    continue
            if text.strip():
                try:
                    return json.loads(text), sid, status
                except json.JSONDecodeError:
                    obj = _extract_sse_json(text)
                    if obj is not None:
                        return obj, sid, status

        text = body.decode("utf-8", "replace") if headers_done else ""
        obj = _extract_sse_json(text)
        if obj is not None:
            return obj, sid, status
        if text.strip():
            try:
                return json.loads(text), sid, status
            except json.JSONDecodeError:
                pass
        if allow_empty and headers_done and status and status < 400:
            return {}, sid, status
        if headers_done and status and status < 400:
            # Connection closed / stalled with no payload on a non-error status.
            raise EmptyUpstreamReply(status, session)
        raise TimeoutError(
            f"No JSON-RPC result from {MCP_URL} (status={status}, body={text[:300]!r}) "
            f"— editor may be blocked by a modal dialog or busy. "
            f"Do NOT re-initialize; wait or call unreal-watch.check_unreal. "
            f"Do NOT kill/rebind the :8001 proxy."
        )
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _port_open(host: str = HOST, port: int = PORT) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(CONNECT_TIMEOUT)
    try:
        return sock.connect_ex((host, port)) == 0
    finally:
        sock.close()


def _wait_for_server() -> bool:
    deadline = time.time() + READY_WAIT_S
    while time.time() < deadline:
        if _port_open():
            return True
        time.sleep(0.5)
    return False


def _is_session_error(status: int, result: Any) -> bool:
    if status in (400, 404):
        return True
    if not isinstance(result, dict):
        return False
    err = result.get("error")
    if not isinstance(err, dict):
        return False
    msg = str(err.get("message", "")).lower()
    return any(
        token in msg
        for token in (
            "session not found",
            "unknown session",
            "invalid session",
            "session expired",
            "no session",
            "reinitialize",
        )
    )


def _request_key(method: str, params: Any) -> str:
    blob = json.dumps({"m": method, "p": params}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def _cache_get(key: str) -> Any | None:
    with _state_lock:
        item = _cache.get(key)
        if not item:
            return None
        expires, value = item
        if time.time() > expires:
            _cache.pop(key, None)
            return None
        return copy.deepcopy(value)


def _cache_put(key: str, value: Any, ttl: float) -> None:
    with _state_lock:
        _cache[key] = (time.time() + ttl, copy.deepcopy(value))


def _hydrate_from_sticky() -> bool:
    global _session_id, _initialized_params, _cached_init_result
    sticky = _load_sticky()
    if not sticky:
        return False
    with _state_lock:
        _session_id = str(sticky["session_id"])
        _cached_init_result = sticky.get("init_result")
        _initialized_params = sticky.get("params")
    _log(f"loaded sticky session from disk: {_session_id}")
    return True


def _upstream_initialize(
    params: dict[str, Any] | None = None,
    *,
    force: bool = False,
) -> dict[str, Any]:
    global _session_id, _initialized_params, _cached_init_result, _last_new_session_at
    global _session_generation, _last_upstream_error

    if not force:
        sticky = _load_sticky()
        if sticky and sticky.get("session_id") and sticky.get("init_result"):
            with _state_lock:
                _session_id = str(sticky["session_id"])
                _cached_init_result = sticky.get("init_result")
                _initialized_params = sticky.get("params") or params
            _log(f"reuse sticky session (fast, no lock): {_session_id}")
            return copy.deepcopy(_cached_init_result)

        with _state_lock:
            if _session_id and _cached_init_result is not None:
                _log(f"reuse memory session: {_session_id}")
                return copy.deepcopy(_cached_init_result)

    lock_fd: int | None = None
    lock_token = ""
    try:
        lock_fd, lock_token = _acquire_file_lock(
            _SESSION_LOCK_PATH, timeout=min(30.0, REQUEST_TIMEOUT)
        )
    except TimeoutError:
        sticky = _load_sticky()
        if sticky and sticky.get("init_result"):
            with _state_lock:
                _session_id = str(sticky["session_id"])
                _cached_init_result = sticky.get("init_result")
            _log("session lock busy — returning sticky anyway")
            return copy.deepcopy(_cached_init_result)
        raise RuntimeError(
            "Could not create Unreal MCP session (lock busy). "
            "Another proxy call is in flight — wait; do NOT kill/rebind :8001. "
            f"State dir: {_STATE_DIR}"
        )

    try:
        if not force:
            sticky = _load_sticky()
            if sticky and sticky.get("session_id") and sticky.get("init_result"):
                with _state_lock:
                    _session_id = str(sticky["session_id"])
                    _cached_init_result = sticky.get("init_result")
                    _initialized_params = sticky.get("params") or params
                _log(f"reuse sticky session under lock: {_session_id}")
                return copy.deepcopy(_cached_init_result)

        now = time.time()
        if force:
            gap = now - _last_new_session_at
            sticky = _load_sticky()
            if gap < MIN_NEW_SESSION_GAP_S and sticky and sticky.get("init_result"):
                with _state_lock:
                    _session_id = str(sticky["session_id"])
                    _cached_init_result = sticky.get("init_result")
                _log(
                    f"suppress new session (gap {gap:.1f}s < {MIN_NEW_SESSION_GAP_S}s); "
                    f"reuse {_session_id}"
                )
                return copy.deepcopy(_cached_init_result)

        if not _wait_for_server():
            raise RuntimeError(
                f"Unreal MCP not reachable at {MCP_URL} within {READY_WAIT_S:.0f}s"
            )

        init_params = params or _initialized_params or {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "unreal-mcp-proxy", "version": PROXY_VERSION},
        }
        if "protocolVersion" not in init_params:
            init_params = {**init_params, "protocolVersion": PROTOCOL_VERSION}

        _log("creating NEW upstream session")
        result, sid, status = _http_post(
            {
                "jsonrpc": "2.0",
                "id": _next_id(),
                "method": "initialize",
                "params": init_params,
            },
            session=None,
            timeout=min(45.0, REQUEST_TIMEOUT),
        )
        if status >= 400 or (isinstance(result, dict) and "error" in result):
            raise RuntimeError(f"initialize failed ({status}): {result}")
        if not sid:
            raise RuntimeError(f"initialize returned no Mcp-Session-Id: {result}")

        try:
            _http_post(
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                session=sid,
                allow_empty=True,
                timeout=10.0,
            )
        except Exception as exc:  # noqa: BLE001
            _log(f"notifications/initialized warning: {exc}")

        wrapped = result if isinstance(result, dict) else {"result": result}
        with _state_lock:
            _session_id = sid
            _initialized_params = init_params
            _cached_init_result = copy.deepcopy(wrapped)
            _last_new_session_at = time.time()
            _session_generation += 1
        _save_sticky(sid, wrapped, init_params)
        _log(f"upstream session ready: {sid}")
        return copy.deepcopy(wrapped)
    except Exception as exc:
        with _state_lock:
            _last_upstream_error = str(exc)
        raise
    finally:
        if lock_fd is not None:
            _release_file_lock(lock_fd, _SESSION_LOCK_PATH, lock_token)


def _ensure_session() -> str:
    with _state_lock:
        sid = _session_id
    if sid:
        return sid
    if _hydrate_from_sticky():
        with _state_lock:
            if _session_id:
                return _session_id
    _upstream_initialize()
    with _state_lock:
        if not _session_id:
            raise RuntimeError("failed to establish upstream MCP session")
        return _session_id


def _client_result(msg_id: Any, upstream: Any) -> dict[str, Any]:
    if isinstance(upstream, dict) and ("result" in upstream or "error" in upstream):
        out: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id}
        if "error" in upstream:
            out["error"] = upstream["error"]
        else:
            out["result"] = upstream["result"]
        return out
    return {"jsonrpc": "2.0", "id": msg_id, "result": upstream}


def _ttl_for(method: str, params: Any) -> float:
    if method in ("tools/list", "resources/list", "prompts/list"):
        return LIST_CACHE_TTL_S
    if method == "tools/call":
        name = ""
        if isinstance(params, dict):
            name = str(params.get("name") or "")
        if name in ("list_toolsets", "describe_toolset", "list_tools"):
            return LIST_CACHE_TTL_S
        return CACHE_TTL_S
    return 0.0


def _upstream_timeout_for(method: str) -> float:
    if method in ("tools/list", "resources/list", "prompts/list", "initialize"):
        return LIST_TIMEOUT
    return REQUEST_TIMEOUT


def _reset_session_state() -> None:
    global _session_id, _cached_init_result
    _clear_sticky()
    with _state_lock:
        _session_id = None
        _cached_init_result = None


def _recover_and_retry(
    payload: dict[str, Any], reason: str
) -> tuple[Any, str | None, int]:
    """Drop the dead session, create exactly one new one, and retry the call once."""
    global _last_wedge_reinit_at

    with _state_lock:
        _diag["session_reinits"] += 1
    _log(f"{reason}: one controlled reinit + single retry")
    _reset_session_state()
    _upstream_initialize(force=True)
    sid = _ensure_session()
    with _state_lock:
        _last_wedge_reinit_at = time.time()
    return _http_post(
        payload, sid, timeout=_upstream_timeout_for(str(payload.get("method") or ""))
    )


def _forward_upstream(method: str, params: Any, msg_id: Any) -> dict[str, Any]:
    global _session_id, _active_calls, _last_upstream_error, _session_generation
    global _cached_init_result, _consecutive_empty

    sid = _ensure_session()
    payload: dict[str, Any] = {
        "jsonrpc": "2.0",
        "id": msg_id if msg_id is not None else _next_id(),
        "method": method,
        "params": params if params is not None else {},
    }

    call_fd: int | None = None
    call_token = ""
    batch_warn: str | None = None
    if method == "tools/call":
        batch_warn = _oneshot_batch_warning(params)
        try:
            call_fd, call_token = _acquire_file_lock(
                _CALL_LOCK_PATH, timeout=min(REQUEST_TIMEOUT + 30.0, 240.0)
            )
        except TimeoutError as exc:
            with _state_lock:
                _last_upstream_error = str(exc)
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32000,
                    "message": (
                        f"Unreal tools/call queue busy ({exc}). "
                        "Another call is in flight — wait; do not retry with a new session "
                        "and do NOT kill/rebind the :8001 proxy."
                    ),
                },
            }

    with _state_lock:
        _active_calls += 1
    try:
        try:
            result, new_sid, status = _http_post(
                payload, sid, timeout=_upstream_timeout_for(method)
            )
        except EmptyUpstreamReply as exc:
            # Unreal answers a stale/unknown session with 200 + empty body on some
            # paths instead of 404, so this is the only signal that the sticky
            # session is dead. Escalate after a couple in a row rather than pinning
            # a corpse forever (which reads to agents as "MCP returns empty").
            with _state_lock:
                _consecutive_empty += 1
                empties = _consecutive_empty
                _diag["empty_upstream"] += 1
                _last_upstream_error = str(exc)
                since_reinit = time.time() - _last_wedge_reinit_at
            _log(
                f"empty upstream reply #{empties} (status={exc.status}, session={sid})"
            )
            may_recover = (
                empties >= EMPTY_REPLY_REINIT_AFTER
                and since_reinit >= MIN_NEW_SESSION_GAP_S
            )
            if not may_recover:
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32000,
                        "message": (
                            f"Unreal MCP returned an empty reply (HTTP {exc.status}) for "
                            f"session {sid}. Resubmit this call once — the proxy "
                            f"re-establishes the session after "
                            f"{EMPTY_REPLY_REINIT_AFTER} consecutive empties. "
                            "Do NOT switch to RC yet and do NOT kill/rebind :8001."
                        ),
                    },
                }
            try:
                result, new_sid, status = _recover_and_retry(
                    payload, "wedged session (empty upstream replies)"
                )
            except EmptyUpstreamReply as exc2:
                with _state_lock:
                    _last_upstream_error = str(exc2)
                _log(f"wedge recovery still empty: {exc2}")
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32000,
                        "message": (
                            "Unreal MCP returns empty replies even on a freshly "
                            f"created session (HTTP {exc2.status}). The editor is "
                            "likely modal-blocked or mid-compile: call "
                            "unreal-watch.check_unreal once, then resubmit one "
                            "batched call. RC oneshot is the fallback after that. "
                            "Do NOT kill/rebind :8001."
                        ),
                    },
                }
            except Exception as exc2:  # noqa: BLE001
                with _state_lock:
                    _last_upstream_error = str(exc2)
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32000,
                        "message": f"wedged session recovery failed: {exc2}",
                    },
                }
            with _state_lock:
                _consecutive_empty = 0
                _diag["wedge_recoveries"] += 1
            _log("wedge recovery succeeded on fresh session")
        except TimeoutError as exc:
            with _state_lock:
                _last_upstream_error = str(exc)
            _log(f"upstream timeout (keeping sticky session {sid}): {exc}")
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32000, "message": str(exc)},
            }
        except OSError as exc:
            with _state_lock:
                _last_upstream_error = str(exc)
            _log(f"upstream transport error (keeping sticky unless session error): {exc}")
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {
                    "code": -32000,
                    "message": (
                        f"Unreal MCP transport error: {exc}. "
                        "If ports are open, editor is likely modal-blocked — "
                        "use unreal-watch.check_unreal; do not re-init MCP; "
                        "do NOT kill/rebind :8001."
                    ),
                },
            }
        else:
            with _state_lock:
                _consecutive_empty = 0

        if new_sid:
            with _state_lock:
                _session_id = new_sid
            sticky = _load_sticky() or {}
            if sticky.get("init_result"):
                _save_sticky(
                    new_sid,
                    sticky.get("init_result") or {"result": {}},
                    sticky.get("params") or {},
                )

        if _is_session_error(status, result):
            try:
                result, new_sid, status = _recover_and_retry(
                    payload, f"hard session error ({status})"
                )
                if new_sid:
                    with _state_lock:
                        _session_id = new_sid
            except Exception as exc:  # noqa: BLE001
                with _state_lock:
                    _last_upstream_error = str(exc)
                return {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32000, "message": f"session recovery failed: {exc}"},
                }

        return _inject_batch_warning(_client_result(msg_id, result), batch_warn)
    finally:
        with _state_lock:
            _active_calls = max(0, _active_calls - 1)
        if call_fd is not None:
            _release_file_lock(call_fd, _CALL_LOCK_PATH, call_token)


def _disk_coalesce_wait(key: str, msg_id: Any) -> dict[str, Any] | None:
    pending = _INFLIGHT_DIR / f"{key}.pending"
    result_path = _INFLIGHT_DIR / f"{key}.result"
    if pending.is_file():
        try:
            meta_text = pending.read_text(encoding="utf-8").strip()
            owner_pid = int(meta_text.split()[0]) if meta_text else 0
            if owner_pid and not _pid_alive(owner_pid):
                _log(f"clear dead inflight pending {key[:8]} pid={owner_pid}")
                pending.unlink(missing_ok=True)
        except Exception:
            pass
    if not pending.is_file() and not result_path.is_file():
        return None
    with _state_lock:
        _diag["coalesce_waits"] += 1
    _log(f"cross-process coalesce wait: {key[:8]}")
    deadline = time.time() + min(REQUEST_TIMEOUT + 30.0, 240.0)
    while time.time() < deadline:
        if result_path.is_file():
            try:
                data = json.loads(result_path.read_text(encoding="utf-8"))
                return _client_result(msg_id, data)
            except Exception:
                return None
        if not pending.is_file() and not result_path.is_file():
            return None
        # If owner died mid-flight, become leader
        if pending.is_file():
            try:
                meta_text = pending.read_text(encoding="utf-8").strip()
                owner_pid = int(meta_text.split()[0]) if meta_text else 0
                if owner_pid and not _pid_alive(owner_pid):
                    pending.unlink(missing_ok=True)
                    return None
            except Exception:
                pass
        time.sleep(0.1)
    try:
        pending.unlink(missing_ok=True)
    except OSError:
        pass
    return None


def _disk_coalesce_begin(key: str) -> bool:
    pending = _INFLIGHT_DIR / f"{key}.pending"
    try:
        fd = os.open(str(pending), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.write(fd, f"{os.getpid()} {time.time():.3f}\n".encode("ascii"))
        os.close(fd)
        return True
    except FileExistsError:
        return False


def _is_degenerate_result(method: str, result: Any) -> bool:
    """True for replies that must never be cached — they'd pin a wedge for the TTL."""
    if result is None:
        return True
    if isinstance(result, dict):
        if not result:
            return True
        if method == "tools/list" and not result.get("tools"):
            return True
    return False


def _disk_coalesce_finish(
    key: str, upstream_shape: dict[str, Any], *, retain_s: float = 15.0
) -> None:
    result_path = _INFLIGHT_DIR / f"{key}.result"
    pending = _INFLIGHT_DIR / f"{key}.pending"
    try:
        _atomic_write_json(result_path, upstream_shape)
    except Exception:
        pass
    try:
        pending.unlink(missing_ok=True)
    except OSError:
        pass

    def _cleanup() -> None:
        time.sleep(retain_s)
        try:
            result_path.unlink(missing_ok=True)
        except OSError:
            pass

    threading.Thread(target=_cleanup, daemon=True).start()


def _forward(msg: dict[str, Any]) -> dict[str, Any] | None:
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params", {})
    is_notification = "id" not in msg

    if method == "initialize":
        try:
            upstream = _upstream_initialize(params if isinstance(params, dict) else {})
        except Exception as exc:  # noqa: BLE001
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32000, "message": str(exc)},
            }
        result = upstream.get("result", upstream)
        if isinstance(result, dict):
            info = dict(result.get("serverInfo") or {})
            info["name"] = info.get("name") or "unreal-mcp"
            info["title"] = info.get("title") or "Unreal MCP (anti-thrash proxy)"
            info["version"] = info.get("version") or PROXY_VERSION
            result = {**result, "serverInfo": info}
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    if method == "notifications/initialized":
        return None

    if is_notification and method in (
        "notifications/roots/list_changed",
        "notifications/cancelled",
    ):
        _log(f"ignore notification: {method}")
        return None

    if is_notification:
        try:
            sid = _ensure_session()
            _http_post(
                {
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params if params is not None else {},
                },
                sid,
                allow_empty=True,
                timeout=15.0,
            )
        except Exception as exc:  # noqa: BLE001
            _log(f"notification drop: {method}: {exc}")
        return None

    key = _request_key(str(method), params)
    ttl = _ttl_for(str(method), params)

    cached = _cache_get(key) if ttl > 0 else None
    if cached is not None:
        with _state_lock:
            _diag["cache_hits"] += 1
        _log(f"cache hit: {method}")
        return _client_result(msg_id, cached)

    waited = _disk_coalesce_wait(key, msg_id)
    if waited is not None:
        return waited

    leader_disk = _disk_coalesce_begin(key)
    if not leader_disk:
        waited2 = _disk_coalesce_wait(key, msg_id)
        if waited2 is not None:
            return waited2
        leader_disk = _disk_coalesce_begin(key)

    leader = False
    slot: dict[str, Any]
    with _state_lock:
        existing = _inflight.get(key)
        if existing is None:
            slot = {"event": threading.Event(), "result": None, "error": None}
            _inflight[key] = slot
            leader = True
        else:
            slot = existing

    if not leader:
        _log(f"in-process coalesce wait: {method}")
        slot["event"].wait(timeout=REQUEST_TIMEOUT + 30)
        if slot["error"]:
            return {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32000, "message": str(slot["error"])},
            }
        if slot["result"] is not None:
            return _client_result(msg_id, slot["result"])
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32000, "message": "coalesced request failed"},
        }

    try:
        response = _forward_upstream(str(method), params, msg_id)
        if "error" in response:
            upstream_shape: dict[str, Any] = {"error": response["error"]}
        else:
            upstream_shape = {"result": response.get("result")}
        degenerate = "error" in upstream_shape or _is_degenerate_result(
            str(method), upstream_shape.get("result")
        )
        slot["result"] = upstream_shape
        if ttl > 0 and not degenerate:
            _cache_put(key, upstream_shape, ttl)
        elif ttl > 0:
            with _state_lock:
                _diag["degenerate_not_cached"] += 1
            _log(f"not caching degenerate/error reply for {method}")
        if leader_disk:
            # Short retention for failures so an immediate resubmit re-hits Unreal
            # instead of replaying the wedge to every late waiter.
            _disk_coalesce_finish(
                key, upstream_shape, retain_s=1.0 if degenerate else 15.0
            )
        return response
    except Exception as exc:  # noqa: BLE001
        slot["error"] = str(exc)
        err_shape = {"error": {"code": -32000, "message": str(exc)}}
        if leader_disk:
            _disk_coalesce_finish(key, err_shape, retain_s=1.0)
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": -32000, "message": str(exc)},
        }
    finally:
        slot["event"].set()
        with _state_lock:
            if _inflight.get(key) is slot:
                _inflight.pop(key, None)
        if leader_disk:
            pending = _INFLIGHT_DIR / f"{key}.pending"
            try:
                pending.unlink(missing_ok=True)
            except OSError:
                pass


def _stdio_main() -> int:
    _hydrate_from_sticky()
    _log(
        f"stdio -> {MCP_URL} (sticky disk+memory, coalesce, serialize tools/call, "
        f"state={_STATE_DIR})"
    )
    while True:
        try:
            msg = _read_message()
        except Exception as exc:  # noqa: BLE001
            _log(f"read failed: {exc}")
            return 1
        if msg is None:
            _log("stdin closed")
            return 0
        try:
            response = _forward(msg)
        except Exception as exc:  # noqa: BLE001
            _log(f"forward failed: {exc}")
            if "id" in msg:
                response = {
                    "jsonrpc": "2.0",
                    "id": msg.get("id"),
                    "error": {"code": -32603, "message": str(exc)},
                }
            else:
                response = None
        if response is not None:
            _write_message(response)
    return 0


def _http_port_open(host: str, port: int) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(0.4)
    try:
        return sock.connect_ex((host, port)) == 0
    except OSError:
        return False
    finally:
        sock.close()


def _http_get_json(host: str, port: int, path: str, timeout: float = 1.0) -> tuple[int, Any]:
    raw = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Accept: application/json\r\n"
        "Connection: close\r\n\r\n"
    ).encode("ascii")
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        return 0, {"error": f"{type(exc).__name__}: {exc}"}
    sock.settimeout(timeout)
    try:
        sock.sendall(raw)
        buf = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
            if b"\r\n\r\n" in buf:
                head, body = buf.split(b"\r\n\r\n", 1)
                cl = None
                for line in head.decode("utf-8", "replace").split("\r\n")[1:]:
                    if line.lower().startswith("content-length:"):
                        cl = int(line.split(":", 1)[1].strip())
                if cl is not None and len(body) >= cl:
                    break
        if b"\r\n\r\n" not in buf:
            return 0, None
        head, body = buf.split(b"\r\n\r\n", 1)
        status_line = head.decode("utf-8", "replace").split("\r\n", 1)[0]
        try:
            status = int(status_line.split()[1])
        except Exception:
            status = 0
        try:
            return status, json.loads(body.decode("utf-8"))
        except Exception:
            return status, body.decode("utf-8", "replace")
    except OSError as exc:
        return 0, {"error": f"{type(exc).__name__}: {exc}"}
    finally:
        try:
            sock.close()
        except Exception:
            pass


def _version_tuple(text: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in str(text).split("."):
        digits = "".join(c for c in chunk if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)


def probe_proxy_health(host: str = "127.0.0.1", port: int = 8001) -> dict[str, Any]:
    """Public helper for UnrealWatch / agents: identify listener without mutating it.

    A healthy modern proxy MUST:
    - answer GET /health with service + version
    - answer GET /mcp with 405 (Streamable HTTP: no SSE offered)
    Legacy proxies that returned JSON 200 on GET /mcp are treated as stale
    (they cause Cursor reconnect storms) — report ok=False but still advise
    against blind kill unless performing an explicit controlled restart.
    """
    if not _http_port_open(host, port):
        return {"ok": False, "listening": False, "url": f"http://{host}:{port}/health"}
    status, body = _http_get_json(host, port, "/health")
    mcp_status = 0
    for _ in range(3):
        mcp_status, _ = _http_get_json(host, port, "/mcp")
        if mcp_status in (200, 405):
            break
        time.sleep(0.15)
    has_service = (
        status == 200
        and isinstance(body, dict)
        and body.get("service") == SERVICE_NAME
        and body.get("ok") is True
    )
    has_version = isinstance(body, dict) and bool(body.get("version"))
    running_version = str(body.get("version") or "") if isinstance(body, dict) else ""
    outdated = bool(
        has_version and _version_tuple(running_version) < _version_tuple(PROXY_VERSION)
    )
    # Legacy bug: short JSON 200 on GET /mcp. Transient connect failures (status 0)
    # during TIME_WAIT storms must not be misclassified as legacy.
    stale_legacy = bool(has_service and mcp_status == 200)
    protocol_ok = mcp_status == 405 or (has_service and has_version and mcp_status == 0)
    identity_ok = bool(has_service and has_version and protocol_ok and not stale_legacy)
    if identity_ok and outdated:
        advice = (
            f"Proxy healthy but running {running_version} (canonical {PROXY_VERSION}). "
            "Keep using it — do NOT kill mid-session. Upgrade with one explicit "
            "`--restart` when the editor is idle."
        )
    elif identity_ok:
        advice = "Proxy healthy — reuse; never kill/rebind."
    elif stale_legacy:
        advice = (
            "Stale legacy proxy on :8001 (GET /mcp returned 200 instead of 405). "
            "This causes Cursor reconnect storms. Perform ONE controlled restart via "
            "Scripts/start_unreal_mcp_http_proxy.cmd after stopping the old PID — "
            "do not thrash kill/respawn in a loop."
        )
    else:
        advice = (
            "Port open but not our proxy identity. Do NOT kill the process; "
            "inspect the owner and leave :8001 alone unless you know it is stale."
        )
    return {
        "ok": identity_ok,
        "listening": True,
        "stale_legacy": stale_legacy,
        "outdated": outdated,
        "running_version": running_version,
        "canonical_version": PROXY_VERSION,
        "health_status": status,
        "health": body if isinstance(body, dict) else {"raw": body},
        "mcp_get_status": mcp_status,
        "mcp_get_expected": 405,
        "url": f"http://{host}:{port}/mcp",
        "health_url": f"http://{host}:{port}/health",
        "advice": advice,
    }


def diagnostics_snapshot() -> dict[str, Any]:
    with _state_lock:
        return {
            "ok": True,
            "service": SERVICE_NAME,
            "version": PROXY_VERSION,
            "instance_id": _INSTANCE_ID,
            "pid": os.getpid(),
            "uptime_s": round(time.time() - _STARTED_AT, 1),
            "upstream": MCP_URL,
            "state_dir": str(_STATE_DIR),
            "session_id": _session_id,
            "session_generation": _session_generation,
            "active_calls": _active_calls,
            "consecutive_empty": _consecutive_empty,
            "empty_reinit_after": EMPTY_REPLY_REINIT_AFTER,
            "last_wedge_reinit_age_s": (
                round(time.time() - _last_wedge_reinit_at, 1)
                if _last_wedge_reinit_at
                else None
            ),
            "last_upstream_error": _last_upstream_error,
            "counters": dict(_diag),
        }


def _write_pid_file(bind: str, port: int) -> None:
    _atomic_write_json(
        _PID_PATH,
        {
            "pid": os.getpid(),
            "bind": bind,
            "port": port,
            "version": PROXY_VERSION,
            "instance_id": _INSTANCE_ID,
            "started_at": _STARTED_AT,
            "service": SERVICE_NAME,
        },
    )


def _run_http_server(bind: str, port: int) -> int:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class QuietThreadingHTTPServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

        def handle_error(self, request: Any, client_address: Any) -> None:  # noqa: ANN401
            exc = sys.exc_info()[1]
            if isinstance(exc, (ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
                return
            if isinstance(exc, OSError) and getattr(exc, "winerror", None) in (10054, 10053):
                return
            super().handle_error(request, client_address)

    _hydrate_from_sticky()
    _write_pid_file(bind, port)
    _log(
        f"HTTP listen http://{bind}:{port}/mcp -> {MCP_URL} "
        f"(v{PROXY_VERSION}, sticky/coalesce/serialize, state={_STATE_DIR})"
    )

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args: Any) -> None:
            # Suppress GET /mcp spam; keep POST / errors.
            try:
                line = fmt % args
            except Exception:
                line = fmt
            if "GET /mcp" in line or 'GET /mcp' in str(args):
                global _log_get_until
                now = time.time()
                with _state_lock:
                    n = _diag["get_mcp"]
                if now >= _log_get_until:
                    _log(f"http GET /mcp suppressed (count={n})")
                    _log_get_until = now + _GET_LOG_EVERY_S
                return
            _log("http " + line)

        def _conn_value(self) -> str:
            """Honor the client's connection disposition.

            Always answering "keep-alive" resets BaseHTTPRequestHandler's
            close_connection flag, so a client that sent "Connection: close"
            and reads until EOF blocks until its own timeout — which looks
            exactly like a mid-session MCP drop.
            """
            requested = (self.headers.get("Connection") or "").lower()
            if "close" in requested:
                return "close"
            if self.request_version == "HTTP/1.0" and "keep-alive" not in requested:
                return "close"
            return "keep-alive"

        def _cors(self) -> None:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
            self.send_header("Access-Control-Expose-Headers", "Mcp-Session-Id")

        def _send_bytes(
            self,
            status: int,
            body: bytes,
            *,
            content_type: str = "application/json",
            extra: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self._cors()
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", self._conn_value())
            if extra:
                for k, v in extra.items():
                    self.send_header(k, v)
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self.send_response(204)
            self._cors()
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path in ("/health", "/"):
                with _state_lock:
                    _diag["get_health"] += 1
                body = json.dumps(diagnostics_snapshot(), separators=(",", ":")).encode("utf-8")
                self._send_bytes(200, body)
                return
            if path == "/mcp":
                # Streamable HTTP: SSE stream OR 405. Short JSON 200 caused Cursor
                # reconnect storms (~63k GETs) and TimeWait exhaustion.
                with _state_lock:
                    _diag["get_mcp"] += 1
                body = b'{"error":"SSE stream not offered; use POST /mcp","service":"' + SERVICE_NAME.encode() + b'"}\n'
                self._send_bytes(405, body, extra={"Allow": "POST"})
                return
            with _state_lock:
                _diag["get_other"] += 1
            self._send_bytes(404, b'{"error":"not found"}\n')

        def do_DELETE(self) -> None:  # noqa: N802
            _log("client DELETE session (keeping sticky upstream)")
            self.send_response(200)
            self._cors()
            self.send_header("Content-Length", "0")
            self.send_header("Connection", self._conn_value())
            self.end_headers()

        def do_POST(self) -> None:  # noqa: N802
            path = self.path.split("?", 1)[0]
            if path not in ("/mcp", "/"):
                self._send_bytes(404, b'{"error":"not found"}\n')
                return
            with _state_lock:
                _diag["post"] += 1
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                length = 0
            raw = self.rfile.read(length) if length > 0 else b""
            if not raw:
                sid = _session_id or ((_load_sticky() or {}).get("session_id"))
                extra = {"Mcp-Session-Id": str(sid)} if sid else None
                self.send_response(202)
                self._cors()
                if extra:
                    for k, v in extra.items():
                        self.send_header(k, v)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", self._conn_value())
                self.end_headers()
                return
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                err = json.dumps(
                    {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}}
                ).encode("utf-8")
                self._send_bytes(400, err)
                return

            messages = payload if isinstance(payload, list) else [payload]
            responses: list[dict[str, Any]] = []
            for msg in messages:
                if not isinstance(msg, dict):
                    continue
                try:
                    resp = _forward(msg)
                except Exception as exc:  # noqa: BLE001
                    _log(f"HTTP forward failed: {exc}")
                    if "id" in msg:
                        resp = {
                            "jsonrpc": "2.0",
                            "id": msg.get("id"),
                            "error": {"code": -32603, "message": str(exc)},
                        }
                    else:
                        resp = None
                if resp is not None:
                    responses.append(resp)

            sid = _session_id
            if not sid:
                sticky = _load_sticky()
                if sticky and sticky.get("session_id"):
                    sid = str(sticky["session_id"])

            if not responses:
                self.send_response(202)
                self._cors()
                if sid:
                    self.send_header("Mcp-Session-Id", sid)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", self._conn_value())
                self.end_headers()
                return

            out_obj: Any = responses if isinstance(payload, list) else responses[0]
            if isinstance(out_obj, dict) and "error" in out_obj:
                with _state_lock:
                    _diag["post_err"] += 1
            else:
                with _state_lock:
                    _diag["post_ok"] += 1
            body = json.dumps(out_obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
            extra = {"Mcp-Session-Id": sid} if sid else None
            self._send_bytes(200, body, content_type="application/json;charset=utf-8", extra=extra)

    try:
        server = QuietThreadingHTTPServer((bind, port), Handler)
    except OSError as exc:
        # Expected race: another ensure-http won the bind. Reuse if identity matches.
        win = getattr(exc, "winerror", None)
        if win == 10048 or getattr(exc, "errno", None) in (98, 48, 10048):
            health = probe_proxy_health(bind, port)
            if health.get("ok"):
                _log(
                    f"bind race on {bind}:{port} (address in use) — expected proxy already up; "
                    "treating as success. Do NOT kill/rebind."
                )
                return 0
            _log(
                f"port {bind}:{port} in use by unknown owner: {exc}. "
                "Do NOT kill it; inspect the process and leave the listener alone."
            )
            return 2
        raise

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        _log("HTTP proxy stopped")
    finally:
        server.server_close()
        try:
            if _PID_PATH.is_file():
                data = json.loads(_PID_PATH.read_text(encoding="utf-8"))
                if data.get("pid") == os.getpid():
                    _PID_PATH.unlink(missing_ok=True)
        except Exception:
            pass
    return 0


def ensure_http_proxy(bind: str = "127.0.0.1", port: int = 8001) -> dict[str, Any]:
    """Ensure one healthy proxy on bind:port. Never kills/rebinds an existing listener."""
    health = probe_proxy_health(bind, port)
    if health.get("ok"):
        _log(f"HTTP proxy already up on {bind}:{port}")
        return {"ok": True, "already_up": True, "url": f"http://{bind}:{port}/mcp", **health}
    if health.get("listening") and not health.get("ok"):
        _log(
            f"port {bind}:{port} listening but not {SERVICE_NAME} — refusing to kill/rebind"
        )
        return {
            "ok": False,
            "already_up": False,
            "error": "port_owned_by_unknown",
            "advice": health.get("advice"),
            **health,
        }

    lock_fd: int | None = None
    lock_token = ""
    try:
        lock_fd, lock_token = _acquire_file_lock(_SPAWN_LOCK_PATH, timeout=10.0)
    except TimeoutError:
        # Someone else is spawning — wait for health
        for _ in range(50):
            health = probe_proxy_health(bind, port)
            if health.get("ok"):
                return {
                    "ok": True,
                    "already_up": True,
                    "spawned_by_peer": True,
                    "url": f"http://{bind}:{port}/mcp",
                    **health,
                }
            time.sleep(0.1)
        return {"ok": False, "error": "spawn_lock_timeout", "url": f"http://{bind}:{port}/mcp"}

    try:
        # Re-check under lock
        health = probe_proxy_health(bind, port)
        if health.get("ok"):
            return {"ok": True, "already_up": True, "url": f"http://{bind}:{port}/mcp", **health}
        if health.get("listening"):
            return {
                "ok": False,
                "error": "port_owned_by_unknown",
                "advice": health.get("advice"),
                **health,
            }

        py = sys.executable
        script = str(Path(__file__).resolve())
        creation = 0
        if sys.platform == "win32":
            creation = getattr(os, "DETACHED_PROCESS", 0x00000008) | getattr(
                os, "CREATE_NEW_PROCESS_GROUP", 0x00000200
            )
        try:
            import subprocess

            log_path = _STATE_DIR / "http_proxy.log"
            log_f = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
            subprocess.Popen(
                [py, script, "--http", f"{bind}:{port}"],
                stdin=subprocess.DEVNULL,
                stdout=log_f,
                stderr=subprocess.STDOUT,
                cwd=str(Path(__file__).resolve().parent),
                env=os.environ.copy(),
                creationflags=creation,
                close_fds=True,
            )
        except Exception as exc:  # noqa: BLE001
            _log(f"failed to spawn HTTP proxy: {exc}")
            return {"ok": False, "error": str(exc)}

        for _ in range(50):
            health = probe_proxy_health(bind, port)
            if health.get("ok"):
                _log(f"spawned HTTP proxy on {bind}:{port}")
                return {
                    "ok": True,
                    "spawned": True,
                    "url": f"http://{bind}:{port}/mcp",
                    **health,
                }
            time.sleep(0.1)
        _log(f"HTTP proxy did not bind {bind}:{port} in time")
        return {"ok": False, "error": "proxy did not bind in time", "url": f"http://{bind}:{port}/mcp"}
    finally:
        if lock_fd is not None:
            _release_file_lock(lock_fd, _SPAWN_LOCK_PATH, lock_token)


def _terminate_pid(pid: int, *, wait_s: float = 8.0) -> bool:
    import signal
    import subprocess

    try:
        os.kill(pid, getattr(signal, "SIGTERM", signal.SIGINT))
    except Exception:  # noqa: BLE001
        pass
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
                timeout=10,
            )
        except Exception:  # noqa: BLE001
            pass
    deadline = time.time() + wait_s
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    return not _pid_alive(pid)


def restart_proxy(
    host: str = "127.0.0.1", port: int = 8001, *, force: bool = False
) -> dict[str, Any]:
    """Explicit operator-initiated upgrade of the listener on host:port.

    This is the ONLY sanctioned way to replace a running proxy. It is a
    deployment action, never a mid-session recovery step: agents that hit a
    timeout or WinError 10048 must reuse the existing proxy instead.
    """
    health = probe_proxy_health(host, port)
    if health.get("listening"):
        body = health.get("health") if isinstance(health.get("health"), dict) else {}
        pid = body.get("pid")
        if not health.get("ok") and not health.get("stale_legacy") and not force:
            return {
                "ok": False,
                "error": "port_owned_by_unknown",
                "advice": (
                    f"{host}:{port} is open but does not identify as {SERVICE_NAME}. "
                    "Inspect the owner manually; pass force only if you are certain."
                ),
                **health,
            }
        active = body.get("active_calls") or 0
        if active and not force:
            return {
                "ok": False,
                "error": "calls_in_flight",
                "active_calls": active,
                "advice": (
                    "Upstream calls are in flight — wait for the editor to go idle, "
                    "then restart. Use force only to break a confirmed hang."
                ),
                **health,
            }
        if isinstance(pid, int) and pid > 0 and pid != os.getpid():
            _log(f"controlled restart: stopping proxy pid={pid}")
            if not _terminate_pid(pid):
                return {
                    "ok": False,
                    "error": "stop_failed",
                    "pid": pid,
                    "advice": "Could not stop the old proxy; stop it manually.",
                }
        for _ in range(100):
            if not _http_port_open(host, port):
                break
            time.sleep(0.1)
    # A restart is the right moment to drop a possibly wedged sticky session so
    # the fresh instance negotiates a new one instead of hydrating a corpse.
    _clear_sticky()
    result = ensure_http_proxy(host, port)
    result["restarted"] = True
    return result


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("--http", "http"):
        target = args[1] if len(args) > 1 else os.environ.get("UNREAL_MCP_HTTP_BIND", "127.0.0.1:8001")
        if ":" in target:
            host, _, port_s = target.rpartition(":")
            host = host or "127.0.0.1"
            port = int(port_s)
        else:
            host, port = "127.0.0.1", int(target)
        return _run_http_server(host, port)
    if args and args[0] in ("--ensure-http", "ensure-http"):
        target = args[1] if len(args) > 1 else os.environ.get("UNREAL_MCP_HTTP_BIND", "127.0.0.1:8001")
        if ":" in target:
            host, _, port_s = target.rpartition(":")
            host = host or "127.0.0.1"
            port = int(port_s)
        else:
            host, port = "127.0.0.1", int(target)
        result = ensure_http_proxy(host, port)
        print(json.dumps(result))
        return 0 if result.get("ok") else 1
    if args and args[0] in ("--restart", "restart"):
        positional = [a for a in args[1:] if not a.startswith("--")]
        target = positional[0] if positional else os.environ.get("UNREAL_MCP_HTTP_BIND", "127.0.0.1:8001")
        if ":" in target:
            host, _, port_s = target.rpartition(":")
            host = host or "127.0.0.1"
            port = int(port_s)
        else:
            host, port = "127.0.0.1", int(target)
        result = restart_proxy(host, port, force="--force" in args)
        print(json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1
    if args and args[0] in ("--health", "health"):
        target = args[1] if len(args) > 1 else os.environ.get("UNREAL_MCP_HTTP_BIND", "127.0.0.1:8001")
        if ":" in target:
            host, _, port_s = target.rpartition(":")
            host = host or "127.0.0.1"
            port = int(port_s)
        else:
            host, port = "127.0.0.1", int(target)
        print(json.dumps(probe_proxy_health(host, port), indent=2))
        return 0
    return _stdio_main()


if __name__ == "__main__":
    raise SystemExit(main())
