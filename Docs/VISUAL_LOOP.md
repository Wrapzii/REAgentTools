# Agent visual loop — what exists vs what RECapture adds

**Date:** 2026-07-23 · REAgentTools **v1.2.0**

## Verdict (probe)

| Capability | Status | Action |
|------------|--------|--------|
| `EditorToolset.LogsToolset.GetLogEntries` | ✅ Works | Use for `[REAbility]` / cast logs — **do not rebuild** |
| `LiveCodingToolset.CompileLiveCoding` | ✅ Works | Body-only C++ without close→rebuild→relaunch — **do not rebuild** |
| `SlateInspectorToolset.PressKey` | ✅ Works (Slate) | Can simulate keys; PIE gameplay still may need `pie_cast_and_capture` |
| `EditorAppToolset.CaptureViewport` | ✅ Exists, ❌ agent-hostile | Returns **inline base64** (65k–796k+ chars); optionals often required empty |
| Thumbnail / Content Browser for Additive Unlit FX | ❌ Blind | Circles look black — need lit param preview |

## Use these Epic tools (no new code)

```
call_tool(LogsToolset, GetLogEntries, {pattern: "[REAbility]", maxEntries: 25})
call_tool(LiveCodingToolset, CompileLiveCoding, {})
call_tool(SlateInspectorToolset, PressKey, {…})   # Q / confirm schema from map
```

Capability matrix “log-tail 🚫” was **stale**.

## RECaptureWorkflowTools (built)

| Tool | Purpose |
|------|---------|
| `capture_viewport_to_disk` | HighResShot → `Saved/Screenshots` → optional downscale / JPEG → **path only** |
| `render_material_preview_to_disk` | Temp plane + light + DMI param overrides (ParticleColor defaults) → path |
| `pie_cast_and_capture` | PIE → `CastAbility(ability_id)` → delay → disk shot |
| `get_recent_log_entries_compact` | Saved/Logs fallback if LogsToolset unavailable |
| `visual_loop_tool_notes` | This map as a tool |

MCP toolset: `re_agent_tools.toolsets.capture_workflow_tools.RECaptureWorkflowTools`

After plugin update: `ModelContextProtocol.RefreshTools` or `reload_workflow_modules`.

## Project helper (when Epic CaptureViewport is the only reliable grab)

```
python Content/Python/mcp_capture_viewport_to_disk.py --name proof --max 1280 --jpeg 85
python Content/Python/mcp_capture_viewport_to_disk.py --cam Cam_DunLD_Prop_Hero
```

Always sends empty `captureTransform` + `annotations` so Epic does not reject the call. Returns JSON with `path` / `width` / `height` / `bytes`.

## Preferred order for visual QA

1. Logs (`GetLogEntries`) when pixels are unnecessary  
2. `RECapture.capture_viewport_to_disk` or `mcp_capture_viewport_to_disk.py`  
3. `render_material_preview_to_disk` for EdgeSharpness / Additive MI tuning  
4. `pie_cast_and_capture` for full bolt→impact→circle loop  
5. Never paste base64 / full-monitor `CaptureEditorImage` into chat  

## Not building

- Duplicate Logs / LiveCoding / Slate toolsets  
- Returning CaptureViewport base64 from RE composites  
- Full GAS ability-graph authoring (still out of scope)  
