"""
Handler for webhook processing tasks.
"""
from typing import Any, Dict

from shared import WebhookPayload, get_task_logger

logger = get_task_logger(__name__)


async def handle_webhook_process(task_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Process incoming webhook."""
    logger.info(f"Processing webhook task {task_id}", extra_data={"payload": payload})
    
    try:
        webhook = WebhookPayload(**payload)
        
        # Route based on source
        if webhook.source == "github":
            return await process_github_webhook(webhook)
        elif webhook.source == "clickup":
            return await process_clickup_webhook(webhook)
        elif webhook.source == "make":
            return await process_make_webhook(webhook)
        else:
            logger.warning(f"Unknown webhook source: {webhook.source}")
            return {
                "status": "ignored",
                "reason": f"Unknown source: {webhook.source}"
            }
            
    except Exception as e:
        logger.exception(f"Error processing webhook: {e}")
        return {
            "status": "failed",
            "error": str(e)
        }


async def process_github_webhook(webhook: WebhookPayload) -> Dict[str, Any]:
    """Process GitHub webhook."""
    event_type = webhook.event_type
    data = webhook.data
    
    if event_type == "pull_request":
        action = data.get("action", "")
        pr = data.get("pull_request", {})
        
        logger.info(f"GitHub PR event: {action} for PR #{pr.get('number')}")
        
        # Handle PR events
        if action == "opened":
            # Could trigger code review or tests
            return {
                "status": "processed",
                "action": "pr_opened",
                "pr_number": pr.get("number"),
                "pr_url": pr.get("html_url")
            }
        elif action == "closed" and pr.get("merged"):
            # Could trigger deployment
            return {
                "status": "processed",
                "action": "pr_merged",
                "pr_number": pr.get("number")
            }
    
    elif event_type == "push":
        ref = data.get("ref", "")
        commits = data.get("commits", [])
        
        logger.info(f"GitHub push event to {ref} with {len(commits)} commits")
        
        return {
            "status": "processed",
            "action": "push",
            "ref": ref,
            "commit_count": len(commits)
        }
    
    return {
        "status": "ignored",
        "reason": f"Unhandled GitHub event: {event_type}"
    }


async def process_clickup_webhook(webhook: WebhookPayload) -> Dict[str, Any]:
    """Process ClickUp webhook."""
    event_type = webhook.event_type
    data = webhook.data
    
    logger.info(f"ClickUp event: {event_type}")
    
    # Handle ClickUp task events
    if "task" in event_type.lower():
        task_id = data.get("task_id", "")
        task_name = data.get("name", "")
        
        return {
            "status": "processed",
            "action": "task_event",
            "task_id": task_id,
            "task_name": task_name,
            "event": event_type
        }
    
    return {
        "status": "ignored",
        "reason": f"Unhandled ClickUp event: {event_type}"
    }


async def process_make_webhook(webhook: WebhookPayload) -> Dict[str, Any]:
    """Process Make.com webhook."""
    event_type = webhook.event_type
    data = webhook.data
    
    logger.info(f"Make.com event: {event_type}")
    
    # Process Make.com automation triggers
    scenario_id = data.get("scenario_id", "")
    execution_id = data.get("execution_id", "")
    
    return {
        "status": "processed",
        "action": "automation_trigger",
        "scenario_id": scenario_id,
        "execution_id": execution_id,
        "event": event_type
    }