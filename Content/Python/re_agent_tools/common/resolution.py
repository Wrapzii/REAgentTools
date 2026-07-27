"""Resolve actors and assets by label/path without full-world dumps."""

from __future__ import annotations

from typing import Any

import unreal

from re_agent_tools.common.limits import SEARCH_LIMIT, truncate_list


class ResolutionError(ValueError):
    """Base resolution failure. May carry compact candidates for batch retry."""

    def __init__(self, message: str, *, candidates: list[dict[str, str]] | None = None):
        self.candidates = list(candidates or [])
        super().__init__(message)


class AmbiguousResolutionError(ResolutionError):
    def __init__(self, kind: str, query: str, candidates: list[dict[str, str]]):
        self.kind = kind
        self.query = query
        labels = [c.get("label") or c.get("path", "?") for c in candidates[:8]]
        super().__init__(
            (
                f"Ambiguous {kind} query {query!r}: {len(candidates)} matches; "
                f"candidates={labels}. Retry execute_editor_batch with an exact "
                f"label — DO NOT use Epic SceneTools.find_actors."
            ),
            candidates=candidates,
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


def _token_score(query: str, label: str, class_name: str) -> int:
    """Cheap overlap score for soft suggestions (no fuzzy lib)."""
    q = query.lower().replace("_", " ").replace("-", " ")
    tokens = [t for t in q.split() if t]
    if not tokens:
        return 0
    hay = f"{label} {class_name}".lower()
    score = 0
    for t in tokens:
        if t in hay:
            score += 2
        if t in label.lower():
            score += 1
        if t in class_name.lower():
            score += 2
    return score


def suggest_actors(query: str, *, limit: int = 10) -> list[dict[str, str]]:
    """Compact nearby actor suggestions for recovery payloads (no Epic MCP)."""
    query = (query or "").strip()
    if not query:
        return []
    scored: list[tuple[int, unreal.Actor]] = []
    for actor in _eas().get_all_level_actors():
        label = actor.get_actor_label()
        cls = actor.get_class().get_name()
        score = _token_score(query, label, cls)
        if score <= 0:
            # still allow class-name substring for PlayerStart-style queries
            q_lower = query.lower()
            if q_lower in label.lower() or q_lower in cls.lower() or q_lower in actor.get_path_name().lower():
                score = 1
        if score > 0:
            scored.append((score, actor))
    scored.sort(key=lambda x: (-x[0], x[1].get_actor_label()))
    return [actor_ref(a) for _, a in scored[:limit]]


def find_actors_compact(
    *,
    name: str = "",
    class_name: str = "",
    limit: int = SEARCH_LIMIT,
) -> list[dict[str, str]]:
    """In-plugin actor search — replaces Epic SceneTools.find_actors for agents."""
    name_l = name.strip().lower()
    class_l = class_name.strip().lower()
    hits: list[unreal.Actor] = []
    for actor in _eas().get_all_level_actors():
        label = actor.get_actor_label()
        cls = actor.get_class().get_name()
        if name_l and name_l not in label.lower() and name_l not in actor.get_path_name().lower():
            continue
        if class_l and class_l not in cls.lower():
            continue
        if not name_l and not class_l:
            continue
        hits.append(actor)
    items, _, _ = truncate_list(hits, limit, label="actors")
    return [actor_ref(a) for a in items]


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
        # class-name unique match (PlayerStart / GAME_PlayerStart drift)
        class_matches = [a for a in actors if q_lower in a.get_class().get_name().lower()]
        if len(class_matches) == 1:
            return class_matches[0]
        suggestions = suggest_actors(query, limit=SEARCH_LIMIT)
        if len(class_matches) > 1:
            raise AmbiguousResolutionError(
                "actor",
                query,
                [actor_ref(a) for a in class_matches[:SEARCH_LIMIT]],
            )
        raise ResolutionError(
            (
                f"No actor matched {query!r}. candidates="
                f"{[c.get('label') for c in suggestions[:8]]}. "
                "Retry execute_editor_batch with find_actors or an exact label "
                "from candidates — DO NOT use Epic SceneTools.find_actors."
            ),
            candidates=suggestions,
        )

    raise AmbiguousResolutionError(
        "actor", query, [actor_ref(a) for a in partial[:SEARCH_LIMIT]]
    )


def resolve_actor_soft(query: str) -> tuple[unreal.Actor, list[str]]:
    """Resolve with unique soft fallbacks; return (actor, warnings)."""
    warnings: list[str] = []
    try:
        return resolve_actor(query), warnings
    except AmbiguousResolutionError:
        raise
    except ResolutionError as exc:
        # Last-chance: unique class token if query has an UnderscoredClass-like token
        tokens = [t for t in query.replace("-", "_").split("_") if len(t) >= 4]
        actors = _eas().get_all_level_actors()
        for token in reversed(tokens):
            t_lower = token.lower()
            class_hits = [a for a in actors if a.get_class().get_name().lower() == t_lower]
            if len(class_hits) == 1:
                warnings.append(
                    f"Soft-resolved {query!r} via unique class {token!r} → "
                    f"{class_hits[0].get_actor_label()!r}"
                )
                return class_hits[0], warnings
            label_hits = [a for a in actors if t_lower in a.get_actor_label().lower()]
            if len(label_hits) == 1:
                warnings.append(
                    f"Soft-resolved {query!r} via unique token {token!r} → "
                    f"{label_hits[0].get_actor_label()!r}"
                )
                return label_hits[0], warnings
        raise exc


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
