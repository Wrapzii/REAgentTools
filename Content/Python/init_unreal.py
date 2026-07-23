# REAgentTools — register composite workflow toolsets with ToolsetRegistry.
import unreal

try:
    from re_agent_tools import toolsets

    if toolsets._registration.register():
        unreal.log("[REAgentTools] Workflow toolsets registered")
    else:
        unreal.log_warning(
            "[REAgentTools] ToolsetRegistry not available — enable ModelContextProtocol"
        )
except Exception as exc:  # noqa: BLE001
    unreal.log_error(f"[REAgentTools] Registration failed: {exc}")
