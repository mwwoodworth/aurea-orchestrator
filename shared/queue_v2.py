"""
Enhanced queue implementation with Redis Streams, visibility timeout, and DLQ.
Provides at-least-once delivery semantics with consumer groups.
"""

import asyncio
import json
import time
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List, Tuple
from uuid import UUID

import redis.asyncio as redis
from redis.exceptions import ResponseError

from shared.schemas import Task, TaskStatus, TaskPriority
from shared.logging import get_logger

logger = get_logger(__name__)


class EnhancedQueue:
    """Production-grade queue with Redis Streams and visibility timeout."""
    
    def __init__(
        self,
        redis_client: redis.Redis,
        stream_key: str = "aurea:tasks",
        consumer_group: str = "aurea-workers",
        dlq_key: str = "aurea:dlq",
        visibility_timeout: int = 900,  # 15 minutes
        max_retries: int = 3,
    ):
        self.redis = redis_client
        self.stream_key = stream_key
        self.consumer_group = consumer_group
        self.dlq_key = dlq_key
        self.visibility_timeout = visibility_timeout
        self.max_retries = max_retries
        self.lease_key_prefix = "aurea:lease:"
        self.lock_key_prefix = "aurea:lock:"
        
    async def initialize(self) -> None:
        """Create consumer group if it doesn't exist."""
        try:
            await self.redis.xgroup_create(
                self.stream_key,
                self.consumer_group,
                id="0",
                mkstream=True
            )
            logger.info(f"Created consumer group {self.consumer_group}")
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
            logger.debug(f"Consumer group {self.consumer_group} already exists")
            
    async def enqueue(
        self,
        task: Task,
        idempotency_key: Optional[str] = None,
    ) -> str:
        """
        Enqueue a task with optional idempotency.
        Returns the stream message ID.
        """
        # Check idempotency
        if idempotency_key:
            lock_key = f"{self.lock_key_prefix}{idempotency_key}"
            if await self.redis.exists(lock_key):
                logger.warning(f"Duplicate task rejected: {idempotency_key}")
                existing = await self.redis.get(lock_key)
                return existing.decode() if existing else ""
                
        # Prepare task data
        task_data = {
            "task_id": str(task.id),
            "type": task.type.value,
            "payload": json.dumps(task.payload),
            "priority": task.priority.value,
            "status": TaskStatus.QUEUED.value,
            "retry_count": 0,
            "created_at": datetime.utcnow().isoformat(),
        }
        
        # Add to stream
        message_id = await self.redis.xadd(self.stream_key, task_data)
        
        # Store idempotency key if provided
        if idempotency_key:
            lock_key = f"{self.lock_key_prefix}{idempotency_key}"
            await self.redis.setex(
                lock_key,
                86400,  # 24 hour TTL
                message_id.decode() if isinstance(message_id, bytes) else message_id
            )
            
        # Also add to priority queue for backward compatibility
        score = self._get_priority_score(task.priority)
        await self.redis.zadd(
            "aurea:queue",
            {str(task.id): score}
        )
        
        logger.info(f"Enqueued task {task.id} with message ID {message_id}")
        return message_id.decode() if isinstance(message_id, bytes) else message_id
        
    async def dequeue(
        self,
        consumer_name: str,
        count: int = 1,
        block: int = 1000,  # Block for 1 second
    ) -> List[Tuple[str, Dict[str, Any]]]:
        """
        Dequeue tasks using consumer group with visibility timeout.
        Returns list of (message_id, task_data) tuples.
        """
        messages = await self.redis.xreadgroup(
            self.consumer_group,
            consumer_name,
            {self.stream_key: ">"},
            count=count,
            block=block,
        )
        
        if not messages:
            return []
            
        results = []
        for stream_name, stream_messages in messages:
            for message_id, data in stream_messages:
                # Decode data
                task_data = {
                    k.decode() if isinstance(k, bytes) else k:
                    v.decode() if isinstance(v, bytes) else v
                    for k, v in data.items()
                }
                
                # Set lease (visibility timeout)
                lease_key = f"{self.lease_key_prefix}{message_id}"
                await self.redis.setex(
                    lease_key,
                    self.visibility_timeout,
                    json.dumps({
                        "consumer": consumer_name,
                        "acquired_at": datetime.utcnow().isoformat(),
                    })
                )
                
                message_id_str = (
                    message_id.decode() if isinstance(message_id, bytes)
                    else message_id
                )
                results.append((message_id_str, task_data))
                
        return results
        
    async def ack(self, message_id: str) -> None:
        """Acknowledge successful task completion."""
        # Remove from stream
        await self.redis.xack(self.stream_key, self.consumer_group, message_id)
        await self.redis.xdel(self.stream_key, message_id)
        
        # Remove lease
        lease_key = f"{self.lease_key_prefix}{message_id}"
        await self.redis.delete(lease_key)
        
        logger.debug(f"Acknowledged message {message_id}")
        
    async def nack(
        self,
        message_id: str,
        task_data: Dict[str, Any],
        error: Optional[str] = None,
    ) -> None:
        """
        Negative acknowledge - retry or move to DLQ.
        """
        retry_count = int(task_data.get("retry_count", 0)) + 1
        
        if retry_count > self.max_retries:
            # Move to DLQ
            dlq_data = {
                **task_data,
                "retry_count": retry_count,
                "final_error": error or "Max retries exceeded",
                "moved_to_dlq_at": datetime.utcnow().isoformat(),
            }
            await self.redis.xadd(self.dlq_key, dlq_data)
            
            # Remove from main stream
            await self.redis.xack(self.stream_key, self.consumer_group, message_id)
            await self.redis.xdel(self.stream_key, message_id)
            
            logger.error(f"Task {task_data.get('task_id')} moved to DLQ after {retry_count} retries")
        else:
            # Update retry count and re-enqueue
            task_data["retry_count"] = retry_count
            task_data["last_error"] = error
            task_data["last_retry_at"] = datetime.utcnow().isoformat()
            
            # Remove old message
            await self.redis.xack(self.stream_key, self.consumer_group, message_id)
            await self.redis.xdel(self.stream_key, message_id)
            
            # Re-add with exponential backoff
            backoff = min(60, 2 ** retry_count)
            await asyncio.sleep(backoff)
            await self.redis.xadd(self.stream_key, task_data)
            
            logger.warning(f"Task {task_data.get('task_id')} retry {retry_count}/{self.max_retries}")
            
    async def extend_lease(self, message_id: str, consumer_name: str) -> bool:
        """
        Extend the visibility timeout for a task (heartbeat).
        Returns True if extended, False if lease expired.
        """
        lease_key = f"{self.lease_key_prefix}{message_id}"
        
        # Check if lease exists and belongs to this consumer
        lease_data = await self.redis.get(lease_key)
        if not lease_data:
            return False
            
        lease_info = json.loads(lease_data)
        if lease_info["consumer"] != consumer_name:
            return False
            
        # Extend lease
        await self.redis.expire(lease_key, self.visibility_timeout)
        logger.debug(f"Extended lease for message {message_id}")
        return True
        
    async def reclaim_expired(self) -> int:
        """
        Reclaim tasks with expired leases (visibility timeout).
        Returns the number of tasks reclaimed.
        """
        # Get pending messages
        pending = await self.redis.xpending(
            self.stream_key,
            self.consumer_group,
        )
        
        if not pending:
            return 0
            
        reclaimed = 0
        min_idle_time = self.visibility_timeout * 1000  # Convert to milliseconds
        
        # Get detailed pending info
        detailed = await self.redis.xpending_range(
            self.stream_key,
            self.consumer_group,
            min="-",
            max="+",
            count=100,
        )
        
        for entry in detailed:
            message_id = entry["message_id"]
            idle_time = entry["time_since_delivered"]
            
            if idle_time > min_idle_time:
                # Check if lease expired
                lease_key = f"{self.lease_key_prefix}{message_id}"
                if not await self.redis.exists(lease_key):
                    # Reclaim the message
                    await self.redis.xclaim(
                        self.stream_key,
                        self.consumer_group,
                        "reclaimer",
                        min_idle_time,
                        message_id,
                    )
                    reclaimed += 1
                    logger.info(f"Reclaimed expired message {message_id}")
                    
        return reclaimed
        
    async def get_metrics(self) -> Dict[str, Any]:
        """Get queue metrics for monitoring."""
        # Stream length
        stream_len = await self.redis.xlen(self.stream_key)
        
        # DLQ length
        dlq_len = await self.redis.xlen(self.dlq_key)
        
        # Pending messages
        pending_info = await self.redis.xpending(
            self.stream_key,
            self.consumer_group,
        )
        
        pending_count = pending_info["pending"] if pending_info else 0
        
        # Active leases
        lease_pattern = f"{self.lease_key_prefix}*"
        lease_keys = []
        async for key in self.redis.scan_iter(match=lease_pattern):
            lease_keys.append(key)
        active_leases = len(lease_keys)
        
        return {
            "queue_depth": stream_len,
            "dlq_depth": dlq_len,
            "pending_count": pending_count,
            "active_leases": active_leases,
        }
        
    def _get_priority_score(self, priority: TaskPriority) -> float:
        """Convert priority to score (lower score = higher priority)."""
        now = time.time()
        if priority == TaskPriority.CRITICAL:
            return now - 1000000
        elif priority == TaskPriority.HIGH:
            return now - 100000
        elif priority == TaskPriority.NORMAL:
            return now
        else:  # LOW
            return now + 100000
            
    async def drain_dlq(
        self,
        max_messages: int = 100,
        lower_priority: bool = True,
    ) -> int:
        """
        Drain DLQ back to main queue with optional priority adjustment.
        Returns the number of messages drained.
        """
        messages = await self.redis.xrange(self.dlq_key, count=max_messages)
        drained = 0
        
        for message_id, data in messages:
            # Decode data
            task_data = {
                k.decode() if isinstance(k, bytes) else k:
                v.decode() if isinstance(v, bytes) else v
                for k, v in data.items()
            }
            
            # Reset retry count
            task_data["retry_count"] = 0
            task_data["drained_from_dlq_at"] = datetime.utcnow().isoformat()
            
            # Lower priority if requested
            if lower_priority and task_data.get("priority") != TaskPriority.LOW.value:
                current_priority = TaskPriority(task_data["priority"])
                if current_priority == TaskPriority.CRITICAL:
                    task_data["priority"] = TaskPriority.HIGH.value
                elif current_priority == TaskPriority.HIGH:
                    task_data["priority"] = TaskPriority.NORMAL.value
                else:
                    task_data["priority"] = TaskPriority.LOW.value
                    
            # Re-enqueue
            await self.redis.xadd(self.stream_key, task_data)
            
            # Remove from DLQ
            await self.redis.xdel(self.dlq_key, message_id)
            drained += 1
            
        logger.info(f"Drained {drained} messages from DLQ")
        return drained