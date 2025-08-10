"""
Chaos engineering tests to verify system resilience.
Tests network failures, timeouts, and error conditions.
"""

import asyncio
import random
import os
from typing import Any, Optional
from unittest.mock import AsyncMock, patch, MagicMock
from contextlib import asynccontextmanager

import pytest
import pytest_asyncio
import httpx
import asyncpg
import redis.asyncio as redis

from shared.resilience import CircuitBreaker, BudgetGuard, AIProvider
from shared.queue_v2 import EnhancedQueue
from orchestrator.worker import Worker


# Enable chaos testing only in test environment
CHAOS_ENABLED = os.getenv("CHAOS_TESTING", "false").lower() == "true"


class ChaosMonkey:
    """Inject failures and delays for chaos testing."""
    
    def __init__(
        self,
        failure_rate: float = 0.3,
        delay_range: tuple = (0.1, 2.0),
        timeout_rate: float = 0.1,
    ):
        self.failure_rate = failure_rate
        self.delay_range = delay_range
        self.timeout_rate = timeout_rate
        self.enabled = CHAOS_ENABLED
        
    async def maybe_fail(self, service: str = "unknown"):
        """Randomly fail with configured probability."""
        if not self.enabled:
            return
            
        if random.random() < self.failure_rate:
            error_types = [
                ConnectionError(f"Connection to {service} failed"),
                TimeoutError(f"Timeout connecting to {service}"),
                httpx.HTTPStatusError(
                    f"503 Service Unavailable",
                    request=MagicMock(),
                    response=MagicMock(status_code=503)
                ),
                httpx.HTTPStatusError(
                    f"429 Too Many Requests",
                    request=MagicMock(),
                    response=MagicMock(status_code=429)
                ),
            ]
            raise random.choice(error_types)
            
    async def maybe_delay(self):
        """Add random delay to simulate network latency."""
        if not self.enabled:
            return
            
        if random.random() < 0.5:  # 50% chance of delay
            delay = random.uniform(*self.delay_range)
            await asyncio.sleep(delay)
            
    async def maybe_timeout(self):
        """Simulate timeout by sleeping forever."""
        if not self.enabled:
            return
            
        if random.random() < self.timeout_rate:
            await asyncio.sleep(30)  # Sleep long enough to trigger timeout
            
    @asynccontextmanager
    async def chaos_context(self, service: str):
        """Context manager for chaos injection."""
        if not self.enabled:
            yield
            return
            
        await self.maybe_delay()
        await self.maybe_fail(service)
        yield
        await self.maybe_delay()


@pytest.mark.skipif(not CHAOS_ENABLED, reason="Chaos testing disabled")
class TestChaosResilience:
    """Test system behavior under chaotic conditions."""
    
    @pytest_asyncio.fixture
    def chaos_monkey(self):
        """Create chaos monkey for testing."""
        return ChaosMonkey(
            failure_rate=0.3,
            delay_range=(0.1, 1.0),
            timeout_rate=0.1,
        )
        
    @pytest.mark.asyncio
    async def test_worker_handles_redis_failures(
        self,
        db_pool: asyncpg.Pool,
        redis_client: redis.Redis,
        chaos_monkey: ChaosMonkey,
    ):
        """Test worker resilience to Redis failures."""
        queue = EnhancedQueue(redis_client)
        await queue.initialize()
        
        # Enqueue test tasks
        for i in range(10):
            task = {
                "id": f"task_{i}",
                "type": "gen_content",
                "payload": {"prompt": f"Test {i}"},
                "priority": "normal",
                "status": "queued",
            }
            await queue.enqueue(task)
            
        # Create worker with chaos-injected Redis
        class ChaosRedis:
            def __init__(self, redis_client, chaos):
                self._redis = redis_client
                self._chaos = chaos
                
            async def __getattr__(self, name):
                attr = getattr(self._redis, name)
                if callable(attr):
                    async def wrapper(*args, **kwargs):
                        async with self._chaos.chaos_context("redis"):
                            return await attr(*args, **kwargs)
                    return wrapper
                return attr
                
        chaos_redis = ChaosRedis(redis_client, chaos_monkey)
        
        # Process tasks with failures
        processed = 0
        errors = 0
        
        worker = Worker(
            worker_id="chaos_worker",
            redis_client=chaos_redis,
            db_pool=db_pool,
        )
        
        for _ in range(20):  # Try more than tasks to handle retries
            try:
                messages = await queue.dequeue("chaos_worker", count=1)
                if messages:
                    message_id, task_data = messages[0]
                    
                    # Mock handler
                    with patch("orchestrator.handlers.gen_content.handle_gen_content") as mock:
                        mock.return_value = {"content": "Generated"}
                        
                        try:
                            await worker._process_task(message_id, task_data)
                            processed += 1
                        except Exception as e:
                            errors += 1
                            await queue.nack(message_id, task_data, str(e))
                            
            except Exception as e:
                errors += 1
                await asyncio.sleep(0.5)  # Back off on error
                
        # Should process at least some tasks despite failures
        assert processed >= 5
        assert errors > 0  # Should have some failures
        
        # Check retry mechanism worked
        metrics = await queue.get_metrics()
        assert metrics["queue_depth"] + processed >= 10
        
    @pytest.mark.asyncio
    async def test_circuit_breaker_under_chaos(
        self,
        db_pool: asyncpg.Pool,
        redis_client: redis.Redis,
        chaos_monkey: ChaosMonkey,
    ):
        """Test circuit breaker behavior with chaotic failures."""
        breaker = CircuitBreaker(
            db_pool,
            redis_client,
            "chaos_service",
            failure_threshold=0.5,
            timeout_seconds=1,
            window_size=20,
        )
        
        # Function that fails randomly
        call_count = 0
        async def chaotic_func():
            nonlocal call_count
            call_count += 1
            
            # Inject chaos
            await chaos_monkey.maybe_delay()
            await chaos_monkey.maybe_fail("test_service")
            
            return f"success_{call_count}"
            
        # Make many calls
        successes = 0
        failures = 0
        circuit_opens = 0
        
        for i in range(50):
            try:
                result = await breaker.call(chaotic_func)
                successes += 1
            except CircuitOpenError:
                circuit_opens += 1
                await asyncio.sleep(0.1)
            except Exception:
                failures += 1
                
        # Should have mix of outcomes
        assert successes > 0
        assert failures > 0
        
        # Circuit should have opened at some point
        if failures / (successes + failures) > 0.5:
            assert circuit_opens > 0
            
    @pytest.mark.asyncio
    async def test_budget_guard_with_chaotic_costs(
        self,
        db_pool: asyncpg.Pool,
        redis_client: redis.Redis,
        chaos_monkey: ChaosMonkey,
    ):
        """Test budget guard with unpredictable costs."""
        guard = BudgetGuard(db_pool, redis_client, daily_budget_usd=10.0)
        
        total_spent = 0.0
        requests = 0
        budget_errors = 0
        
        # Make requests with random costs
        for _ in range(100):
            # Random cost between $0.01 and $0.50
            cost = random.uniform(0.01, 0.50)
            
            try:
                await guard.check_budget(AIProvider.ANTHROPIC, cost)
                
                # Simulate actual usage with chaos
                actual_cost = cost * random.uniform(0.8, 1.2)  # ±20% variance
                await guard.record_usage(
                    AIProvider.ANTHROPIC,
                    actual_cost,
                    int(actual_cost * 1000)  # Fake tokens
                )
                
                total_spent += actual_cost
                requests += 1
                
            except BudgetExceededError:
                budget_errors += 1
                
                # Should not exceed budget
                remaining = await guard.get_remaining_budget(AIProvider.ANTHROPIC)
                assert remaining <= 0.5  # Small tolerance for rounding
                
        # Should have stopped before exceeding budget too much
        assert total_spent <= 11.0  # 10% tolerance
        assert budget_errors > 0  # Should have hit limit
        
    @pytest.mark.asyncio
    async def test_lease_recovery_under_chaos(
        self,
        redis_client: redis.Redis,
        chaos_monkey: ChaosMonkey,
    ):
        """Test task lease recovery when workers fail."""
        queue = EnhancedQueue(
            redis_client,
            visibility_timeout=2,  # Short timeout for testing
        )
        await queue.initialize()
        
        # Enqueue tasks
        task_ids = []
        for i in range(5):
            task = {
                "id": f"lease_task_{i}",
                "type": "gen_content",
                "payload": {"prompt": f"Test {i}"},
                "priority": "normal",
                "status": "queued",
            }
            message_id = await queue.enqueue(task)
            task_ids.append(message_id)
            
        # Simulate workers taking tasks but failing
        dead_workers = []
        for i in range(3):
            messages = await queue.dequeue(f"worker_{i}", count=1)
            if messages:
                dead_workers.append(messages[0])
                # Simulate worker death - don't ack or extend lease
                
        # Wait for visibility timeout
        await asyncio.sleep(2.5)
        
        # Reclaim expired leases
        reclaimed = await queue.reclaim_expired()
        assert reclaimed >= len(dead_workers)
        
        # New worker should be able to get the tasks
        healthy_worker_processed = 0
        for _ in range(10):
            messages = await queue.dequeue("healthy_worker", count=1)
            if messages:
                message_id, task_data = messages[0]
                await queue.ack(message_id)
                healthy_worker_processed += 1
                
        # Should process all tasks eventually
        assert healthy_worker_processed >= 5
        
    @pytest.mark.asyncio
    async def test_webhook_replay_under_chaos(
        self,
        db_pool: asyncpg.Pool,
        chaos_monkey: ChaosMonkey,
    ):
        """Test webhook replay protection with chaotic duplicate attempts."""
        from shared.security import WebhookVerifier
        
        secret = "chaos_secret"
        verifier = WebhookVerifier(secret, db_pool)
        
        # Generate webhooks
        webhooks = []
        for i in range(20):
            payload = json.dumps({"id": i, "data": f"test_{i}"}).encode()
            signature = verifier.compute_signature(payload)
            webhooks.append((f"event_{i}", payload, signature))
            
        # Process with random duplicates and delays
        processed = set()
        rejected = 0
        
        for _ in range(50):  # More attempts than webhooks
            # Pick random webhook
            event_id, payload, signature = random.choice(webhooks)
            
            # Add chaos
            await chaos_monkey.maybe_delay()
            
            try:
                # Verify webhook
                valid = await verifier.verify_github(payload, signature, event_id)
                
                if valid:
                    processed.add(event_id)
                else:
                    rejected += 1
                    
            except Exception:
                pass  # Chaos failure
                
        # Should process each webhook exactly once
        assert len(processed) == len(webhooks)
        assert rejected > 0  # Should have rejected duplicates
        
    @pytest.mark.asyncio
    async def test_failover_chain_under_chaos(
        self,
        db_pool: asyncpg.Pool,
        redis_client: redis.Redis,
        chaos_monkey: ChaosMonkey,
    ):
        """Test AI provider failover with chaotic failures."""
        from shared.resilience import ModelFailover, CircuitBreaker
        
        # Create circuit breakers for each provider
        breakers = {
            AIProvider.ANTHROPIC: CircuitBreaker(
                db_pool, redis_client, "anthropic",
                failure_threshold=0.3, timeout_seconds=1
            ),
            AIProvider.OPENAI: CircuitBreaker(
                db_pool, redis_client, "openai",
                failure_threshold=0.3, timeout_seconds=1
            ),
            AIProvider.GOOGLE: CircuitBreaker(
                db_pool, redis_client, "google",
                failure_threshold=0.3, timeout_seconds=1
            ),
        }
        
        # Create budget guard
        budget = BudgetGuard(db_pool, redis_client, daily_budget_usd=100.0)
        
        # Create failover manager
        failover = ModelFailover(
            providers=[AIProvider.ANTHROPIC, AIProvider.OPENAI, AIProvider.GOOGLE],
            circuit_breakers=breakers,
            budget_guard=budget,
        )
        
        # Mock functions with chaos
        async def anthropic_call(prompt):
            await chaos_monkey.maybe_fail("anthropic")
            return f"Anthropic: {prompt}"
            
        async def openai_call(prompt):
            await chaos_monkey.maybe_fail("openai")
            return f"OpenAI: {prompt}"
            
        async def google_call(prompt):
            await chaos_monkey.maybe_fail("google")
            return f"Google: {prompt}"
            
        func_map = {
            AIProvider.ANTHROPIC: anthropic_call,
            AIProvider.OPENAI: openai_call,
            AIProvider.GOOGLE: google_call,
        }
        
        costs = {
            AIProvider.ANTHROPIC: 0.01,
            AIProvider.OPENAI: 0.02,
            AIProvider.GOOGLE: 0.005,
        }
        
        # Make many calls
        provider_usage = {p: 0 for p in AIProvider}
        total_failures = 0
        
        for i in range(50):
            try:
                result, provider = await failover.call_with_failover(
                    func_map,
                    costs,
                    f"Test prompt {i}"
                )
                provider_usage[provider] += 1
                
            except Exception as e:
                total_failures += 1
                await asyncio.sleep(0.1)
                
        # Should use multiple providers due to failures
        providers_used = sum(1 for count in provider_usage.values() if count > 0)
        assert providers_used >= 2
        
        # Should have some complete failures when all providers fail
        assert total_failures > 0