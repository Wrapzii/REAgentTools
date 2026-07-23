"""Compact JSON workflow results for MCP tool returns."""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from re_agent_tools.common.limits import enforce_response_soft_limit
from re_agent_tools.common.serialization import dumps_compact


def make_request_id() -> str:
    return uuid.uuid4().hex[:12]


class WorkflowTimer:
    """Simple wall-clock timer for duration_ms."""

    def __init__(self) -> None:
        self._start = time.perf_counter()

    @property
    def elapsed_ms(self) -> float:
        return round((time.perf_counter() - self._start) * 1000.0, 2)


def workflow_result(
    operation: str,
    success: bool,
    summary: str = "",
    *,
    request_id: str | None = None,
    resolved_targets: list[dict[str, Any]] | dict[str, Any] | None = None,
    changed: list[str] | None = None,
    created: list[str] | None = None,
    deleted: list[str] | None = None,
    compiled: list[str] | None = None,
    saved: list[str] | None = None,
    warnings: list[str] | None = None,
    errors: list[str] | None = None,
    truncated: bool = False,
    duration_ms: float | None = None,
    detail_level: str = "normal",
    extra: dict[str, Any] | None = None,
) -> str:
    """Build a WorkflowResult dict and return compact JSON string."""
    payload: dict[str, Any] = {
        "success": success,
        "request_id": request_id or make_request_id(),
        "operation": operation,
        "summary": summary,
        "resolved_targets": resolved_targets or [],
        "changed": changed or [],
        "created": created or [],
        "deleted": deleted or [],
        "compiled": compiled or [],
        "saved": saved or [],
        "warnings": warnings or [],
        "errors": errors or [],
        "truncated": truncated,
        "duration_ms": duration_ms,
        "detail_level": detail_level,
    }
    if extra:
        payload.update(extra)
    payload, soft_warnings = enforce_response_soft_limit(payload)
    if soft_warnings:
        payload["warnings"] = list(payload.get("warnings", [])) + soft_warnings
    return dumps_compact(payload)


def error_result(
    operation: str,
    message: str,
    *,
    request_id: str | None = None,
    errors: list[str] | None = None,
    duration_ms: float | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    return workflow_result(
        operation,
        False,
        message,
        request_id=request_id,
        errors=errors or [message],
        duration_ms=duration_ms,
        extra=extra,
    )
