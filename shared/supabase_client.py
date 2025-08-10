"""
Supabase client for persistence and storage.
"""
import os
from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

import httpx
from httpx import AsyncClient

from shared.logging import get_logger
from shared.schemas import RunRecord, Task, TaskStatus

logger = get_logger(__name__)


class SupabaseClient:
    """Async Supabase client for database and storage operations."""

    def __init__(self, url: str, service_role_key: str):
        self.url = url
        self.service_role_key = service_role_key
        self.headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        }
        self.client: Optional[AsyncClient] = None

    async def connect(self) -> None:
        """Initialize HTTP client."""
        self.client = httpx.AsyncClient(
            base_url=self.url,
            headers=self.headers,
            timeout=30.0
        )
        logger.info("Connected to Supabase")

    async def disconnect(self) -> None:
        """Close HTTP client."""
        if self.client:
            await self.client.aclose()
            logger.info("Disconnected from Supabase")

    async def create_task(self, task: Task) -> Dict[str, Any]:
        """Create task in database."""
        try:
            task_data = {
                "id": str(task.id),
                "type": task.type,
                "payload": task.payload,
                "priority": task.priority,
                "status": task.status,
                "enqueued_at": task.enqueued_at.isoformat(),
                "trace_id": task.trace_id,
                "idempotency_key": task.idempotency_key,
                "metadata": task.metadata
            }
            
            response = await self.client.post(
                "/rest/v1/orchestrator_tasks",
                json=task_data
            )
            
            if response.status_code == 201:
                logger.info(f"Created task {task.id} in database")
                return response.json()[0] if response.json() else task_data
            else:
                logger.error(f"Failed to create task: {response.text}")
                raise Exception(f"Failed to create task: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Error creating task {task.id}: {e}")
            raise

    async def get_task(self, task_id: UUID) -> Optional[Dict[str, Any]]:
        """Get task from database."""
        try:
            response = await self.client.get(
                f"/rest/v1/orchestrator_tasks",
                params={"id": f"eq.{task_id}", "select": "*"}
            )
            
            if response.status_code == 200:
                data = response.json()
                return data[0] if data else None
            else:
                logger.error(f"Failed to get task: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error getting task {task_id}: {e}")
            return None

    async def update_task_status(
        self,
        task_id: UUID,
        status: TaskStatus,
        error: Optional[str] = None,
        completed_at: Optional[datetime] = None
    ) -> bool:
        """Update task status in database."""
        try:
            updates = {"status": status}
            if error:
                updates["last_error"] = error
            if completed_at:
                updates["completed_at"] = completed_at.isoformat()
            if status == TaskStatus.RUNNING:
                updates["started_at"] = datetime.utcnow().isoformat()
                
            response = await self.client.patch(
                f"/rest/v1/orchestrator_tasks",
                params={"id": f"eq.{task_id}"},
                json=updates
            )
            
            if response.status_code in [200, 204]:
                logger.info(f"Updated task {task_id} status to {status}")
                return True
            else:
                logger.error(f"Failed to update task status: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error updating task {task_id} status: {e}")
            return False

    async def create_run(self, run: RunRecord) -> Dict[str, Any]:
        """Create run record in database."""
        try:
            run_data = {
                "id": str(run.id),
                "task_id": str(run.task_id),
                "started_at": run.started_at.isoformat(),
                "status": run.status,
                "attempts": run.attempts,
                "metrics": run.metrics
            }
            
            response = await self.client.post(
                "/rest/v1/orchestrator_runs",
                json=run_data
            )
            
            if response.status_code == 201:
                logger.info(f"Created run {run.id} for task {run.task_id}")
                return response.json()[0] if response.json() else run_data
            else:
                logger.error(f"Failed to create run: {response.text}")
                raise Exception(f"Failed to create run: {response.status_code}")
                
        except Exception as e:
            logger.error(f"Error creating run {run.id}: {e}")
            raise

    async def update_run(
        self,
        run_id: UUID,
        status: str,
        ended_at: Optional[datetime] = None,
        metrics: Optional[Dict[str, Any]] = None,
        error_details: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Update run record in database."""
        try:
            updates = {"status": status}
            if ended_at:
                updates["ended_at"] = ended_at.isoformat()
            if metrics:
                updates["metrics"] = metrics
            if error_details:
                updates["error_details"] = error_details
                
            response = await self.client.patch(
                f"/rest/v1/orchestrator_runs",
                params={"id": f"eq.{run_id}"},
                json=updates
            )
            
            if response.status_code in [200, 204]:
                logger.info(f"Updated run {run_id} status to {status}")
                return True
            else:
                logger.error(f"Failed to update run: {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Error updating run {run_id}: {e}")
            return False

    async def get_recent_runs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent run records."""
        try:
            response = await self.client.get(
                "/rest/v1/orchestrator_runs",
                params={
                    "select": "*",
                    "order": "started_at.desc",
                    "limit": limit
                }
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to get runs: {response.text}")
                return []
                
        except Exception as e:
            logger.error(f"Error getting runs: {e}")
            return []

    async def get_tasks_by_status(self, status: TaskStatus, limit: int = 100) -> List[Dict[str, Any]]:
        """Get tasks by status."""
        try:
            response = await self.client.get(
                "/rest/v1/orchestrator_tasks",
                params={
                    "status": f"eq.{status}",
                    "select": "*",
                    "order": "enqueued_at.asc",
                    "limit": limit
                }
            )
            
            if response.status_code == 200:
                return response.json()
            else:
                logger.error(f"Failed to get tasks: {response.text}")
                return []
                
        except Exception as e:
            logger.error(f"Error getting tasks by status: {e}")
            return []

    async def check_idempotency(self, idempotency_key: str) -> Optional[Dict[str, Any]]:
        """Check if task with idempotency key exists."""
        try:
            response = await self.client.get(
                "/rest/v1/orchestrator_tasks",
                params={
                    "idempotency_key": f"eq.{idempotency_key}",
                    "select": "*"
                }
            )
            
            if response.status_code == 200:
                data = response.json()
                return data[0] if data else None
            else:
                logger.error(f"Failed to check idempotency: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error checking idempotency: {e}")
            return None

    async def upload_file(self, bucket: str, path: str, content: bytes, content_type: str = "application/octet-stream") -> Optional[str]:
        """Upload file to Supabase Storage."""
        try:
            response = await self.client.post(
                f"/storage/v1/object/{bucket}/{path}",
                content=content,
                headers={
                    **self.headers,
                    "Content-Type": content_type
                }
            )
            
            if response.status_code in [200, 201]:
                public_url = f"{self.url}/storage/v1/object/public/{bucket}/{path}"
                logger.info(f"Uploaded file to {public_url}")
                return public_url
            else:
                logger.error(f"Failed to upload file: {response.text}")
                return None
                
        except Exception as e:
            logger.error(f"Error uploading file: {e}")
            return None

    async def store_logs(self, task_id: UUID, logs: str) -> Optional[str]:
        """Store task logs in storage."""
        try:
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            path = f"logs/{task_id}/{timestamp}.log"
            
            url = await self.upload_file(
                bucket="orchestrator",
                path=path,
                content=logs.encode("utf-8"),
                content_type="text/plain"
            )
            
            return url
                
        except Exception as e:
            logger.error(f"Error storing logs for task {task_id}: {e}")
            return None

    async def get_metrics_summary(self, hours: int = 24) -> Dict[str, Any]:
        """Get metrics summary for the last N hours."""
        try:
            # This would be a more complex query in practice
            # For now, return a simple structure
            response = await self.client.get(
                "/rest/v1/orchestrator_runs",
                params={
                    "select": "status,metrics",
                    "started_at": f"gte.{datetime.utcnow().isoformat()}",
                    "limit": 1000
                }
            )
            
            if response.status_code == 200:
                runs = response.json()
                # Calculate metrics
                total = len(runs)
                successful = sum(1 for r in runs if r["status"] == "success")
                failed = sum(1 for r in runs if r["status"] == "failed")
                
                return {
                    "total_runs": total,
                    "successful_runs": successful,
                    "failed_runs": failed,
                    "success_rate": successful / total if total > 0 else 0,
                    "failure_rate": failed / total if total > 0 else 0
                }
            else:
                logger.error(f"Failed to get metrics: {response.text}")
                return {}
                
        except Exception as e:
            logger.error(f"Error getting metrics: {e}")
            return {}