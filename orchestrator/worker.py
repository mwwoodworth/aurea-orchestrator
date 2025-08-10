"""
AUREA Orchestrator Worker Service
"""
import asyncio
import os
import signal
import sys
from datetime import datetime
from typing import Any, Dict, Optional

import sentry_sdk
from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from orchestrator.handlers import HANDLERS
from shared import (
    RedisClient,
    RunRecord,
    RunStatus,
    SupabaseClient,
    Task,
    TaskStatus,
    get_logger,
    get_task_logger,
    setup_logging,
)

# Initialize logging
setup_logging(level=os.getenv("LOG_LEVEL", "INFO"))
logger = get_logger(__name__)

# Initialize Sentry
if sentry_dsn := os.getenv("SENTRY_DSN"):
    sentry_sdk.init(
        dsn=sentry_dsn,
        environment=os.getenv("ENV", "production"),
        traces_sample_rate=0.1,
    )


class Worker:
    """AUREA Orchestrator Worker."""
    
    def __init__(self):
        self.worker_id = os.getenv("WORKER_ID", "aurea-worker-01")
        self.redis_client: Optional[RedisClient] = None
        self.supabase_client: Optional[SupabaseClient] = None
        self.running = False
        self.current_task: Optional[Task] = None
        self.semaphore = asyncio.Semaphore(int(os.getenv("MAX_CONCURRENCY", "8")))
        self.shutdown_event = asyncio.Event()
        
    async def setup(self) -> None:
        """Initialize clients and connections."""
        logger.info(f"Setting up worker {self.worker_id}")
        
        # Initialize Redis
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        redis_tls = os.getenv("REDIS_TLS", "false").lower() == "true"
        self.redis_client = RedisClient(redis_url, ssl=redis_tls)
        await self.redis_client.connect()
        
        # Initialize Supabase
        supabase_url = os.getenv("SUPABASE_URL")
        supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
        if supabase_url and supabase_key:
            self.supabase_client = SupabaseClient(supabase_url, supabase_key)
            await self.supabase_client.connect()
        
        self.running = True
        logger.info(f"Worker {self.worker_id} setup complete")
    
    async def teardown(self) -> None:
        """Clean up connections."""
        logger.info(f"Tearing down worker {self.worker_id}")
        self.running = False
        
        if self.redis_client:
            await self.redis_client.disconnect()
        if self.supabase_client:
            await self.supabase_client.disconnect()
        
        logger.info(f"Worker {self.worker_id} teardown complete")
    
    async def run(self) -> None:
        """Main worker loop."""
        logger.info(f"Worker {self.worker_id} starting main loop")
        
        while self.running:
            try:
                # Check for shutdown signal
                if self.shutdown_event.is_set():
                    logger.info("Shutdown signal received")
                    break
                
                # Dequeue task with timeout
                task = await self.redis_client.dequeue_task(timeout=5)
                
                if task:
                    # Process task asynchronously
                    asyncio.create_task(self.process_task(task))
                
            except Exception as e:
                logger.error(f"Error in worker loop: {e}")
                await asyncio.sleep(1)
        
        logger.info(f"Worker {self.worker_id} stopped")
    
    async def process_task(self, task: Task) -> None:
        """Process a single task."""
        async with self.semaphore:
            task_logger = get_task_logger(__name__, task.id, task.trace_id)
            task_logger.info(f"Processing task {task.id} of type {task.type}")
            
            # Acquire lock
            if not await self.redis_client.acquire_lock(task.id):
                task_logger.warning(f"Could not acquire lock for task {task.id}")
                return
            
            run_record = None
            try:
                # Update task status
                await self.redis_client.update_task_status(task.id, TaskStatus.RUNNING)
                if self.supabase_client:
                    await self.supabase_client.update_task_status(
                        task.id,
                        TaskStatus.RUNNING
                    )
                
                # Create run record
                run_record = RunRecord(task_id=task.id)
                if self.supabase_client:
                    await self.supabase_client.create_run(run_record)
                
                # Execute handler with retries
                result = await self.execute_with_retries(task, task_logger)
                
                # Update task and run status
                end_time = datetime.utcnow()
                duration = (end_time - run_record.started_at).total_seconds()
                
                if result.get("status") == "success":
                    await self.mark_task_success(task, run_record, result, duration)
                else:
                    await self.mark_task_failed(task, run_record, result, duration)
                
            except Exception as e:
                task_logger.exception(f"Error processing task: {e}")
                await self.mark_task_failed(
                    task,
                    run_record,
                    {"status": "failed", "error": str(e)},
                    0
                )
                
            finally:
                # Release lock
                await self.redis_client.release_lock(task.id)
    
    @retry(
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
        stop=stop_after_attempt(int(os.getenv("TASK_MAX_RETRIES", "6"))),
        wait=wait_exponential(
            multiplier=int(os.getenv("TASK_BACKOFF_BASE_SEC", "2")),
            max=int(os.getenv("TASK_BACKOFF_MAX_SEC", "60"))
        )
    )
    async def execute_with_retries(self, task: Task, task_logger) -> Dict[str, Any]:
        """Execute task handler with retries."""
        handler = HANDLERS.get(task.type)
        
        if not handler:
            raise ValueError(f"No handler for task type: {task.type}")
        
        task_logger.info(f"Executing handler for task type {task.type}")
        
        # Extend lock periodically
        lock_extender = asyncio.create_task(
            self.extend_lock_periodically(task.id)
        )
        
        try:
            result = await handler(str(task.id), task.payload)
            return result
        finally:
            lock_extender.cancel()
    
    async def extend_lock_periodically(self, task_id) -> None:
        """Extend lock periodically while task is running."""
        extension_interval = int(os.getenv("TASK_LEASE_SECONDS", "900")) // 2
        
        while True:
            await asyncio.sleep(extension_interval)
            await self.redis_client.extend_lock(task_id)
            logger.debug(f"Extended lock for task {task_id}")
    
    async def mark_task_success(
        self,
        task: Task,
        run_record: Optional[RunRecord],
        result: Dict[str, Any],
        duration: float
    ) -> None:
        """Mark task as successful."""
        logger.info(f"Task {task.id} completed successfully")
        
        # Update Redis
        await self.redis_client.update_task_status(task.id, TaskStatus.DONE)
        
        # Update database
        if self.supabase_client:
            await self.supabase_client.update_task_status(
                task.id,
                TaskStatus.DONE,
                completed_at=datetime.utcnow()
            )
            
            if run_record:
                await self.supabase_client.update_run(
                    run_record.id,
                    RunStatus.SUCCESS,
                    ended_at=datetime.utcnow(),
                    metrics={
                        "duration_seconds": duration,
                        "result": result
                    }
                )
        
        # Update metrics
        await self.redis_client.increment_counter(f"success:{task.type}")
    
    async def mark_task_failed(
        self,
        task: Task,
        run_record: Optional[RunRecord],
        result: Dict[str, Any],
        duration: float
    ) -> None:
        """Mark task as failed."""
        error = result.get("error", "Unknown error")
        logger.error(f"Task {task.id} failed: {error}")
        
        # Update Redis
        await self.redis_client.update_task_status(
            task.id,
            TaskStatus.FAILED,
            error=error
        )
        
        # Update database
        if self.supabase_client:
            await self.supabase_client.update_task_status(
                task.id,
                TaskStatus.FAILED,
                error=error,
                completed_at=datetime.utcnow()
            )
            
            if run_record:
                await self.supabase_client.update_run(
                    run_record.id,
                    RunStatus.FAILED,
                    ended_at=datetime.utcnow(),
                    metrics={
                        "duration_seconds": duration
                    },
                    error_details=result
                )
        
        # Update metrics
        await self.redis_client.increment_counter(f"failure:{task.type}")
    
    def handle_shutdown(self, signum, frame):
        """Handle shutdown signals."""
        logger.info(f"Received signal {signum}, initiating shutdown")
        self.shutdown_event.set()
        self.running = False


async def main():
    """Main entry point."""
    worker = Worker()
    
    # Setup signal handlers
    signal.signal(signal.SIGINT, worker.handle_shutdown)
    signal.signal(signal.SIGTERM, worker.handle_shutdown)
    
    try:
        await worker.setup()
        await worker.run()
    finally:
        await worker.teardown()


if __name__ == "__main__":
    asyncio.run(main())