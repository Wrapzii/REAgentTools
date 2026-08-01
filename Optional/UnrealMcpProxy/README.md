# UnrealMcpProxy (optional, canonical)

Anti-thrash HTTP/stdio sidecar between Cursor MCP and Unreal’s native MCP listener.

| Role | Port / URL |
|------|------------|
| Unreal native MCP | `http://127.0.0.1:8000/mcp` |
| This proxy (Cursor should use this) | `http://127.0.0.1:8001/mcp` |
| Health / identity | `http://127.0.0.1:8001/health` |

## Why it exists

Cursor HTTP MCP clients open `GET /mcp` for Streamable HTTP. Returning a short JSON `200` causes reconnect storms (tens of thousands of GETs), ephemeral-port TimeWait pile-ups, and mid-session “MCP dropped” states even while Unreal is healthy.

This proxy:

- Returns **`405 Method Not Allowed`** on `GET /mcp` (`Allow: POST`) — protocol-correct when no SSE stream is offered
- Serves **`GET /health`** with service identity + diagnostics
- Keeps **one sticky upstream session** on disk; timeouts do **not** open new Unreal sessions
- Serializes / coalesces `tools/call`
- **Never** kills or rebinds `:8001` on “address in use” — verify `/health` and reuse

## Connection handling (the mid-session drop cause)

Two connection rules are load-bearing. Breaking either produces “MCP returns
empty responses” or “MCP timed out” while Unreal is perfectly healthy.

**Upstream to Unreal must be `Connection: keep-alive`.** Unreal answers
`tools/call` with a **streaming SSE body on the POST response**
(`content-type: text/event-stream`, no `Content-Length`) and writes the
JSON-RPC result as an event when the tool finishes. If the client sends
`Connection: close`, Unreal returns headers only and tears the stream down —
every `tools/call` yields an empty `200` and no result. `tools/list` and
`initialize` are unaffected because they answer inline as `application/json`,
which is why discovery looks fine while all real work fails.

**Downstream to the client must honor the client's `Connection` header.**
Answering `keep-alive` unconditionally resets Python's `close_connection`
flag, so a client that sent `Connection: close` and reads until EOF blocks
until its own timeout. That is indistinguishable from a hung editor and was
adding a full client timeout to every call.

## Empty upstream replies

An empty non-error reply is a distinct failure from a busy editor, so it is
handled separately:

- Detected and surfaced immediately — never spins out the request timeout
- After `UNREAL_MCP_EMPTY_REINIT_AFTER` (default **2**) consecutive empties the
  proxy performs **one** controlled reinit and retries the call once, rather
  than pinning a dead session forever (Unreal reports a stale session as
  `200` + empty body on some paths, not `404`)
- Empty / degenerate results (including an empty `tools/list`) are **never
  cached**, so a wedge cannot be replayed for the cache TTL
- Counters live in `/health`: `empty_upstream`, `wedge_recoveries`,
  `session_reinits`, `degenerate_not_cached`, plus `consecutive_empty`

## Cursor MCP wiring

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

Do **not** point desktop Editor Agent at raw `:8000` if you want sticky-session protection.

Client tool timeouts should be **≥** `UNREAL_MCP_REQUEST_TIMEOUT` (default **180s**). Shorter client timeouts look like mid-session drops while the proxy is still waiting on Unreal.

## Start / ensure

```bash
# Start or reuse a healthy listener (safe to call repeatedly)
python Optional/UnrealMcpProxy/unreal_mcp_proxy.py --ensure-http 127.0.0.1:8001

# Probe without mutating
python Optional/UnrealMcpProxy/unreal_mcp_proxy.py --health 127.0.0.1:8001
```

## Upgrading a running proxy

`--restart` is the **only** sanctioned way to replace a live listener. It is a
deployment action, never a recovery step — agents must not use it for timeouts
or WinError 10048.

```bash
python Optional/UnrealMcpProxy/unreal_mcp_proxy.py --restart 127.0.0.1:8001
```

It refuses to act when calls are in flight or the port belongs to something
else (override with `--force`), stops the old PID, drops the sticky session so
the new instance negotiates a fresh one, and re-ensures the listener.

`--health` reports `running_version` vs `canonical_version` and an `outdated`
flag. An outdated proxy still reports `ok: true` — keep using it and upgrade
when the editor is idle.

## Agent rules (hard)

1. WinError **10048** / “address already in use” → call `--health` / `unreal-watch.check_unreal`. If `/health` shows `unreal-mcp-http-proxy`, **reuse**. Never kill/rebind `:8001`.
2. Mid-session MCP timeout → `check_unreal` **once** → one batched MCP resubmit → only then RC oneshot.
3. `GET /mcp` volume is transport negotiation noise after a bad response shape — not Unreal death.
4. Epic `datarouter` / libcurl DNS errors are telemetry, ignore.
5. “Unreal MCP returned an empty reply” → **resubmit the same call once**. The proxy re-establishes the session on the second empty. Only if a freshly created session also returns empty is the editor actually blocked (then `check_unreal`, then RC).
6. `call_tool` needs the **registry name** from `list_toolsets`, e.g. `re_agent_tools.toolsets.context_tools.REContextTools` — the bare class name `REContextTools` returns “Toolset not found”, which is a bad argument, not an unreachable toolset.

## State

Defaults to `<UnrealProject>/Saved/REAgentTools/mcp_proxy/` when installed under `Plugins/REAgentTools/`, else `%LOCALAPPDATA%/REAgentTools/mcp_proxy/<host_port>/`.

Override with `UNREAL_MCP_STATE_DIR`.

## Tests

```bash
python -m unittest Optional.UnrealMcpProxy.tests.test_proxy -v
```

Uses a fake upstream on ephemeral ports — does not touch live `:8000` / `:8001`.
