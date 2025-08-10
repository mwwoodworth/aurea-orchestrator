"""
Tests for Worker service.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from orchestrator.worker import Worker
from shared import Task, TaskStatus, TaskType


@pytest.fixture
async def worker():
    """Create test worker."""
    worker = Worker()
    worker.redis_client = MagicMock()
    worker.supabase_client = MagicMock()
    return worker


@pytest.mark.asyncio
async def test_worker_setup():
    """Test worker setup."""
    worker = Worker()
    
    with patch("orchestrator.worker.RedisClient") as mock_redis:
        with patch("orchestrator.worker.SupabaseClient") as mock_supabase:
            mock_redis.return_value.connect = AsyncMock()
            mock_supabase.return_value.connect = AsyncMock()
            
            await worker.setup()
            
            assert worker.running is True
            assert worker.redis_client is not None


@pytest.mark.asyncio
async def test_process_task(worker):
    """Test task processing."""
    task = Task(
        id=uuid4(),
        type=TaskType.GEN_CONTENT,
        payload={"prompt": "test"}
    )
    
    worker.redis_client.acquire_lock = AsyncMock(return_value=True)
    worker.redis_client.release_lock = AsyncMock(return_value=True)
    worker.redis_client.update_task_status = AsyncMock()
    
    with patch("orchestrator.handlers.HANDLERS") as mock_handlers:
        mock_handler = AsyncMock(return_value={"status": "success"})
        mock_handlers.get.return_value = mock_handler
        
        await worker.process_task(task)
        
        mock_handler.assert_called_once()
        worker.redis_client.acquire_lock.assert_called_once_with(task.id)
        worker.redis_client.release_lock.assert_called_once_with(task.id)


@pytest.mark.asyncio
async def test_mark_task_success(worker):
    """Test marking task as successful."""
    task = Task(
        id=uuid4(),
        type=TaskType.GEN_CONTENT,
        payload={}
    )
    
    worker.redis_client.update_task_status = AsyncMock()
    worker.redis_client.increment_counter = AsyncMock()
    worker.supabase_client.update_task_status = AsyncMock()
    
    await worker.mark_task_success(
        task,
        None,
        {"status": "success"},
        10.5
    )
    
    worker.redis_client.update_task_status.assert_called_once_with(
        task.id,
        TaskStatus.DONE
    )
    worker.redis_client.increment_counter.assert_called_once()