"""Register all RE Agent workflow toolsets."""

from toolset_registry.registration import Registration

from re_agent_tools.toolsets.actor_workflow_tools import REActorWorkflowTools
from re_agent_tools.toolsets.anim_workflow_tools import REAnimWorkflowTools
from re_agent_tools.toolsets.asset_workflow_tools import REAssetWorkflowTools
from re_agent_tools.toolsets.batch_workflow_tools import REBatchWorkflowTools
from re_agent_tools.toolsets.blueprint_workflow_tools import REBlueprintWorkflowTools
from re_agent_tools.toolsets.context_tools import REContextTools
from re_agent_tools.toolsets.level_workflow_tools import RELevelWorkflowTools
from re_agent_tools.toolsets.material_workflow_tools import REMaterialWorkflowTools
from re_agent_tools.toolsets.project_workflow_tools import REProjectWorkflowTools
from re_agent_tools.toolsets.validation_workflow_tools import REValidationWorkflowTools

_registration = Registration([
    REContextTools,
    REActorWorkflowTools,
    REAnimWorkflowTools,
    REAssetWorkflowTools,
    REBlueprintWorkflowTools,
    REMaterialWorkflowTools,
    RELevelWorkflowTools,
    REValidationWorkflowTools,
    REBatchWorkflowTools,
    REProjectWorkflowTools,
])
