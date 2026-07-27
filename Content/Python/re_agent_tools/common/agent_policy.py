"""Anti-burn agent policy attached to every REAgentTools WorkflowResult.

Token burn comes from LLM round-trips after failures (agents falling back to
Epic SceneTools / ActorTools / ObjectTools one-by-one). Every composite result
must tell the agent: stay in REAgentTools, retry once, then STOP.
"""

from __future__ import annotations

from typing import Any

# Compact — kept short so it does not itself inflate MCP payloads.
AGENT_POLICY: dict[str, Any] = {
    "forbid_epic_manual_fallback": True,
    "max_recovery_mcp_calls": 1,
    "on_failure": (
        "Retry ONCE via REAgentTools only "
        "(prefer execute_editor_batch or the same composite with fixed args). "
        "Then STOP and report errors — do not open a new tool loop."
    ),
    "forbidden_on_failure": [
        "editor_toolset.toolsets.scene.SceneTools",
        "editor_toolset.toolsets.actor.ActorTools",
        "editor_toolset.toolsets.object.ObjectTools",
        "list_toolsets",
        "describe_toolset",
    ],
    "required_recovery_path": [
        "re_agent_tools.toolsets.batch_workflow_tools.REBatchWorkflowTools.execute_editor_batch",
        "same REAgentTools composite that failed",
    ],
}


def recovery_block(
    *,
    operation: str,
    errors: list[str] | None = None,
    candidates: list[dict[str, Any]] | None = None,
    suggested_ops: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Structured recovery payload so the next step needs no Epic discovery."""
    return {
        "mode": "retry_batch_once",
        "failed_operation": operation,
        "errors": list(errors or [])[:10],
        "candidates": list(candidates or [])[:15],
        "suggested_ops": list(suggested_ops or [])[:10],
        "instruction": (
            "DO NOT call Epic SceneTools/ActorTools/ObjectTools. "
            "Issue at most ONE more REAgentTools call (execute_editor_batch "
            "with suggested_ops or fixed labels from candidates). Then STOP."
        ),
    }


def attach_agent_policy(
    payload: dict[str, Any],
    *,
    operation: str,
    success: bool,
) -> dict[str, Any]:
    """Merge policy into every result; add recovery block on failure."""
    out = dict(payload)
    out["agent_policy"] = dict(AGENT_POLICY)
    if not success:
        existing = out.get("recovery") if isinstance(out.get("recovery"), dict) else {}
        errors = out.get("errors") if isinstance(out.get("errors"), list) else []
        candidates = existing.get("candidates") or out.get("candidates") or []
        suggested = existing.get("suggested_ops") or out.get("suggested_ops") or []
        out["recovery"] = {
            **recovery_block(
                operation=operation,
                errors=[str(e) for e in errors],
                candidates=list(candidates) if isinstance(candidates, list) else [],
                suggested_ops=list(suggested) if isinstance(suggested, list) else [],
            ),
            **existing,
        }
    return out
