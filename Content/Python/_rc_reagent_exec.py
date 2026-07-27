# REAgentTools — Unreal Remote Control entry (no MCP required).
#
# Why: Cursor Remote Control / Agents Window often reports mcp-unreal
#   serverStatus=error ("failed during live tool discovery"). Unreal Remote
#   Control (:30010) still works. Call RE composites through this bridge —
#   do NOT fall back to Epic SceneTools or ad-hoc editor scripts.
#
# Usage (Unreal Remote Control ExecutePythonCommand / editor console):
#   1) Write Saved/REAgentTools/rc_request.json, e.g.:
#      {"action":"call","toolset":"REContextTools","tool":"get_editor_context",
#       "arguments":{"include_level":true,"include_selection":true}}
#   2) py "…/Plugins/REAgentTools/Content/Python/_rc_reagent_exec.py"
#      (or import + reload; see run() below)
#   3) Read Saved/REAgentTools/rc_response.json
#
# Inline (when your RC wrapper can pass a Python expression):
#   import importlib, re_agent_tools.rc_bridge as b; importlib.reload(b); \
#   print(b.call_tool("REBatchWorkflowTools","execute_editor_batch",
#         {"operations_json":"[…]","dry_run":true}))

from __future__ import annotations

import json
import os
import sys


def _ensure_plugin_python_path() -> None:
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)


def run(request: dict | None = None) -> str:
    _ensure_plugin_python_path()
    from re_agent_tools import rc_bridge

    if request is not None:
        result = rc_bridge.run_request(request)
        # Still mirror to response file for RC clients that only read files.
        _, resp_path = rc_bridge.request_paths()
        try:
            with open(resp_path, "w", encoding="utf-8") as fh:
                fh.write(result)
                fh.write("\n")
        except Exception:  # noqa: BLE001
            pass
        return result

    env_json = os.environ.get("REAGENT_RC_REQUEST", "").strip()
    if env_json:
        try:
            payload = json.loads(env_json)
        except Exception as exc:  # noqa: BLE001
            from re_agent_tools.common.results import error_result

            return error_result(
                "_rc_reagent_exec",
                f"REAGENT_RC_REQUEST is not valid JSON: {exc}",
            )
        return run(payload if isinstance(payload, dict) else None)

    return rc_bridge.run_from_files()


if __name__ == "__main__":
    out = run()
    try:
        import unreal

        unreal.log(f"[REAgentTools/_rc_reagent_exec] {out[:500]}")
    except Exception:  # noqa: BLE001
        print(out)
