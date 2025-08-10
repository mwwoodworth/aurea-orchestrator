#!/usr/bin/env python3
"""
Dead Letter Queue drain utility.
Moves failed tasks back to main queue with optional priority adjustment.
"""

import asyncio
import argparse
import json
import os
import sys
from datetime import datetime
from typing import Optional

import redis.asyncio as redis
import asyncpg

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.queue_v2 import EnhancedQueue
from shared.logging import get_logger

logger = get_logger(__name__)


class DLQDrainer:
    """Utility to drain Dead Letter Queue."""
    
    def __init__(
        self,
        redis_url: str,
        database_url: Optional[str] = None,
        dry_run: bool = False,
    ):
        self.redis_url = redis_url
        self.database_url = database_url
        self.dry_run = dry_run
        
    async def run(
        self,
        max_messages: int = 100,
        lower_priority: bool = True,
        filter_type: Optional[str] = None,
    ) -> int:
        """
        Drain DLQ messages back to main queue.
        
        Args:
            max_messages: Maximum messages to drain
            lower_priority: Whether to lower task priority
            filter_type: Only drain tasks of this type
            
        Returns:
            Number of messages drained
        """
        # Connect to Redis
        redis_client = redis.from_url(self.redis_url)
        
        try:
            queue = EnhancedQueue(redis_client)
            
            # Get DLQ messages
            dlq_messages = await redis_client.xrange(
                queue.dlq_key,
                count=max_messages
            )
            
            if not dlq_messages:
                logger.info("DLQ is empty")
                return 0
                
            logger.info(f"Found {len(dlq_messages)} messages in DLQ")
            
            drained = 0
            skipped = 0
            
            for message_id, data in dlq_messages:
                # Decode data
                task_data = {
                    k.decode() if isinstance(k, bytes) else k:
                    v.decode() if isinstance(v, bytes) else v
                    for k, v in data.items()
                }
                
                # Filter by type if specified
                if filter_type and task_data.get("type") != filter_type:
                    skipped += 1
                    continue
                    
                # Display task info
                task_id = task_data.get("task_id", "unknown")
                task_type = task_data.get("type", "unknown")
                retry_count = task_data.get("retry_count", 0)
                error = task_data.get("final_error", "unknown")
                
                logger.info(
                    f"Task {task_id} ({task_type}): "
                    f"retries={retry_count}, error={error[:100]}"
                )
                
                if self.dry_run:
                    logger.info("  [DRY RUN] Would drain this task")
                    drained += 1
                    continue
                    
                # Actually drain the task
                if await self._drain_message(
                    redis_client,
                    queue,
                    message_id,
                    task_data,
                    lower_priority
                ):
                    drained += 1
                    logger.info(f"  ✓ Drained task {task_id}")
                else:
                    logger.error(f"  ✗ Failed to drain task {task_id}")
                    
            logger.info(
                f"Summary: Drained {drained}, Skipped {skipped}, "
                f"Remaining {len(dlq_messages) - drained - skipped}"
            )
            
            return drained
            
        finally:
            await redis_client.close()
            
    async def _drain_message(
        self,
        redis_client: redis.Redis,
        queue: EnhancedQueue,
        message_id: str,
        task_data: dict,
        lower_priority: bool,
    ) -> bool:
        """Drain a single message from DLQ."""
        try:
            # Reset retry count
            task_data["retry_count"] = 0
            task_data["drained_from_dlq_at"] = datetime.utcnow().isoformat()
            
            # Lower priority if requested
            if lower_priority:
                current_priority = task_data.get("priority", "normal")
                priority_map = {
                    "critical": "high",
                    "high": "normal",
                    "normal": "low",
                    "low": "low",
                }
                task_data["priority"] = priority_map.get(current_priority, "low")
                
            # Re-enqueue to main stream
            await redis_client.xadd(queue.stream_key, task_data)
            
            # Remove from DLQ
            await redis_client.xdel(queue.dlq_key, message_id)
            
            # Record in database if available
            if self.database_url:
                await self._record_drain(task_data)
                
            return True
            
        except Exception as e:
            logger.error(f"Error draining message: {e}")
            return False
            
    async def _record_drain(self, task_data: dict):
        """Record drain operation in database."""
        if not self.database_url:
            return
            
        try:
            conn = await asyncpg.connect(self.database_url)
            try:
                await conn.execute(
                    """
                    INSERT INTO orchestrator_runs 
                    (task_id, type, status, metadata, created_at)
                    VALUES ($1, $2, 'requeued', $3, NOW())
                    ON CONFLICT (task_id) DO UPDATE
                    SET status = 'requeued',
                        metadata = orchestrator_runs.metadata || $3
                    """,
                    task_data.get("task_id"),
                    task_data.get("type"),
                    {"drained_from_dlq": True, "drain_time": datetime.utcnow().isoformat()},
                )
            finally:
                await conn.close()
        except Exception as e:
            logger.warning(f"Failed to record drain in database: {e}")
            
    async def show_stats(self) -> dict:
        """Show DLQ statistics."""
        redis_client = redis.from_url(self.redis_url)
        
        try:
            queue = EnhancedQueue(redis_client)
            
            # Get DLQ length
            dlq_len = await redis_client.xlen(queue.dlq_key)
            
            if dlq_len == 0:
                return {"total": 0, "by_type": {}, "by_error": {}}
                
            # Get all messages for analysis
            messages = await redis_client.xrange(queue.dlq_key)
            
            by_type = {}
            by_error = {}
            oldest = None
            newest = None
            
            for message_id, data in messages:
                task_data = {
                    k.decode() if isinstance(k, bytes) else k:
                    v.decode() if isinstance(v, bytes) else v
                    for k, v in data.items()
                }
                
                # Count by type
                task_type = task_data.get("type", "unknown")
                by_type[task_type] = by_type.get(task_type, 0) + 1
                
                # Count by error (simplified)
                error = task_data.get("final_error", "unknown")
                error_key = error.split(":")[0] if ":" in error else error[:50]
                by_error[error_key] = by_error.get(error_key, 0) + 1
                
                # Track age
                moved_at = task_data.get("moved_to_dlq_at")
                if moved_at:
                    if not oldest or moved_at < oldest:
                        oldest = moved_at
                    if not newest or moved_at > newest:
                        newest = moved_at
                        
            return {
                "total": dlq_len,
                "by_type": by_type,
                "by_error": by_error,
                "oldest": oldest,
                "newest": newest,
            }
            
        finally:
            await redis_client.close()


async def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="AUREA Orchestrator DLQ Drain Utility"
    )
    
    parser.add_argument(
        "action",
        choices=["drain", "stats"],
        help="Action to perform",
    )
    
    parser.add_argument(
        "--redis-url",
        default=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        help="Redis connection URL",
    )
    
    parser.add_argument(
        "--database-url",
        default=os.getenv("DATABASE_URL"),
        help="PostgreSQL connection URL (optional)",
    )
    
    parser.add_argument(
        "--max",
        type=int,
        default=100,
        help="Maximum messages to drain (default: 100)",
    )
    
    parser.add_argument(
        "--no-lower-priority",
        action="store_true",
        help="Don't lower task priority when draining",
    )
    
    parser.add_argument(
        "--filter-type",
        help="Only drain tasks of this type",
    )
    
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be drained without actually doing it",
    )
    
    args = parser.parse_args()
    
    drainer = DLQDrainer(
        redis_url=args.redis_url,
        database_url=args.database_url,
        dry_run=args.dry_run,
    )
    
    if args.action == "stats":
        stats = await drainer.show_stats()
        print("\n=== DLQ Statistics ===")
        print(f"Total messages: {stats['total']}")
        
        if stats["total"] > 0:
            print(f"Oldest: {stats.get('oldest', 'unknown')}")
            print(f"Newest: {stats.get('newest', 'unknown')}")
            
            print("\nBy Type:")
            for task_type, count in sorted(stats["by_type"].items()):
                print(f"  {task_type}: {count}")
                
            print("\nBy Error:")
            for error, count in sorted(
                stats["by_error"].items(),
                key=lambda x: x[1],
                reverse=True
            )[:10]:  # Top 10 errors
                print(f"  {error}: {count}")
                
    elif args.action == "drain":
        if args.dry_run:
            print("=== DRY RUN MODE ===")
            
        drained = await drainer.run(
            max_messages=args.max,
            lower_priority=not args.no_lower_priority,
            filter_type=args.filter_type,
        )
        
        if not args.dry_run:
            print(f"\n✓ Drained {drained} messages from DLQ")


if __name__ == "__main__":
    asyncio.run(main())