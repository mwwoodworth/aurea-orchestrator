"""
Redis client for queue and distributed locking.
"""
import asyncio
import json
import os
from typing import Any, Dict, List, Optional
from uuid import UUID

import redis.asyncio as redis
from redis.exceptions import LockError, RedisError

from shared.logging import get_logger
from shared.schemas import Task, TaskStatus

logger = get_logger(__name__)


class RedisClient:
    """Async Redis client with queue and locking support."""

    def __init__(self, url: str, ssl: bool = False):
        self.url = url
        self.ssl = ssl
        self.client: Optional[redis.Redis] = None
        self.queue_name = os.getenv("QUEUE_NAME", "aurea:queue")
        self.lock_prefix = "aurea:lock:"
        self.lease_seconds = int(os.getenv("TASK_LEASE_SECONDS", "900"))

    async def connect(self) -> None:
        """Connect to Redis."""
        try:
            self.client = redis.from_url(
                self.url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
                ssl=self.ssl
            )
            await self.client.ping()
            logger.info("Connected to Redis")
        except RedisError as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise

    async def disconnect(self) -> None:
        """Disconnect from Redis."""
        if self.client:
            await self.client.close()
            logger.info("Disconnected from Redis")

    async def enqueue_task(self, task: Task) -> bool:
        """Add task to queue."""
        try:
            # Serialize task
            task_json = json.dumps(task.dict())
            
            # Add to sorted set with priority as score
            await self.client.zadd(
                self.queue_name,
                {task_json: task.priority}
            )
            
            # Track task status
            await self.client.hset(
                f"aurea:tasks:{task.id}",
                mapping={
                    "status": TaskStatus.QUEUED,
                    "enqueued_at": task.enqueued_at.isoformat()
                }
            )
            
            logger.info(f"Enqueued task {task.id} with priority {task.priority}")
            return True
            
        except RedisError as e:
            logger.error(f"Failed to enqueue task {task.id}: {e}")
            return False

    async def dequeue_task(self, timeout: int = 1) -> Optional[Task]:
        """Get next task from queue (blocking)."""
        try:
            # Pop highest priority task (lowest score)
            result = await self.client.bzpopmin(self.queue_name, timeout)
            
            if result:
                _, task_json, _ = result
                task_data = json.loads(task_json)
                task = Task(**task_data)
                
                # Update task status
                await self.client.hset(
                    f"aurea:tasks:{task.id}",
                    "status", TaskStatus.RUNNING
                )
                
                logger.info(f"Dequeued task {task.id}")
                return task
                
        except RedisError as e:
            logger.error(f"Failed to dequeue task: {e}")
            
        return None

    async def acquire_lock(self, task_id: UUID, ttl: Optional[int] = None) -> bool:
        """Acquire distributed lock for task."""
        try:
            lock_key = f"{self.lock_prefix}{task_id}"
            ttl = ttl or self.lease_seconds
            
            # Set lock with NX (only if not exists) and EX (expiry)
            acquired = await self.client.set(
                lock_key,
                "locked",
                nx=True,
                ex=ttl
            )
            
            if acquired:
                logger.info(f"Acquired lock for task {task_id}")
            else:
                logger.warning(f"Failed to acquire lock for task {task_id} - already locked")
                
            return bool(acquired)
            
        except RedisError as e:
            logger.error(f"Error acquiring lock for task {task_id}: {e}")
            return False

    async def release_lock(self, task_id: UUID) -> bool:
        """Release distributed lock for task."""
        try:
            lock_key = f"{self.lock_prefix}{task_id}"
            deleted = await self.client.delete(lock_key)
            
            if deleted:
                logger.info(f"Released lock for task {task_id}")
            else:
                logger.warning(f"Lock for task {task_id} was already released")
                
            return bool(deleted)
            
        except RedisError as e:
            logger.error(f"Error releasing lock for task {task_id}: {e}")
            return False

    async def extend_lock(self, task_id: UUID, ttl: Optional[int] = None) -> bool:
        """Extend TTL on existing lock."""
        try:
            lock_key = f"{self.lock_prefix}{task_id}"
            ttl = ttl or self.lease_seconds
            
            # Only extend if lock exists
            if await self.client.exists(lock_key):
                await self.client.expire(lock_key, ttl)
                logger.info(f"Extended lock for task {task_id} by {ttl} seconds")
                return True
            else:
                logger.warning(f"Cannot extend lock for task {task_id} - lock doesn't exist")
                return False
                
        except RedisError as e:
            logger.error(f"Error extending lock for task {task_id}: {e}")
            return False

    async def get_queue_depth(self) -> int:
        """Get number of tasks in queue."""
        try:
            return await self.client.zcard(self.queue_name)
        except RedisError as e:
            logger.error(f"Error getting queue depth: {e}")
            return 0

    async def get_task_status(self, task_id: UUID) -> Optional[Dict[str, Any]]:
        """Get task status from Redis."""
        try:
            status = await self.client.hgetall(f"aurea:tasks:{task_id}")
            return status if status else None
        except RedisError as e:
            logger.error(f"Error getting task status for {task_id}: {e}")
            return None

    async def update_task_status(self, task_id: UUID, status: TaskStatus, error: Optional[str] = None) -> bool:
        """Update task status in Redis."""
        try:
            updates = {"status": status}
            if error:
                updates["last_error"] = error
                
            await self.client.hset(
                f"aurea:tasks:{task_id}",
                mapping=updates
            )
            logger.info(f"Updated task {task_id} status to {status}")
            return True
            
        except RedisError as e:
            logger.error(f"Error updating task status for {task_id}: {e}")
            return False

    async def increment_counter(self, key: str, amount: int = 1) -> int:
        """Increment a counter."""
        try:
            return await self.client.incrby(f"aurea:counters:{key}", amount)
        except RedisError as e:
            logger.error(f"Error incrementing counter {key}: {e}")
            return 0

    async def get_counter(self, key: str) -> int:
        """Get counter value."""
        try:
            value = await self.client.get(f"aurea:counters:{key}")
            return int(value) if value else 0
        except (RedisError, ValueError) as e:
            logger.error(f"Error getting counter {key}: {e}")
            return 0

    async def set_with_ttl(self, key: str, value: Any, ttl: int) -> bool:
        """Set a value with TTL."""
        try:
            await self.client.setex(f"aurea:{key}", ttl, json.dumps(value))
            return True
        except RedisError as e:
            logger.error(f"Error setting {key}: {e}")
            return False

    async def get_json(self, key: str) -> Optional[Any]:
        """Get JSON value."""
        try:
            value = await self.client.get(f"aurea:{key}")
            return json.loads(value) if value else None
        except (RedisError, json.JSONDecodeError) as e:
            logger.error(f"Error getting {key}: {e}")
            return None