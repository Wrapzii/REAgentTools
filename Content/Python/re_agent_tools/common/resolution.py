"""Resolve actors and assets by label/path without full-world dumps."""

from __future__ import annotations

from typing import Any

import unreal

from re_agent_tools.common.limits import SEARCH_LIMIT, truncate_list


class ResolutionError(ValueError):
    """Base resolution failure."""


class AmbiguousResolutionError(ResolutionError):
    def __init__(self, kind: str, query: str, candidates: list[dict[str, str]]):
        self.kind = kind
        self.query = query
        self.candidates = candidates
        super().__init__(
            f"Ambiguous {kind} query {query!r}: {len(candidates)} matches"
        )


def _eas() -> unreal.EditorActorSubsystem:
    return unreal.get_editor_subsystem(unreal.EditorActorSubsystem)


def _eas_assets() -> unreal.EditorAssetSubsystem:
    return unreal.get_editor_subsystem(unreal.EditorAssetSubsystem)


def actor_ref(actor: unreal.Actor) -> dict[str, str]:
    return {
        "kind": "actor",
        "label": actor.get_actor_label(),
        "path": actor.get_path_name(),
        "class": actor.get_class().get_name(),
    }


def asset_ref(path: str) -> dict[str, str]:
    asset = unreal.load_asset(path)
    cls = asset.get_class().get_name() if asset else "Unknown"
    return {"kind": "asset", "path": path, "class": cls}


def resolve_actor(query: str) -> unreal.Actor:
    """Exact label match preferred, else unique case-insensitive substring."""
    query = query.strip()
    if not query:
        raise ResolutionError("Actor query is empty")

    actors = _eas().get_all_level_actors()
    exact = [a for a in actors if a.get_actor_label() == query]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise AmbiguousResolutionError(
            "actor", query, [actor_ref(a) for a in exact[:SEARCH_LIMIT]]
        )

    q_lower = query.lower()
    partial = [a for a in actors if q_lower in a.get_actor_label().lower()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        # path suffix fallback
        path_matches = [a for a in actors if query in a.get_path_name()]
        if len(path_matches) == 1:
            return path_matches[0]
        if len(path_matches) > 1:
            raise AmbiguousResolutionError(
                "actor", query, [actor_ref(a) for a in path_matches[:SEARCH_LIMIT]]
            )
        raise ResolutionError(f"No actor matched {query!r}")

    raise AmbiguousResolutionError(
        "actor", query, [actor_ref(a) for a in partial[:SEARCH_LIMIT]]
    )


def resolve_actors(queries: list[str], *, limit: int = SEARCH_LIMIT) -> list[unreal.Actor]:
    queries, truncated, msg = truncate_list(queries, limit, label="actor queries")
    actors = [resolve_actor(q) for q in queries]
    if truncated and msg:
        raise ResolutionError(msg)
    return actors


def resolve_asset(path: str) -> unreal.Object:
    path = path.strip()
    if not path:
        raise ResolutionError("Asset path is empty")
    if not path.startswith("/"):
        path = f"/Game/{path.lstrip('/')}"
    if not _eas_assets().does_asset_exist(path):
        # fuzzy name search in folder
        folder, _, name = path.rpartition("/")
        if not folder:
            folder = "/Game"
        assets = _eas_assets().list_assets(folder, recursive=True)
        name_lower = name.lower()
        matches = [p for p in assets if p.rsplit("/", 1)[-1].lower() == name_lower]
        if len(matches) == 1:
            path = matches[0]
        elif len(matches) > 1:
            raise AmbiguousResolutionError(
                "asset", path, [asset_ref(p) for p in matches[:SEARCH_LIMIT]]
            )
        else:
            raise ResolutionError(f"Asset not found: {path}")
    return unreal.load_asset(path)


def find_assets(
    *,
    path: str = "/Game",
    class_name: str = "",
    name_filter: str = "",
    limit: int = SEARCH_LIMIT,
) -> tuple[list[str], bool, str | None]:
    if not path.startswith("/"):
        path = f"/Game/{path.lstrip('/')}"
    assets = _eas_assets().list_assets(path, recursive=True)
    if class_name:
        cls_lower = class_name.lower()
        assets = [
            p
            for p in assets
            if cls_lower in (unreal.load_asset(p).get_class().get_name().lower() if unreal.load_asset(p) else "")
        ]
    if name_filter:
        nf = name_filter.lower()
        assets = [p for p in assets if nf in p.rsplit("/", 1)[-1].lower()]
    return truncate_list(assets, limit, label="assets")


def resolve_targets(
  actor_queries: list[str] | None = None,
  asset_paths: list[str] | None = None,
) -> dict[str, Any]:
    resolved: list[dict[str, str]] = []
    warnings: list[str] = []
    truncated = False

    if actor_queries:
        items, trunc, msg = truncate_list(actor_queries, SEARCH_LIMIT, label="actors")
        truncated = truncated or trunc
        if msg:
            warnings.append(msg)
        for q in items:
            resolved.append(actor_ref(resolve_actor(q)))

    if asset_paths:
        items, trunc, msg = truncate_list(asset_paths, SEARCH_LIMIT, label="assets")
        truncated = truncated or trunc
        if msg:
            warnings.append(msg)
        for p in items:
            resolved.append(asset_ref(p))

    return {"targets": resolved, "warnings": warnings, "truncated": truncated}
