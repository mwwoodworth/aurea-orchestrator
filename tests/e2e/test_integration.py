"""
End-to-end integration tests for AUREA Orchestrator.
Tests the full flow from task enqueue to completion.
"""

import asyncio
import json
import os
from datetime import datetime
from typing import Dict, Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
import httpx
import asyncpg
import redis.asyncio as redis

from shared.schemas import TaskType, TaskStatus, TaskPriority
from shared.queue_v2 import EnhancedQueue
from shared.security import WebhookVerifier, APIKeyManager, UserRole
from orchestrator.worker import Worker


# Test configuration
TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://test:test@localhost:5432/aurea_test"
)
TEST_REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://localhost:6379/1")
TEST_API_URL = os.getenv("TEST_API_URL", "http://localhost:8000")


@pytest_asyncio.fixture
async def db_pool():
    """Create test database pool."""
    pool = await asyncpg.create_pool(TEST_DATABASE_URL, min_size=1, max_size=5)
    
    # Run migrations
    async with pool.acquire() as conn:
        with open("migrations/001_initial.sql") as f:
            await conn.execute(f.read())
        with open("migrations/002_outbox_inbox.sql") as f:
            await conn.execute(f.read())
            
    yield pool
    
    # Cleanup
    async with pool.acquire() as conn:
        await conn.execute("DROP TABLE IF EXISTS orchestrator_runs CASCADE")
        await conn.execute("DROP TABLE IF EXISTS orchestrator_outbox CASCADE")
        await conn.execute("DROP TABLE IF EXISTS orchestrator_inbox CASCADE")
        await conn.execute("DROP TABLE IF EXISTS orchestrator_api_keys CASCADE")
        await conn.execute("DROP TABLE IF EXISTS orchestrator_budgets CASCADE")
        await conn.execute("DROP TABLE IF EXISTS orchestrator_circuit_breakers CASCADE")
        
    await pool.close()


@pytest_asyncio.fixture
async def redis_client():
    """Create test Redis client."""
    client = redis.from_url(TEST_REDIS_URL)
    await client.flushdb()  # Clear test database
    yield client
    await client.flushdb()  # Cleanup
    await client.close()


@pytest_asyncio.fixture
async def api_client():
    """Create test API client."""
    async with httpx.AsyncClient(base_url=TEST_API_URL) as client:
        yield client


@pytest_asyncio.fixture
async def api_key(db_pool):
    """Create test API key."""
    manager = APIKeyManager(db_pool, "test_salt")
    key_id, raw_key = await manager.create_key(
        name="test_key",
        role=UserRole.SERVICE,
        description="Test API key",
        created_by="test",
    )
    return raw_key


class TestTaskFlow:
    """Test complete task flow from enqueue to completion."""
    
    @pytest.mark.asyncio
    async def test_enqueue_and_complete_task(
        self,
        api_client: httpx.AsyncClient,
        api_key: str,
        db_pool: asyncpg.Pool,
        redis_client: redis.Redis,
    ):
        """Test enqueueing a task and verifying completion."""
        # Enqueue task
        task_data = {
            "type": "gen_content",
            "payload": {
                "prompt": "Test prompt",
                "model": "claude-3-sonnet-20240229",
            },
            "priority": "normal",
        }
        
        response = await api_client.post(
            "/tasks",
            json=task_data,
            headers={"Authorization": f"Bearer {api_key}"},
        )
        
        assert response.status_code == 200
        result = response.json()
        task_id = result["task_id"]
        
        # Verify task in queue
        queue = EnhancedQueue(redis_client)
        metrics = await queue.get_metrics()
        assert metrics["queue_depth"] > 0
        
        # Mock handler and process task
        with patch("orchestrator.handlers.gen_content.handle_gen_content") as mock_handler:
            mock_handler.return_value = {"content": "Generated content"}
            
            # Create worker and process
            worker = Worker(
                worker_id="test_worker",
                redis_client=redis_client,
                db_pool=db_pool,
            )
            
            # Process one task
            messages = await queue.dequeue("test_worker", count=1)
            assert len(messages) == 1
            
            message_id, task_data = messages[0]
            assert task_data["task_id"] == task_id
            
            # Simulate processing
            await worker._process_task(message_id, task_data)
            
        # Check task status
        response = await api_client.get(
            f"/tasks/{task_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        
        assert response.status_code == 200
        status = response.json()
        assert status["status"] == TaskStatus.COMPLETED.value
        
    @pytest.mark.asyncio
    async def test_task_retry_on_failure(
        self,
        db_pool: asyncpg.Pool,
        redis_client: redis.Redis,
    ):
        """Test task retry logic on failure."""
        queue = EnhancedQueue(redis_client, max_retries=3)
        await queue.initialize()
        
        # Enqueue task
        task = {
            "id": str(uuid4()),
            "type": TaskType.GEN_CONTENT,
            "payload": {"prompt": "Test"},
            "priority": TaskPriority.NORMAL,
            "status": TaskStatus.QUEUED,
        }
        
        await queue.enqueue(task)
        
        # Dequeue and fail multiple times
        for i in range(3):
            messages = await queue.dequeue("test_worker", count=1)
            assert len(messages) == 1
            
            message_id, task_data = messages[0]
            assert int(task_data["retry_count"]) == i
            
            # Simulate failure
            await queue.nack(message_id, task_data, error=f"Test error {i+1}")
            
            # Wait for backoff
            await asyncio.sleep(0.1)
            
        # Next failure should move to DLQ
        messages = await queue.dequeue("test_worker", count=1)
        message_id, task_data = messages[0]
        await queue.nack(message_id, task_data, error="Final error")
        
        # Check DLQ
        metrics = await queue.get_metrics()
        assert metrics["dlq_depth"] == 1
        
    @pytest.mark.asyncio
    async def test_idempotency(
        self,
        api_client: httpx.AsyncClient,
        api_key: str,
    ):
        """Test idempotency key prevents duplicate tasks."""
        idempotency_key = str(uuid4())
        
        task_data = {
            "type": "gen_content",
            "payload": {"prompt": "Test"},
            "idempotency_key": idempotency_key,
        }
        
        # First request
        response1 = await api_client.post(
            "/tasks",
            json=task_data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Idempotency-Key": idempotency_key,
            },
        )
        assert response1.status_code == 200
        task_id1 = response1.json()["task_id"]
        
        # Duplicate request
        response2 = await api_client.post(
            "/tasks",
            json=task_data,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Idempotency-Key": idempotency_key,
            },
        )
        assert response2.status_code == 200
        task_id2 = response2.json()["task_id"]
        
        # Should return same task ID
        assert task_id1 == task_id2


class TestWebhookSecurity:
    """Test webhook signature verification and replay protection."""
    
    @pytest.mark.asyncio
    async def test_github_webhook_verification(self, db_pool: asyncpg.Pool):
        """Test GitHub webhook signature verification."""
        secret = "test_webhook_secret"
        verifier = WebhookVerifier(secret, db_pool)
        
        payload = json.dumps({"action": "opened", "number": 123}).encode()
        
        # Valid signature
        valid_signature = verifier.compute_signature(payload)
        assert await verifier.verify_github(payload, valid_signature, "event_123")
        
        # Invalid signature
        assert not await verifier.verify_github(payload, "sha256=invalid", "event_124")
        
        # Replay attack (same event ID)
        assert not await verifier.verify_github(payload, valid_signature, "event_123")
        
    @pytest.mark.asyncio
    async def test_generic_webhook_with_timestamp(self, db_pool: asyncpg.Pool):
        """Test generic webhook with timestamp validation."""
        secret = "test_webhook_secret"
        verifier = WebhookVerifier(secret, db_pool)
        
        payload = json.dumps({"data": "test"}).encode()
        timestamp = str(int(datetime.utcnow().timestamp()))
        
        # Valid signature with timestamp
        valid_signature = verifier.compute_signature(payload, timestamp)
        assert await verifier.verify_generic(
            payload,
            valid_signature,
            timestamp,
            "test_source",
            "event_456"
        )
        
        # Old timestamp (replay attack)
        old_timestamp = str(int(datetime.utcnow().timestamp()) - 600)  # 10 minutes old
        old_signature = verifier.compute_signature(payload, old_timestamp)
        assert not await verifier.verify_generic(
            payload,
            old_signature,
            old_timestamp,
            "test_source",
            "event_457"
        )


class TestCircuitBreaker:
    """Test circuit breaker behavior."""
    
    @pytest.mark.asyncio
    async def test_circuit_opens_on_failures(
        self,
        db_pool: asyncpg.Pool,
        redis_client: redis.Redis,
    ):
        """Test circuit breaker opens after threshold failures."""
        from shared.resilience import CircuitBreaker, CircuitOpenError
        
        breaker = CircuitBreaker(
            db_pool,
            redis_client,
            "test_service",
            failure_threshold=0.5,  # 50% error rate
            timeout_seconds=1,
            window_size=10,
        )
        
        # Failing function
        call_count = 0
        async def failing_func():
            nonlocal call_count
            call_count += 1
            if call_count <= 6:  # Fail first 6 calls
                raise Exception("Test error")
            return "success"
            
        # Make calls until circuit opens
        failures = 0
        for i in range(10):
            try:
                await breaker.call(failing_func)
            except CircuitOpenError:
                # Circuit opened
                break
            except Exception:
                failures += 1
                
        # Circuit should be open after 60% failure rate
        assert failures >= 6
        
        # Next call should fail immediately
        with pytest.raises(CircuitOpenError):
            await breaker.call(failing_func)
            
        # Wait for timeout
        await asyncio.sleep(1.1)
        
        # Circuit should attempt half-open
        result = await breaker.call(failing_func)
        assert result == "success"


class TestBudgetGuard:
    """Test budget enforcement."""
    
    @pytest.mark.asyncio
    async def test_budget_enforcement(
        self,
        db_pool: asyncpg.Pool,
        redis_client: redis.Redis,
    ):
        """Test daily budget limits are enforced."""
        from shared.resilience import BudgetGuard, BudgetExceededError, AIProvider
        
        guard = BudgetGuard(db_pool, redis_client, daily_budget_usd=1.0)
        
        # Check small cost passes
        await guard.check_budget(AIProvider.ANTHROPIC, 0.10)
        await guard.record_usage(AIProvider.ANTHROPIC, 0.10, 100)
        
        # Keep adding until budget exceeded
        for i in range(10):
            await guard.record_usage(AIProvider.ANTHROPIC, 0.10, 100)
            
        # Next request should fail
        with pytest.raises(BudgetExceededError):
            await guard.check_budget(AIProvider.ANTHROPIC, 0.10)
            
        # Check remaining budget
        remaining = await guard.get_remaining_budget(AIProvider.ANTHROPIC)
        assert remaining == 0.0
        
        # Different provider should have its own budget
        await guard.check_budget(AIProvider.OPENAI, 0.50)


class TestMetrics:
    """Test metrics endpoint."""
    
    @pytest.mark.asyncio
    async def test_metrics_endpoint(self, api_client: httpx.AsyncClient):
        """Test Prometheus metrics endpoint."""
        response = await api_client.get("/metrics")
        
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        
        metrics = response.text
        
        # Check for required metrics
        assert "aurea_up 1" in metrics
        assert "aurea_queue_depth" in metrics
        assert "aurea_throughput_fph" in metrics
        assert "aurea_slo_latency_met" in metrics
        
    @pytest.mark.asyncio
    async def test_deps_health_endpoint(self, api_client: httpx.AsyncClient):
        """Test dependencies health check."""
        response = await api_client.get("/internal/health/deps")
        
        assert response.status_code == 200
        health = response.json()
        
        assert "status" in health
        assert "dependencies" in health
        assert "postgres" in health["dependencies"]
        assert "redis" in health["dependencies"]
        assert "migrations" in health["dependencies"]