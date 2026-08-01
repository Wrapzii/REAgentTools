#!/usr/bin/env python3
"""Stdlib tests for UnrealMcpProxy — fake upstream, ephemeral ports.

Does not touch live Unreal :8000 or production proxy :8001.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
PROXY_DIR = HERE.parent
PROXY_SCRIPT = PROXY_DIR / "unreal_mcp_proxy.py"
sys.path.insert(0, str(PROXY_DIR))

import unreal_mcp_proxy as proxy  # noqa: E402


def _free_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _http_json(method: str, url: str, body: bytes | None = None, timeout: float = 5.0) -> tuple[int, dict[str, str], Any]:
    from urllib.error import HTTPError

    req = Request(url, data=body, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json, text/event-stream")
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310
            raw = resp.read()
            headers = {k.lower(): v for k, v in resp.headers.items()}
            status = resp.status
    except HTTPError as exc:
        raw = exc.read()
        headers = {k.lower(): v for k, v in exc.headers.items()} if exc.headers else {}
        status = int(exc.code)
    try:
        data = json.loads(raw.decode("utf-8")) if raw else None
    except json.JSONDecodeError:
        data = raw.decode("utf-8", "replace")
    return status, headers, data


class FakeUnrealMCP(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, addr: tuple[str, int]) -> None:
        self.sessions: dict[str, dict[str, Any]] = {}
        self.calls: list[dict[str, Any]] = []
        self.call_delay_s = 0.0
        self.list_delay_s = 0.0
        self.fail_unknown_session = True
        # Real Unreal answers a stale/unknown session with 200 + empty body on some
        # paths rather than 404; these flags reproduce that.
        self.empty_on_unknown_session = False
        self.force_empty = False
        self.empty_tools = False
        # Unreal answers tools/call with an SSE stream on the POST response and
        # aborts it when the client sent "Connection: close".
        self.sse_tools_call = False
        self.close_requests: list[str] = []
        super().__init__(addr, self._handler())

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt: str, *args: Any) -> None:
                return

            def do_GET(self) -> None:  # noqa: N802
                self.send_response(405)
                self.send_header("Allow", "POST")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length") or "0")
                raw = self.rfile.read(n) if n else b""
                try:
                    msg = json.loads(raw.decode("utf-8")) if raw else {}
                except json.JSONDecodeError:
                    self._json(400, {"error": "bad json"})
                    return
                sid = self.headers.get("Mcp-Session-Id")
                method = msg.get("method")
                msg_id = msg.get("id")
                server.calls.append({"method": method, "sid": sid, "params": msg.get("params")})

                if method == "initialize":
                    new_sid = uuid.uuid4().hex
                    server.sessions[new_sid] = {"created": time.time()}
                    body = {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {
                            "protocolVersion": "2025-11-25",
                            "capabilities": {"tools": {}},
                            "serverInfo": {"name": "fake-unreal", "version": "0"},
                        },
                    }
                    self._json(200, body, session=new_sid)
                    return

                if method == "notifications/initialized":
                    self.send_response(202)
                    if sid:
                        self.send_header("Mcp-Session-Id", sid)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                if sid and sid not in server.sessions:
                    if server.empty_on_unknown_session:
                        self._empty(200, session=sid)
                        return
                    if server.fail_unknown_session:
                        self._json(
                            404,
                            {
                                "jsonrpc": "2.0",
                                "id": msg_id,
                                "error": {
                                    "code": -32000,
                                    "message": f"Unknown session id '{sid}'",
                                },
                            },
                        )
                        return

                if server.force_empty:
                    self._empty(200, session=sid)
                    return

                if method == "tools/list":
                    if server.list_delay_s:
                        time.sleep(server.list_delay_s)
                    tools = (
                        []
                        if server.empty_tools
                        else [
                            {"name": "call_tool", "description": "x", "inputSchema": {"type": "object"}}
                        ]
                    )
                    self._json(
                        200,
                        {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}},
                        session=sid,
                    )
                    return

                if method == "tools/call":
                    if server.call_delay_s:
                        time.sleep(server.call_delay_s)
                    if server.sse_tools_call:
                        self._sse_or_abort(
                            {
                                "jsonrpc": "2.0",
                                "id": msg_id,
                                "result": {
                                    "content": [{"type": "text", "text": "sse-ok"}]
                                },
                            },
                            session=sid,
                        )
                        return
                    self._json(
                        200,
                        {
                            "jsonrpc": "2.0",
                            "id": msg_id,
                            "result": {
                                "content": [
                                    {
                                        "type": "text",
                                        "text": json.dumps({"ok": True, "params": msg.get("params")}),
                                    }
                                ]
                            },
                        },
                        session=sid,
                    )
                    return

                if method and method.startswith("notifications/"):
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return

                self._json(
                    200,
                    {
                        "jsonrpc": "2.0",
                        "id": msg_id,
                        "result": {"ok": True, "method": method},
                    },
                    session=sid,
                )

            def _sse_or_abort(self, obj: dict[str, Any], session: str | None = None) -> None:
                requested = (self.headers.get("Connection") or "").lower()
                server.close_requests.append(requested)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream;charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                if session:
                    self.send_header("Mcp-Session-Id", session)
                self.end_headers()
                if "close" in requested:
                    # Reproduce Unreal: stream is torn down, no result delivered.
                    self.close_connection = True
                    return
                self.wfile.write(
                    b"event: message\ndata: " + json.dumps(obj).encode("utf-8") + b"\n\n"
                )
                self.wfile.flush()
                self.close_connection = True

            def _empty(self, status: int, session: str | None = None) -> None:
                self.send_response(status)
                self.send_header("Content-Length", "0")
                if session:
                    self.send_header("Mcp-Session-Id", session)
                self.end_headers()

            def _json(self, status: int, obj: dict[str, Any], session: str | None = None) -> None:
                raw = json.dumps(obj).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                if session:
                    self.send_header("Mcp-Session-Id", session)
                self.end_headers()
                self.wfile.write(raw)

        return Handler


class ProxyHarness:
    def __init__(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="mcp_proxy_test_")
        self.state_dir = Path(self.tmp.name)
        self.upstream_port = _free_port()
        self.proxy_port = _free_port()
        self.upstream = FakeUnrealMCP(("127.0.0.1", self.upstream_port))
        self._up_thread = threading.Thread(target=self.upstream.serve_forever, daemon=True)
        self._up_thread.start()
        self.proxy_proc: subprocess.Popen[bytes] | None = None
        self.env = os.environ.copy()
        self.env["UNREAL_MCP_URL"] = f"http://127.0.0.1:{self.upstream_port}/mcp"
        self.env["UNREAL_MCP_STATE_DIR"] = str(self.state_dir)
        self.env["UNREAL_MCP_REQUEST_TIMEOUT"] = "30"
        self.env["UNREAL_MCP_LIST_TIMEOUT"] = "10"
        self.env["UNREAL_MCP_CONNECT_TIMEOUT"] = "2"
        self.env["UNREAL_MCP_READY_WAIT"] = "5"
        self.env["UNREAL_MCP_MIN_SESSION_GAP"] = "1"
        self.env["UNREAL_MCP_CACHE_TTL"] = "5"
        self.env["UNREAL_MCP_LIST_CACHE_TTL"] = "5"
        self.env["UNREAL_MCP_EMPTY_REINIT_AFTER"] = "2"
        self.env["PYTHONIOENCODING"] = "utf-8"

    def start_proxy(self) -> None:
        self.proxy_proc = subprocess.Popen(
            [sys.executable, str(PROXY_SCRIPT), "--http", f"127.0.0.1:{self.proxy_port}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=self.env,
        )
        deadline = time.time() + 8
        while time.time() < deadline:
            try:
                status, _, body = _http_json("GET", f"http://127.0.0.1:{self.proxy_port}/health")
                if status == 200 and isinstance(body, dict) and body.get("service") == proxy.SERVICE_NAME:
                    return
            except Exception:
                time.sleep(0.05)
        raise RuntimeError("proxy failed to start")

    def close(self) -> None:
        if self.proxy_proc and self.proxy_proc.poll() is None:
            self.proxy_proc.terminate()
            try:
                self.proxy_proc.wait(timeout=3)
            except Exception:
                self.proxy_proc.kill()
        self.upstream.shutdown()
        self.tmp.cleanup()


class TestProtocolAndHealth(unittest.TestCase):
    def setUp(self) -> None:
        self.h = ProxyHarness()
        self.h.start_proxy()

    def tearDown(self) -> None:
        self.h.close()

    def test_get_mcp_returns_405(self) -> None:
        # urllib raises on 405 — use raw socket
        sock = socket.create_connection(("127.0.0.1", self.h.proxy_port), timeout=2)
        try:
            sock.sendall(
                b"GET /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\nAccept: text/event-stream\r\nConnection: close\r\n\r\n"
            )
            data = sock.recv(4096).decode("utf-8", "replace")
        finally:
            sock.close()
        self.assertIn("405", data.split("\r\n", 1)[0])
        self.assertIn("Allow: POST", data)

    def test_connection_close_is_honored(self) -> None:
        """A client that asks to close must get EOF, not a socket held open."""
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            }
        ).encode("utf-8")
        sock = socket.create_connection(("127.0.0.1", self.h.proxy_port), timeout=15)
        sock.settimeout(15)
        t0 = time.time()
        try:
            sock.sendall(
                b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                b"Content-Type: application/json\r\n"
                b"Accept: application/json, text/event-stream\r\n"
                + f"Content-Length: {len(body)}\r\n".encode("ascii")
                + b"Connection: close\r\n\r\n"
                + body
            )
            chunks = []
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            sock.close()
        elapsed = time.time() - t0
        raw = b"".join(chunks).decode("utf-8", "replace")
        self.assertIn("Connection: close", raw)
        self.assertIn('"result"', raw)
        # Server must close promptly rather than stranding the client until its
        # own timeout, which reads as a mid-session MCP drop.
        self.assertLess(elapsed, 5.0, f"server held socket open for {elapsed:.1f}s")

    def test_health_identity(self) -> None:
        status, _, body = _http_json("GET", f"http://127.0.0.1:{self.h.proxy_port}/health")
        self.assertEqual(status, 200)
        assert isinstance(body, dict)
        self.assertTrue(body.get("ok"))
        self.assertEqual(body.get("service"), proxy.SERVICE_NAME)
        self.assertIn("version", body)
        self.assertIn("pid", body)


class TestStickyAndCalls(unittest.TestCase):
    def setUp(self) -> None:
        self.h = ProxyHarness()
        self.h.start_proxy()

    def tearDown(self) -> None:
        self.h.close()

    def _post(self, msg: dict[str, Any], timeout: float = 10.0) -> Any:
        raw = json.dumps(msg).encode("utf-8")
        _, headers, body = _http_json(
            "POST", f"http://127.0.0.1:{self.h.proxy_port}/mcp", raw, timeout=timeout
        )
        return headers, body

    def test_initialize_sticky_reuse(self) -> None:
        h1, b1 = self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            }
        )
        self.assertIn("result", b1)
        sid1 = h1.get("mcp-session-id")
        self.assertTrue(sid1)

        inits_before = sum(1 for c in self.h.upstream.calls if c["method"] == "initialize")
        h2, b2 = self._post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "t2", "version": "0"},
                },
            }
        )
        self.assertIn("result", b2)
        inits_after = sum(1 for c in self.h.upstream.calls if c["method"] == "initialize")
        # Sticky fast path — no second upstream initialize
        self.assertEqual(inits_before, inits_after)
        self.assertEqual(h2.get("mcp-session-id"), sid1)

    def test_tools_list_and_call(self) -> None:
        self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            }
        )
        _, listed = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        self.assertIn("result", listed)
        _, called = self._post(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "call_tool", "arguments": {"toolset_name": "X", "tool_name": "y"}},
            }
        )
        self.assertIn("result", called)

    def test_sse_tools_call_requires_upstream_keepalive(self) -> None:
        """Unreal streams tools/call results; asking to close loses them."""
        self.h.upstream.sse_tools_call = True
        self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            }
        )
        _, body = self._post(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "call_tool", "arguments": {"seq": 1}},
            },
            timeout=30.0,
        )
        self.assertIn("result", body, f"SSE result lost: {body}")
        self.assertEqual(body["result"]["content"][0]["text"], "sse-ok")
        self.assertTrue(self.h.upstream.close_requests)
        for requested in self.h.upstream.close_requests:
            self.assertNotIn("close", requested, "proxy must not ask Unreal to close")

    def test_stale_session_404_recovers(self) -> None:
        self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            }
        )
        sticky = self.h.state_dir / "sticky_session.json"
        deadline = time.time() + 3
        while time.time() < deadline and not sticky.is_file():
            time.sleep(0.05)
        self.assertTrue(sticky.is_file(), f"sticky missing; state={list(self.h.state_dir.iterdir())}")
        data = json.loads(sticky.read_text(encoding="utf-8"))
        data["session_id"] = "deadbeef" * 4
        sticky.write_text(json.dumps(data), encoding="utf-8")
        assert self.h.proxy_proc is not None
        self.h.proxy_proc.terminate()
        self.h.proxy_proc.wait(timeout=3)
        self.h.start_proxy()
        _, body = self._post({"jsonrpc": "2.0", "id": 9, "method": "tools/list", "params": {}})
        self.assertIn("result", body)


class TestEmptyReplyWedge(unittest.TestCase):
    """Unreal returns 200 + empty body for a stale session instead of 404."""

    def setUp(self) -> None:
        self.h = ProxyHarness()
        self.h.start_proxy()
        self._init()

    def tearDown(self) -> None:
        self.h.close()

    def _post(self, msg: dict[str, Any], timeout: float = 30.0) -> Any:
        raw = json.dumps(msg).encode("utf-8")
        _, _headers, body = _http_json(
            "POST", f"http://127.0.0.1:{self.h.proxy_port}/mcp", raw, timeout=timeout
        )
        return body

    def _init(self) -> None:
        self._post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            }
        )

    def _health(self) -> dict[str, Any]:
        _, _, body = _http_json("GET", f"http://127.0.0.1:{self.h.proxy_port}/health")
        assert isinstance(body, dict)
        return body

    def _call(self, n: int) -> Any:
        # Unique arguments per call so neither the response cache nor the
        # coalescer can mask a real upstream round trip.
        return self._post(
            {
                "jsonrpc": "2.0",
                "id": 100 + n,
                "method": "tools/call",
                "params": {"name": "call_tool", "arguments": {"seq": n}},
            }
        )

    def _wedge(self) -> None:
        self.h.upstream.empty_on_unknown_session = True
        self.h.upstream.fail_unknown_session = False
        self.h.upstream.sessions.clear()

    def test_empty_reply_recovers_after_threshold(self) -> None:
        self._wedge()
        first = self._call(1)
        self.assertIn("error", first)
        self.assertIn("Resubmit", first["error"]["message"])

        second = self._call(2)
        self.assertIn("result", second, f"expected recovery, got {second}")

        health = self._health()
        self.assertGreaterEqual(health["counters"]["empty_upstream"], 1)
        self.assertGreaterEqual(health["counters"]["wedge_recoveries"], 1)
        self.assertGreaterEqual(health["counters"]["session_reinits"], 1)
        self.assertEqual(health["consecutive_empty"], 0)

        # Session is healthy again — subsequent calls must not re-init.
        reinits = health["counters"]["session_reinits"]
        self.assertIn("result", self._call(3))
        self.assertEqual(self._health()["counters"]["session_reinits"], reinits)

    def test_empty_reply_fails_fast(self) -> None:
        self._wedge()
        t0 = time.time()
        self._call(1)
        elapsed = time.time() - t0
        # Must not burn the request/list timeout budget waiting on a body that
        # the server already declared as zero-length.
        self.assertLess(elapsed, 5.0, f"empty reply took {elapsed:.1f}s")

    def test_persistent_empty_reports_wedge_not_silence(self) -> None:
        self.h.upstream.force_empty = True
        self._call(1)
        body = self._call(2)
        self.assertIn("error", body)
        self.assertIn("check_unreal", body["error"]["message"])
        self.assertNotIn("result", body)

    def test_degenerate_tools_list_not_cached(self) -> None:
        self.h.upstream.empty_tools = True
        first = self._post({"jsonrpc": "2.0", "id": 20, "method": "tools/list", "params": {}})
        self.assertEqual(first.get("result", {}).get("tools"), [])
        self.assertGreaterEqual(self._health()["counters"]["degenerate_not_cached"], 1)

        self.h.upstream.empty_tools = False
        time.sleep(1.3)  # let the short-retention coalesce result expire
        second = self._post({"jsonrpc": "2.0", "id": 21, "method": "tools/list", "params": {}})
        self.assertTrue(
            second.get("result", {}).get("tools"),
            f"empty tools/list was cached: {second}",
        )


class TestLocksAndEnsure(unittest.TestCase):
    def test_live_lock_not_age_stolen(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.lock"
            fd, token = proxy._acquire_file_lock(path, timeout=1.0)
            # Age the mtime artificially — old code would steal; new must not.
            old = time.time() - 120
            os.utime(path, (old, old))
            t0 = time.time()
            with self.assertRaises(TimeoutError):
                proxy._acquire_file_lock(path, timeout=0.4)
            self.assertLess(time.time() - t0, 2.0)
            proxy._release_file_lock(fd, path, token)
            fd2, token2 = proxy._acquire_file_lock(path, timeout=1.0)
            proxy._release_file_lock(fd2, path, token2)

    def test_dead_owner_lock_is_broken(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dead.lock"
            # Fake dead pid
            path.write_text("1 deadtoken 0.0\n", encoding="utf-8")
            fd, token = proxy._acquire_file_lock(path, timeout=2.0)
            proxy._release_file_lock(fd, path, token)
            self.assertFalse(path.exists())

    def test_concurrent_ensure_http_single_listener(self) -> None:
        h = ProxyHarness()
        pid: int | None = None
        try:
            results: list[dict[str, Any]] = []
            errors: list[str] = []

            def worker() -> None:
                try:
                    proc = subprocess.run(
                        [
                            sys.executable,
                            str(PROXY_SCRIPT),
                            "--ensure-http",
                            f"127.0.0.1:{h.proxy_port}",
                        ],
                        env=h.env,
                        capture_output=True,
                        text=True,
                        timeout=20,
                        check=False,
                    )
                    data = json.loads(proc.stdout.strip() or "{}")
                    results.append(data)
                except Exception as exc:  # noqa: BLE001
                    errors.append(str(exc))

            threads = [threading.Thread(target=worker) for _ in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=25)
            self.assertFalse(errors, errors)
            self.assertTrue(results)
            oks = [r for r in results if r.get("ok")]
            self.assertGreaterEqual(len(oks), 1)
            health = proxy.probe_proxy_health("127.0.0.1", h.proxy_port)
            self.assertTrue(health.get("ok"), health)
            pid = (health.get("health") or {}).get("pid")
        finally:
            if pid:
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/PID", str(pid), "/F"],
                        capture_output=True,
                        check=False,
                    )
                else:
                    try:
                        os.kill(int(pid), 15)
                    except OSError:
                        pass
                time.sleep(0.3)
            try:
                h.upstream.shutdown()
            except Exception:
                pass
            try:
                h.tmp.cleanup()
            except Exception:
                pass

    def test_long_call_serialize_beyond_old_stale_window(self) -> None:
        """Call lock must hold through a multi-second call (old stale_after was 20s)."""
        h = ProxyHarness()
        h.upstream.call_delay_s = 2.5
        h.start_proxy()
        try:
            init = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "t", "version": "0"},
                },
            }
            _http_json("POST", f"http://127.0.0.1:{h.proxy_port}/mcp", json.dumps(init).encode(), timeout=10)

            results: list[Any] = []

            def call(i: int) -> None:
                msg = {
                    "jsonrpc": "2.0",
                    "id": 10 + i,
                    "method": "tools/call",
                    "params": {
                        "name": "call_tool",
                        "arguments": {"toolset_name": "T", "tool_name": f"op{i}", "i": i},
                    },
                }
                _status, _headers, body = _http_json(
                    "POST",
                    f"http://127.0.0.1:{h.proxy_port}/mcp",
                    json.dumps(msg).encode(),
                    timeout=20,
                )
                results.append(body)

            t1 = threading.Thread(target=call, args=(1,))
            t2 = threading.Thread(target=call, args=(2,))
            t0 = time.time()
            t1.start()
            time.sleep(0.2)
            t2.start()
            t1.join(timeout=25)
            t2.join(timeout=25)
            elapsed = time.time() - t0
            # Serialized 2.5 + 2.5 => should exceed ~4.5s (not fully parallel)
            self.assertGreaterEqual(elapsed, 4.0)
            self.assertEqual(len(results), 2)
            self.assertTrue(all(isinstance(r, dict) and "result" in r for r in results))
        finally:
            h.close()


class TestAtomicAndProbe(unittest.TestCase):
    def test_atomic_write_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sticky_session.json"
            proxy._atomic_write_json(path, {"session_id": "abc", "ok": True})
            data = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(data["session_id"], "abc")
            self.assertFalse(list(Path(tmp).glob("*.tmp")))

    def test_probe_down(self) -> None:
        port = _free_port()
        health = proxy.probe_proxy_health("127.0.0.1", port)
        self.assertFalse(health.get("listening"))
        self.assertFalse(health.get("ok"))


if __name__ == "__main__":
    unittest.main()
