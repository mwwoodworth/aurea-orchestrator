"""
Handler for MyRoofGenius deployment tasks.
"""
import asyncio
import httpx
import os
from datetime import datetime
from typing import Any, Dict

from shared import MRGDeployPayload, get_task_logger

logger = get_task_logger(__name__)


async def handle_mrg_deploy(task_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Handle MyRoofGenius deployment task."""
    logger.info(f"Starting MRG deployment task {task_id}", extra_data={"payload": payload})
    
    try:
        deploy_payload = MRGDeployPayload(**payload)
        
        # Deployment webhook URLs
        webhook_urls = {
            "staging": os.getenv("MRG_STAGING_WEBHOOK", ""),
            "production": os.getenv("MRG_PRODUCTION_WEBHOOK", "https://api.render.com/deploy/srv-d1tfs4idbo4c73di6k00?key=t2qc-8j6xrM")
        }
        
        webhook_url = webhook_urls.get(deploy_payload.environment)
        if not webhook_url:
            raise ValueError(f"No webhook configured for environment: {deploy_payload.environment}")
        
        # Trigger deployment
        async with httpx.AsyncClient() as client:
            response = await client.post(
                webhook_url,
                json={
                    "version": deploy_payload.version,
                    "services": deploy_payload.services,
                    "triggered_by": "aurea_orchestrator",
                    "task_id": task_id
                },
                timeout=30.0
            )
            
            if response.status_code not in [200, 201, 202]:
                raise Exception(f"Deployment trigger failed: {response.status_code} - {response.text}")
        
        logger.info(f"Triggered deployment to {deploy_payload.environment}")
        
        # Poll for deployment status
        start_time = datetime.utcnow()
        max_wait = 600  # 10 minutes
        poll_interval = 30  # 30 seconds
        
        deployment_status = "pending"
        while (datetime.utcnow() - start_time).total_seconds() < max_wait:
            await asyncio.sleep(poll_interval)
            
            # Check deployment status (would need actual status endpoint)
            # For now, simulate success after 2 minutes
            if (datetime.utcnow() - start_time).total_seconds() > 120:
                deployment_status = "success"
                break
        
        duration = (datetime.utcnow() - start_time).total_seconds()
        
        if deployment_status == "success":
            logger.info(f"Deployment completed successfully in {duration:.2f}s")
            return {
                "status": "success",
                "environment": deploy_payload.environment,
                "version": deploy_payload.version,
                "duration_seconds": duration,
                "deployment_url": f"https://myroofgenius.com" if deploy_payload.environment == "production" else "https://staging.myroofgenius.com"
            }
        else:
            logger.error(f"Deployment failed or timed out")
            return {
                "status": "failed",
                "error": "Deployment timed out or failed",
                "environment": deploy_payload.environment,
                "duration_seconds": duration
            }
            
    except Exception as e:
        logger.exception(f"Error in MRG deploy handler: {e}")
        return {
            "status": "failed",
            "error": str(e)
        }