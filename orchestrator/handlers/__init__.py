"""
Task handlers for AUREA orchestrator.
"""
from orchestrator.handlers.aurea_action import handle_aurea_action
from orchestrator.handlers.centerpoint_sync import handle_centerpoint_sync
from orchestrator.handlers.code_pr import handle_code_pr
from orchestrator.handlers.gen_content import handle_gen_content
from orchestrator.handlers.maintenance import handle_maintenance
from orchestrator.handlers.mrg_deploy import handle_mrg_deploy
from orchestrator.handlers.webhook_process import handle_webhook_process

# Handler registry
HANDLERS = {
    "code_pr": handle_code_pr,
    "centerpoint_sync": handle_centerpoint_sync,
    "mrg_deploy": handle_mrg_deploy,
    "gen_content": handle_gen_content,
    "aurea_action": handle_aurea_action,
    "webhook_process": handle_webhook_process,
    "maintenance": handle_maintenance,
}

__all__ = [
    "HANDLERS",
    "handle_code_pr",
    "handle_centerpoint_sync",
    "handle_mrg_deploy",
    "handle_gen_content",
    "handle_aurea_action",
    "handle_webhook_process",
    "handle_maintenance",
]