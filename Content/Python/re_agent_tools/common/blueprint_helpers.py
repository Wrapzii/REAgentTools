"""UE 5.8-safe Blueprint create/inspect helpers.

`UBlueprint.ParentClass` is protected in Python; `get_super_class` is unavailable
on BlueprintGeneratedClass. Prefer BlueprintEditorLibrary + Asset Registry tags.
Set ParentClass on the factory before create (or use create_blueprint_asset_with_parent).
"""

from __future__ import annotations

import unreal
from toolset_registry.helpers import compile_blueprint, create_asset

from re_agent_tools.common.resolution import ResolutionError


def _normalize_class_tag(tag: str) -> str:
    """Turn AssetRegistry ParentClass tag into a clean path if possible."""
    text = (tag or "").strip()
    if not text or text == "None":
        return ""
    # e.g. /Script/CoreUObject.Class'/Script/Engine.Actor'
    if "'" in text:
        inner = text.split("'")
        if len(inner) >= 2 and inner[1]:
            return inner[1]
    return text


def get_blueprint_parent_class(bp: unreal.Blueprint) -> unreal.Class | None:
    """Best-effort parent class for a Blueprint asset."""
    # 1) BlueprintEditorLibrary (UE 5.8)
    try:
        bel = unreal.BlueprintEditorLibrary
        if hasattr(bel, "get_blueprint_parent_class"):
            parent = bel.get_blueprint_parent_class(bp)
            if parent:
                return parent
    except Exception:  # noqa: BLE001
        pass

    # 2) Asset Registry tags
    try:
        path = bp.get_path_name()
        # Strip trailing .AssetName if present
        if "." in path:
            pkg = path.split(".", 1)[0]
        else:
            pkg = path
        ar = unreal.AssetRegistryHelpers.get_asset_registry()
        assets = ar.get_assets_by_package_name(pkg)
        for asset_data in assets or []:
            tag = asset_data.get_tag_value("ParentClass")
            cleaned = _normalize_class_tag(str(tag) if tag else "")
            if cleaned:
                cls = unreal.load_class(None, cleaned)
                if cls:
                    return cls
    except Exception:  # noqa: BLE001
        pass

    return None


def get_blueprint_parent_name(bp: unreal.Blueprint) -> str:
    parent = get_blueprint_parent_class(bp)
    if parent:
        try:
            return parent.get_path_name()
        except Exception:  # noqa: BLE001
            return parent.get_name()

    # Last resort: return cleaned tag string even if load_class failed
    try:
        path = bp.get_path_name()
        pkg = path.split(".", 1)[0] if "." in path else path
        ar = unreal.AssetRegistryHelpers.get_asset_registry()
        for asset_data in ar.get_assets_by_package_name(pkg) or []:
            cleaned = _normalize_class_tag(str(asset_data.get_tag_value("ParentClass") or ""))
            if cleaned:
                return cleaned
    except Exception:  # noqa: BLE001
        pass
    return ""


def create_blueprint_asset(
    folder_path: str,
    asset_name: str,
    parent_class: unreal.Class,
) -> unreal.Blueprint:
    """Create a Blueprint with ParentClass set (UE 5.8)."""
    if not parent_class:
        raise ResolutionError("parent_class is required")

    # Prefer dedicated helper when present
    try:
        bel = unreal.BlueprintEditorLibrary
        if hasattr(bel, "create_blueprint_asset_with_parent"):
            bp = bel.create_blueprint_asset_with_parent(
                f"{folder_path.rstrip('/')}/{asset_name}",
                parent_class,
            )
            if isinstance(bp, unreal.Blueprint):
                compile_blueprint(bp)
                return bp
    except Exception:  # noqa: BLE001
        pass

    factory = unreal.BlueprintFactory()
    set_ok = False
    for prop_name in ("ParentClass", "parent_class"):
        try:
            factory.set_editor_property(prop_name, parent_class)
            set_ok = True
            break
        except Exception:  # noqa: BLE001
            continue
    if not set_ok:
        try:
            factory.parent_class = parent_class  # type: ignore[attr-defined]
            set_ok = True
        except Exception as exc:  # noqa: BLE001
            raise ResolutionError(f"Could not set BlueprintFactory ParentClass: {exc}") from exc

    bp = create_asset(
        folder_path,
        asset_name,
        unreal.Blueprint.static_class(),
        factory,
    )
    if not isinstance(bp, unreal.Blueprint):
        raise ResolutionError(f"create_asset did not return Blueprint: {bp}")

    compile_blueprint(bp)
    return bp
