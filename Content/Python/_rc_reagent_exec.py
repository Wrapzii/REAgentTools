# REAgentTools — Unreal Remote Control entry (no MCP required).
#
# Preferred (ONE Cursor tool call): pass the request inline so wire+run+result
# happen in a single ExecutePythonCommand / host _rc_exec.py invocation.
#
#   # via oneshot snippet (skill: reagent-rc-oneshot)
#   from re_agent_tools.rc_bridge import oneshot_python
#   exec(oneshot_python({"action":"call","toolset":"REContextTools",
#                        "tool":"get_editor_context","arguments":{...}}))
#
#   # or argv on the script itself (editor py / RC file exec with args):
#   py ".../_rc_reagent_exec.py" --json "{\"action\":\"list_toolsets\"}"
#
# Fallback (3 hops — avoid unless your RC wrapper cannot return stdout/log):
#   write Saved/REAgentTools/rc_request.json → run this script → read rc_response.json

from __future__ import annotations

import json
import os
import sys


def _ensure_plugin_python_path() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)


def _emit(result: str) -> str:
    """Print marked result for one-shot RC capture; also mirror to response file."""
    _ensure_plugin_python_path()
    from re_agent_tools.rc_bridge import RESULT_BEGIN, RESULT_END, request_paths

    _, resp_path = request_paths()
    try:
        with open(resp_path, "w", encoding="utf-8") as fh:
            fh.write(result)
            fh.write("\n")
    except Exception:  # noqa: BLE001
        pass
    print(RESULT_BEGIN)
    print(result)
    print(RESULT_END)
    try:
        import unreal

        unreal.log(f"[REAgentTools/_rc_reagent_exec] {result[:500]}")
    except Exception:  # noqa: BLE001
        pass
    return result


def run(request: dict | None = None) -> str:
    _ensure_plugin_python_path()
    from re_agent_tools import rc_bridge

    if request is not None:
        return _emit(rc_bridge.run_request(request))

    env_json = os.environ.get("REAGENT_RC_REQUEST", "").strip()
    if env_json:
        try:
            payload = json.loads(env_json)
        except Exception as exc:  # noqa: BLE001
            from re_agent_tools.common.results import error_result

            return _emit(
                error_result(
                    "_rc_reagent_exec",
                    f"REAGENT_RC_REQUEST is not valid JSON: {exc}",
                )
            )
        return run(payload if isinstance(payload, dict) else None)

    # File protocol (multi-hop fallback)
    return _emit(rc_bridge.run_from_files())


def _parse_argv(argv: list[str]) -> dict | None:
    if not argv:
        return None
    if argv[0] == "--json" and len(argv) >= 2:
        return json.loads(argv[1])
    if argv[0].startswith("--json="):
        return json.loads(argv[0].split("=", 1)[1])
    # Bare JSON blob as first arg
    if argv[0].lstrip().startswith("{"):
        return json.loads(argv[0])
    return None


if __name__ == "__main__":
    req = None
    try:
        req = _parse_argv(sys.argv[1:])
    except Exception as exc:  # noqa: BLE001
        _ensure_plugin_python_path()
        from re_agent_tools.common.results import error_result

        _emit(error_result("_rc_reagent_exec", f"Bad argv JSON: {exc}"))
        raise SystemExit(1) from exc
    run(req)
