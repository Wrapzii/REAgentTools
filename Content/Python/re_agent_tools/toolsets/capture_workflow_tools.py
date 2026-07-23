"""Budget-safe capture composites — write PNG/JPEG to disk, return path only (no base64)."""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import unreal
import toolset_registry

from re_agent_tools.common.logging import log_tool_call
from re_agent_tools.common.results import WorkflowTimer, error_result, make_request_id, workflow_result
from re_agent_tools.common.serialization import parse_json
from re_agent_tools.common.spawn_helpers import spawn_actor_from_class
from re_agent_tools.common.transactions import scoped_transaction
from re_agent_tools.common.validation import is_pie_active


def _project_root() -> Path:
    return Path(unreal.Paths.project_dir()).resolve()


def _shot_dir() -> Path:
    d = _project_root() / "Saved" / "Screenshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _norm_path(path: Path) -> str:
    """Prefer project-relative Saved/... path; else absolute posix."""
    try:
        resolved = path.resolve()
        root = _project_root().resolve()
        try:
            return str(resolved.relative_to(root)).replace("\\", "/")
        except ValueError:
            return str(resolved).replace("\\", "/")
    except Exception:
        return str(path).replace("\\", "/")


def _downscale_with_pil(path: Path, max_dimension: int, jpeg_quality: int) -> dict:
    from PIL import Image  # type: ignore

    im = Image.open(path)
    w, h = im.size
    longest = max(w, h)
    if max_dimension > 0 and longest > max_dimension:
        scale = max_dimension / float(longest)
        im = im.resize((max(1, int(w * scale)), max(1, int(h * scale))), Image.Resampling.LANCZOS)
    out = path
    if jpeg_quality > 0:
        out = path.with_suffix(".jpg")
        im.convert("RGB").save(out, "JPEG", quality=int(jpeg_quality), optimize=True)
        if out != path and path.exists():
            try:
                path.unlink()
            except Exception:
                pass
    else:
        im.save(out)
    return {
        "path": _norm_path(out),
        "width": im.size[0],
        "height": im.size[1],
        "bytes": out.stat().st_size,
    }


def _downscale(path: Path, max_dimension: int, jpeg_quality: int) -> dict:
    """Downscale in place or to .jpg; returns {path, width, height, bytes}."""
    info = {
        "path": _norm_path(path),
        "width": None,
        "height": None,
        "bytes": path.stat().st_size if path.exists() else 0,
    }
    if (max_dimension <= 0 and jpeg_quality <= 0) or not path.exists():
        return info
    try:
        return _downscale_with_pil(path, max_dimension, jpeg_quality)
    except Exception:
        pass
    # Editor Python often has no PIL — shell out to system Python if present
    try:
        import subprocess
        import sys

        script = (
            "from PIL import Image; import sys; "
            "p,m,j=sys.argv[1],int(sys.argv[2]),int(sys.argv[3]); "
            "im=Image.open(p); w,h=im.size; "
            "m=m if m>0 else max(w,h); "
            "s=m/float(max(w,h)) if max(w,h)>m else 1.0; "
            "im=im.resize((max(1,int(w*s)),max(1,int(h*s))), Image.Resampling.LANCZOS) if s<1 else im; "
            "out=p; "
            "import os; "
            "out=(os.path.splitext(p)[0]+'.jpg') if j>0 else p; "
            "im.convert('RGB').save(out,'JPEG',quality=j,optimize=True) if j>0 else im.save(out); "
            "print(out); print(im.size[0]); print(im.size[1])"
        )
        candidates = [
            sys.executable,
            r"C:\Program Files\Python39\python.exe",
            r"C:\Program Files\Python311\python.exe",
            r"C:\Program Files\Python312\python.exe",
            "python",
        ]
        for py in candidates:
            try:
                proc = subprocess.run(
                    [py, "-c", script, str(path), str(int(max_dimension)), str(int(jpeg_quality))],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                if proc.returncode != 0:
                    continue
                lines = [ln.strip() for ln in (proc.stdout or "").splitlines() if ln.strip()]
                if len(lines) >= 3:
                    out = Path(lines[0])
                    return {
                        "path": _norm_path(out),
                        "width": int(lines[1]),
                        "height": int(lines[2]),
                        "bytes": out.stat().st_size if out.exists() else 0,
                    }
            except Exception:
                continue
        info["warning"] = "PIL unavailable in editor + system Python — kept original"
        return info
    except Exception as exc:
        info["warning"] = f"downscale failed: {exc}"
        return info


def _wait_for_file(path: Path, timeout_s: float = 8.0) -> bool:
    t0 = time.time()
    last = -1
    stable = 0
    while time.time() - t0 < timeout_s:
        if path.exists():
            sz = path.stat().st_size
            if sz > 0 and sz == last:
                stable += 1
                if stable >= 2:
                    return True
            else:
                stable = 0
            last = sz
        time.sleep(0.15)
    return path.exists() and path.stat().st_size > 0


def _newest_screenshot(after_mtime: float) -> Path | None:
    shot = _shot_dir()
    newest = None
    newest_m = after_mtime
    for p in shot.glob("*.png"):
        try:
            m = p.stat().st_mtime
        except Exception:
            continue
        if m > newest_m and (newest is None or m > newest.stat().st_mtime):
            newest = p
            newest_m = m
    return newest


def _highresshot(path: Path, width: int, height: int) -> list[str]:
    """Try several HighResShot filename forms; return attempt notes."""
    notes: list[str] = []
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            path.unlink()
        except Exception:
            pass
    attempts = [
        f'HighResShot {int(width)}x{int(height)} filename="{path.as_posix()}"',
        f"HighResShot {int(width)}x{int(height)} filename={path.as_posix()}",
        f'HighResShot {int(width)}x{int(height)} filename="{path.name}"',
        f"HighResShot {int(width)}x{int(height)}",
    ]
    for cmd in attempts:
        try:
            unreal.SystemLibrary.execute_console_command(None, cmd)
            notes.append(cmd.split(" filename")[0] if " filename" in cmd else cmd)
            if _wait_for_file(path, timeout_s=2.5):
                return notes
            # UE may write under Saved/Screenshots with a generated name
            found = _newest_screenshot(time.time() - 8.0)
            if found and found.resolve() != path.resolve():
                try:
                    found.replace(path)
                    notes.append(f"renamed {found.name} → {path.name}")
                    return notes
                except Exception as exc:
                    notes.append(f"rename failed: {exc}")
        except Exception as exc:
            notes.append(f"cmd failed: {exc}")
    return notes


def _scenecapture_to_disk(dest: Path, width: int, height: int) -> tuple[bool, list[str]]:
    """Reliable editor capture via SceneCapture2D + export_render_target (proven in lookdev_scenecapture_shot)."""
    warnings: list[str] = []
    eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    created: list = []
    try:
        try:
            loc, rot = unreal.EditorLevelLibrary.get_level_viewport_camera_info()
        except Exception:
            loc, rot = unreal.Vector(0, 0, 200), unreal.Rotator(-20, 0, 0)

        rt = None
        try:
            world = unreal.EditorLevelLibrary.get_editor_world()
            rt = unreal.RenderingLibrary.create_render_target2d(
                world, int(width), int(height), unreal.TextureRenderTargetFormat.RTF_RGBA8
            )
        except Exception as exc:
            warnings.append(f"create_render_target2d: {exc}")
            return False, warnings
        if not rt:
            return False, warnings

        for a in list(eas.get_all_level_actors() or []):
            try:
                if (a.get_actor_label() or "") == "RE_Capture_SceneCapture":
                    eas.destroy_actor(a)
            except Exception:
                pass

        cap_actor = eas.spawn_actor_from_class(unreal.SceneCapture2D.static_class(), loc)
        if not cap_actor:
            return False, ["spawn SceneCapture2D failed"]
        created.append(cap_actor)
        cap_actor.set_actor_label("RE_Capture_SceneCapture")
        cap_actor.set_actor_location(loc, False, True)
        cap_actor.set_actor_rotation(rot, False)

        sc = cap_actor.get_component_by_class(unreal.SceneCaptureComponent2D)
        if sc is None:
            sc = getattr(cap_actor, "capture_component2d", None)
        if not sc:
            return False, ["SceneCaptureComponent2D missing"]
        sc.set_editor_property("texture_target", rt)
        try:
            sc.set_editor_property("capture_source", unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR)
        except Exception:
            pass
        try:
            sc.capture_scene()
        except Exception as exc:
            return False, [f"capture_scene: {exc}"]

        time.sleep(0.35)
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            try:
                dest.unlink()
            except Exception:
                pass
        ok = False
        try:
            world = unreal.EditorLevelLibrary.get_editor_world()
            ok = bool(
                unreal.RenderingLibrary.export_render_target(world, rt, str(dest.parent), dest.name)
            )
        except Exception as exc:
            warnings.append(f"export_render_target: {exc}")
        if not ok or not dest.exists():
            # export may write with slight name variance
            found = _newest_screenshot(time.time() - 5.0)
            if found and found.stat().st_size > 500:
                if found.resolve() != dest.resolve():
                    found.replace(dest)
                ok = dest.exists()
        if ok and dest.exists() and dest.stat().st_size > 500:
            warnings.append("used SceneCapture2D → export_render_target")
            return True, warnings
        return False, warnings
    except Exception as exc:
        warnings.append(f"scenecapture: {exc}")
        return False, warnings
    finally:
        try:
            if created:
                eas.destroy_actors(created)
        except Exception:
            try:
                for a in created:
                    eas.destroy_actor(a)
            except Exception:
                pass


def _capture_to_disk(
    dest: Path,
    width: int,
    height: int,
    max_dimension: int,
    jpeg_quality: int,
) -> tuple[dict, list[str]]:
    warnings: list[str] = []
    # Prefer SceneCapture (HighResShot/Automation often write nothing on UE 5.8)
    ok, sc_warns = _scenecapture_to_disk(dest, width, height)
    warnings.extend(sc_warns)
    notes: list[str] = []
    if not ok:
        t_before = time.time()
        notes = _highresshot(dest, width, height)
        ok = dest.exists() and dest.stat().st_size > 0
        if not ok:
            found = _newest_screenshot(t_before - 1.0)
            if found:
                try:
                    if found.resolve() != dest.resolve():
                        found.replace(dest)
                    ok = dest.exists() and dest.stat().st_size > 0
                    warnings.append(f"picked newest screenshot {dest.name}")
                except Exception as exc:
                    warnings.append(f"newest pick failed: {exc}")
    if not ok:
        try:
            stem = dest.stem
            unreal.AutomationLibrary.take_high_res_screenshot(width, height, stem, None, False, False)
            ok = _wait_for_file(dest, timeout_s=4.0)
            if not ok:
                cand = _shot_dir() / f"{stem}.png"
                if cand.exists():
                    if cand.resolve() != dest.resolve():
                        cand.replace(dest)
                    ok = True
            if ok:
                warnings.append("used AutomationLibrary.take_high_res_screenshot fallback")
        except Exception as exc:
            warnings.append(f"automation fallback: {exc}")
    if not ok or not dest.exists():
        raise RuntimeError(
            "No screenshot file written. Prefer Content/Python/mcp_capture_viewport_to_disk.py "
            f"(MCP CaptureViewport → path). attempts={notes[:3]}"
        )
    info = _downscale(dest, int(max_dimension), int(jpeg_quality))
    if info.get("warning"):
        warnings.append(str(info["warning"]))
    return info, warnings


def _pie_world():
    get_pie = getattr(unreal.EditorLevelLibrary, "editor_get_pie_worlds", None)
    if callable(get_pie):
        try:
            worlds = get_pie()
            if worlds:
                return worlds[0]
        except Exception:
            pass
    try:
        gw = getattr(unreal.EditorLevelLibrary, "editor_get_game_world", None)
        if callable(gw):
            w = gw()
            if w:
                return w
    except Exception:
        pass
    return None


def _ensure_pie(start_if_needed: bool) -> tuple[object | None, list[str]]:
    warnings: list[str] = []
    world = _pie_world()
    if world and unreal.GameplayStatics.get_player_pawn(world, 0):
        return world, warnings
    if not start_if_needed:
        return world, warnings
    try:
        les = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
        if hasattr(les, "editor_request_play_session"):
            les.editor_request_play_session()
            warnings.append("started PIE via LevelEditorSubsystem.editor_request_play_session")
        elif hasattr(les, "editor_play_session"):
            les.editor_play_session()
            warnings.append("started PIE via LevelEditorSubsystem.editor_play_session")
        else:
            raise RuntimeError("no LevelEditorSubsystem play API")
    except Exception as exc:
        try:
            unreal.EditorLevelLibrary.editor_play_in_editor()
            warnings.append(f"started PIE via EditorLevelLibrary.editor_play_in_editor ({exc})")
        except Exception as exc2:
            warnings.append(f"PIE start failed: {exc} | {exc2}")
            return None, warnings
    for _ in range(50):
        time.sleep(0.3)
        world = _pie_world()
        if world and unreal.GameplayStatics.get_player_pawn(world, 0):
            return world, warnings
    warnings.append("PIE start timed out waiting for player pawn")
    return _pie_world(), warnings


def _resolve_visual_combat(pawn):
    # Prefer typed class when RE module is loaded; fall back to name scan.
    try:
        cls = unreal.REPlayerVisualCombatComponent
        vc = pawn.get_component_by_class(cls)
        if vc:
            return vc
    except Exception:
        pass
    for comp in pawn.get_components_by_class(unreal.ActorComponent) or []:
        try:
            name = str(comp.get_name())
            cname = str(comp.get_class().get_name()) if comp.get_class() else ""
            if "REPlayerVisualCombat" in cname or name in ("REVisualCombat", "REPlayerVisualCombat"):
                return comp
        except Exception:
            continue
    return None


def _apply_aim(pc, aim_values: list) -> str | None:
    if not pc or not aim_values or len(aim_values) < 3:
        return None
    a0, a1, a2 = float(aim_values[0]), float(aim_values[1]), float(aim_values[2])
    if max(abs(a0), abs(a1), abs(a2)) > 2.0:
        # Treat as pitch/yaw/roll degrees
        rot = unreal.Rotator(a0, a1, a2)
        pc.set_control_rotation(rot)
        return "rotator_degrees"
    vec = unreal.Vector(a0, a1, a2)
    if vec.length() < 1e-4:
        return None
    rot = vec.get_safe_normal().rotation()
    pc.set_control_rotation(rot)
    return "direction_vector"


def _apply_material_params(mat_or_dmi, params: dict) -> list[str]:
    applied: list[str] = []
    for k, v in params.items():
        try:
            if isinstance(v, (list, tuple)) and len(v) >= 3:
                color = unreal.LinearColor(
                    float(v[0]),
                    float(v[1]),
                    float(v[2]),
                    float(v[3]) if len(v) > 3 else 1.0,
                )
                if hasattr(mat_or_dmi, "set_vector_parameter_value"):
                    mat_or_dmi.set_vector_parameter_value(unreal.Name(str(k)), color)
                else:
                    unreal.MaterialEditingLibrary.set_material_instance_vector_parameter_value(
                        mat_or_dmi, str(k), color
                    )
            else:
                if hasattr(mat_or_dmi, "set_scalar_parameter_value"):
                    mat_or_dmi.set_scalar_parameter_value(unreal.Name(str(k)), float(v))
                else:
                    unreal.MaterialEditingLibrary.set_material_instance_scalar_parameter_value(
                        mat_or_dmi, str(k), float(v)
                    )
            applied.append(str(k))
        except Exception:
            continue
    return applied


@unreal.uclass()
class RECaptureWorkflowTools(unreal.ToolsetDefinition):
    """RE composite capture: disk path + optional downscale/JPEG — never return base64."""

    @toolset_registry.tool_call
    @staticmethod
    def capture_viewport_to_disk(
        filename: str = "",
        max_dimension: int = 1280,
        jpeg_quality: int = 0,
        width: int = 1280,
        height: int = 720,
    ) -> str:
        """Capture level viewport to Saved/Screenshots and return path/size only.

        jpeg_quality: 0 = keep PNG; 1–100 = write JPEG and delete PNG.
        max_dimension: downscale longest edge after capture (0 = skip).
        """
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            name = filename.strip() if filename else f"re_capture_{stamp}.png"
            if not name.lower().endswith((".png", ".jpg", ".jpeg")):
                name += ".png"
            dest = _shot_dir() / name
            cap_w = max(int(width), 640)
            cap_h = max(int(height), 360)
            if max_dimension > 0:
                cap_w = min(cap_w, max(int(max_dimension), 640))
                cap_h = min(cap_h, max(int(max_dimension * 9 / 16), 360))
            info, warnings = _capture_to_disk(dest, cap_w, cap_h, int(max_dimension), int(jpeg_quality))
            result = workflow_result(
                "capture_viewport_to_disk",
                True,
                f"Wrote {info['path']}",
                request_id=request_id,
                created=[info["path"]],
                warnings=warnings,
                extra={
                    "screenshot": info["path"],
                    "width": info.get("width"),
                    "height": info.get("height"),
                    "bytes": info.get("bytes"),
                    "max_dimension": max_dimension,
                },
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call(
                "capture_viewport_to_disk", success=True, request_id=request_id, duration_ms=timer.elapsed_ms
            )
            return result
        except Exception as exc:  # noqa: BLE001
            return error_result(
                "capture_viewport_to_disk", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms
            )

    @toolset_registry.tool_call
    @staticmethod
    def render_material_preview_to_disk(
        material_path: str,
        filename: str = "",
        parameters_json: str = "{}",
        max_dimension: int = 1280,
        jpeg_quality: int = 85,
    ) -> str:
        """Lit preview of a Material/MI on a plane (forces readable Additive/Unlit FX mats).

        Spawns temp plane + light, assigns a dynamic MI with param overrides (ParticleColor etc.),
        captures to disk, cleans up. Does not permanently mutate shared assets.
        """
        timer = WorkflowTimer()
        request_id = make_request_id()
        created_actors: list = []
        try:
            mat = unreal.load_asset(material_path)
            if not mat:
                raise ValueError(f"Material not found: {material_path}")
            params = parse_json(parameters_json, field_name="parameters_json")
            if not isinstance(params, dict):
                params = {}
            # Defaults so Additive/Unlit ParticleColor mats are not pure black
            merged = {
                "ParticleColor": [1.0, 0.55, 0.2, 1.0],
                "ElementColor": [1.0, 0.45, 0.12, 1.0],
                "Intensity": 2.0,
                "EdgeSharpness": 2.0,
            }
            merged.update(params)

            with scoped_transaction("RE material preview"):
                plane_cls = unreal.load_class(None, "/Script/Engine.StaticMeshActor")
                plane = spawn_actor_from_class(plane_cls, unreal.Vector(0, 0, 100))
                plane.set_actor_label("RE_MatPreview_Plane")
                created_actors.append(plane)
                sm = unreal.load_asset("/Engine/BasicShapes/Plane")
                smc = getattr(plane, "static_mesh_component", None)
                applied: list[str] = []
                if sm and smc:
                    smc.set_static_mesh(sm)
                    dmi = None
                    try:
                        dmi = smc.create_dynamic_material_instance(0, mat)
                    except Exception:
                        smc.set_material(0, mat)
                        dmi = smc.get_material(0)
                    if dmi:
                        applied = _apply_material_params(dmi, merged)
                    plane.set_actor_scale3d(unreal.Vector(2, 2, 2))

                light_cls = unreal.load_class(None, "/Script/Engine.DirectionalLight")
                light = spawn_actor_from_class(light_cls, unreal.Vector(200, 200, 400))
                light.set_actor_label("RE_MatPreview_Light")
                created_actors.append(light)

                try:
                    unreal.EditorLevelLibrary.set_level_viewport_camera_info(
                        unreal.Vector(250, 0, 180),
                        unreal.Rotator(-15, 180, 0),
                    )
                except Exception:
                    pass

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            name = filename.strip() if filename else f"re_matprev_{stamp}.png"
            if not name.lower().endswith((".png", ".jpg", ".jpeg")):
                name += ".png"
            dest = _shot_dir() / name
            info, warnings = _capture_to_disk(dest, 1280, 720, int(max_dimension), int(jpeg_quality))

            try:
                eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
                eas.destroy_actors(created_actors)
            except Exception:
                pass

            result = workflow_result(
                "render_material_preview_to_disk",
                True,
                f"Material preview → {info['path']}",
                request_id=request_id,
                created=[info["path"]],
                warnings=warnings,
                extra={
                    "screenshot": info["path"],
                    "material": material_path,
                    "params_applied": applied,
                    "width": info.get("width"),
                    "height": info.get("height"),
                    "bytes": info.get("bytes"),
                    "note": "Additive/Unlit FX mats need ParticleColor/ElementColor overrides — defaults applied when omitted",
                },
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call(
                "render_material_preview_to_disk",
                success=True,
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )
            return result
        except Exception as exc:  # noqa: BLE001
            try:
                eas = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
                eas.destroy_actors(created_actors)
            except Exception:
                pass
            return error_result(
                "render_material_preview_to_disk",
                str(exc),
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )

    @toolset_registry.tool_call
    @staticmethod
    def get_recent_log_entries_compact(pattern: str = "RE", max_entries: int = 25) -> str:
        """Compact log tail via Saved/Logs/*.log (fallback when LogsToolset unavailable).

        Prefer Epic `EditorToolset.LogsToolset.GetLogEntries` when MCP exposes it.
        """
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            log_dir = _project_root() / "Saved" / "Logs"
            logs = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
            if not logs:
                return error_result(
                    "get_recent_log_entries_compact",
                    "No Saved/Logs/*.log — try EditorToolset.LogsToolset.GetLogEntries",
                    request_id=request_id,
                    duration_ms=timer.elapsed_ms,
                )
            text = logs[0].read_text(encoding="utf-8", errors="replace").splitlines()
            pat = (pattern or "").lower()
            matched = [ln for ln in text if (not pat or pat in ln.lower())]
            matched = matched[-max(1, min(int(max_entries), 100)) :]
            result = workflow_result(
                "get_recent_log_entries_compact",
                True,
                f"{len(matched)} log lines",
                request_id=request_id,
                extra={
                    "log_file": str(logs[0]).replace("\\", "/"),
                    "entries": matched,
                    "pattern": pattern,
                    "prefer_epic": "EditorToolset.LogsToolset.GetLogEntries",
                },
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call(
                "get_recent_log_entries_compact",
                success=True,
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )
            return result
        except Exception as exc:  # noqa: BLE001
            return error_result(
                "get_recent_log_entries_compact",
                str(exc),
                request_id=request_id,
                duration_ms=timer.elapsed_ms,
            )

    @toolset_registry.tool_call
    @staticmethod
    def pie_cast_and_capture(
        ability_id: str,
        aim_dir_json: str = "",
        capture_delay_ms: int = 400,
        max_dimension: int = 1280,
        jpeg_quality: int = 85,
        filename: str = "",
        start_pie_if_needed: bool = False,
    ) -> str:
        """PIE: CastAbility on player REVisualCombat, wait, capture framed shot to disk.

        aim_dir_json: optional JSON [pitch,yaw,roll] degrees OR unit direction [x,y,z].
        Requires PIE (or start_pie_if_needed=true). Returns screenshot path only.
        """
        timer = WorkflowTimer()
        request_id = make_request_id()
        try:
            aid = (ability_id or "").strip()
            if not aid:
                raise ValueError("ability_id required (DT_Abilities row name)")

            world, warnings = _ensure_pie(bool(start_pie_if_needed))
            if not world:
                return error_result(
                    "pie_cast_and_capture",
                    "No PIE world — start Play-In-Editor first, or pass start_pie_if_needed=true",
                    request_id=request_id,
                    duration_ms=timer.elapsed_ms,
                    extra={
                        "warnings": warnings,
                        "use_instead": {
                            "start_pie": "EditorToolset / Play tools StartPIE",
                            "logs": "EditorToolset.LogsToolset.GetLogEntries",
                            "input": "SlateInspectorToolset.PressKey",
                            "compile": "LiveCodingToolset.CompileLiveCoding",
                        },
                    },
                )

            pawn = unreal.GameplayStatics.get_player_pawn(world, 0)
            if not pawn:
                return error_result(
                    "pie_cast_and_capture",
                    "PIE world has no player pawn",
                    request_id=request_id,
                    duration_ms=timer.elapsed_ms,
                    extra={"warnings": warnings},
                )

            pc = unreal.GameplayStatics.get_player_controller(world, 0)
            aim_mode = None
            if aim_dir_json and aim_dir_json.strip() not in ("", "{}", "[]"):
                aim = parse_json(aim_dir_json, field_name="aim_dir_json")
                if isinstance(aim, list):
                    aim_mode = _apply_aim(pc, aim)

            vc = _resolve_visual_combat(pawn)
            if not vc:
                return error_result(
                    "pie_cast_and_capture",
                    "REPlayerVisualCombatComponent / REVisualCombat not found on pawn",
                    request_id=request_id,
                    duration_ms=timer.elapsed_ms,
                    extra={"warnings": warnings, "pawn": str(pawn.get_name())},
                )

            cast_fn = getattr(vc, "cast_ability", None) or getattr(vc, "CastAbility", None)
            if not callable(cast_fn):
                return error_result(
                    "pie_cast_and_capture",
                    "cast_ability not callable on visual combat component (rebuild RE module?)",
                    request_id=request_id,
                    duration_ms=timer.elapsed_ms,
                    extra={"warnings": warnings},
                )

            cast_fn(unreal.Name(aid))
            delay_s = max(0.0, float(capture_delay_ms) / 1000.0)
            if delay_s > 0:
                time.sleep(delay_s)

            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_id = "".join(c if c.isalnum() or c in "-_" else "_" for c in aid)
            name = filename.strip() if filename else f"re_pie_cast_{safe_id}_{stamp}.png"
            if not name.lower().endswith((".png", ".jpg", ".jpeg")):
                name += ".png"
            dest = _shot_dir() / name
            info, cap_warns = _capture_to_disk(
                dest, 1280, 720, int(max_dimension), int(jpeg_quality)
            )
            warnings.extend(cap_warns)

            result = workflow_result(
                "pie_cast_and_capture",
                True,
                f"Cast {aid} → {info['path']}",
                request_id=request_id,
                created=[info["path"]],
                warnings=warnings,
                extra={
                    "ability_id": aid,
                    "pawn": str(pawn.get_name()),
                    "aim_mode": aim_mode,
                    "capture_delay_ms": capture_delay_ms,
                    "pie_active": is_pie_active(),
                    "screenshot": info["path"],
                    "width": info.get("width"),
                    "height": info.get("height"),
                    "bytes": info.get("bytes"),
                },
                duration_ms=timer.elapsed_ms,
            )
            log_tool_call(
                "pie_cast_and_capture", success=True, request_id=request_id, duration_ms=timer.elapsed_ms
            )
            return result
        except Exception as exc:  # noqa: BLE001
            return error_result(
                "pie_cast_and_capture", str(exc), request_id=request_id, duration_ms=timer.elapsed_ms
            )

    @toolset_registry.tool_call
    @staticmethod
    def visual_loop_tool_notes() -> str:
        """What to use for agent visual QA: Epic tools that already work vs RECapture composites."""
        timer = WorkflowTimer()
        request_id = make_request_id()
        result = workflow_result(
            "visual_loop_tool_notes",
            True,
            "Use Epic Logs/LiveCoding/Slate; RECapture for path-only shots",
            request_id=request_id,
            extra={
                "already_exists_use_these": {
                    "logs": {
                        "toolset": "EditorToolset.LogsToolset",
                        "tool": "GetLogEntries",
                        "args": {"pattern": "[REAbility]", "maxEntries": 25},
                        "status": "works — matrix log-tail 🚫 was stale",
                    },
                    "live_coding": {
                        "toolset": "LiveCodingToolset.LiveCodingToolset",
                        "tool": "CompileLiveCoding",
                        "status": "works — body-only C++ without editor restart",
                    },
                    "slate_input": {
                        "toolset": "SlateInspectorToolset.SlateInspectorToolset",
                        "tool": "PressKey",
                        "status": "works for editor/Slate; PIE gameplay input may still need cast shim",
                    },
                },
                "build_with_RECapture": [
                    "capture_viewport_to_disk",
                    "render_material_preview_to_disk",
                    "pie_cast_and_capture",
                ],
                "epic_capture_pain": {
                    "tool": "EditorToolset.EditorAppToolset.CaptureViewport",
                    "issue": "returns inline base64 (huge); needs empty captureTransform+annotations in practice",
                    "mitigation": "RECapture path-only OR Content/Python/mcp_capture_viewport_to_disk.py",
                },
                "do_not_rebuild": ["LogsToolset", "LiveCodingToolset", "SlateInspectorToolset"],
            },
            duration_ms=timer.elapsed_ms,
        )
        log_tool_call("visual_loop_tool_notes", success=True, request_id=request_id, duration_ms=timer.elapsed_ms)
        return result
