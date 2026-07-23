"""Append-only JSONL logging for tool calls."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import unreal


def _log_path() -> str:
    base = unreal.Paths.project_saved_dir()
    folder = os.path.join(base, "REAgentTools")
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, "tool_calls.jsonl")


def log_tool_call(
    operation: str,
    *,
    success: bool,
    request_id: str,
    duration_ms: float | None = None,
    summary: str = "",
    extra: dict[str, Any] | None = None,
) -> None:
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "operation": operation,
        "success": success,
        "request_id": request_id,
        "duration_ms": duration_ms,
        "summary": summary,
    }
    if extra:
        entry.update(extra)
    try:
        with open(_log_path(), "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, separators=(",", ":"), default=str))
            handle.write("\n")
    except OSError as exc:
        unreal.log_warning(f"[REAgentTools] log write failed: {exc}")
