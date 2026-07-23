"""Configurable limits and truncation."""

from __future__ import annotations

import json
from typing import Any

# Defaults mirror Config/DefaultREAgentTools.ini
SEARCH_LIMIT = 25
MUTATE_LIMIT = 25
BATCH_LIMIT = 20
RESPONSE_SOFT_LIMIT_BYTES = 51200


def truncate_list(
    items: list[Any],
    limit: int,
    *,
    label: str = "items",
) -> tuple[list[Any], bool, str | None]:
    if len(items) <= limit:
        return items, False, None
    return (
        items[:limit],
        True,
        f"Truncated {label} to {limit} (had {len(items)})",
    )


def enforce_response_soft_limit(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Warn and trim detail when JSON exceeds soft byte limit."""
    warnings: list[str] = []
    encoded = json.dumps(payload, separators=(",", ":"), default=str)
    if len(encoded.encode("utf-8")) <= RESPONSE_SOFT_LIMIT_BYTES:
        return payload, warnings

    warnings.append(
        f"Response exceeded soft limit ({RESPONSE_SOFT_LIMIT_BYTES} bytes); "
        "trimming resolved_targets detail"
    )
    trimmed = dict(payload)
    targets = trimmed.get("resolved_targets")
    if isinstance(targets, list) and targets:
        trimmed["resolved_targets"] = [
            {"id": t.get("id"), "kind": t.get("kind"), "path": t.get("path")}
            if isinstance(t, dict)
            else {"ref": str(t)}
            for t in targets[:SEARCH_LIMIT]
        ]
        trimmed["truncated"] = True
    return trimmed, warnings
