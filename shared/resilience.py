"""
Resilience patterns: Budget guards, circuit breakers, and failover for AI calls.
"""

import asyncio
import time
from datetime import datetime, date, timedelta
from decimal import Decimal
from enum import Enum
from typing import Optional, Dict, Any, List, Callable, TypeVar, Awaitable
from collections import deque

import asyncpg
import redis.asyncio as redis

from shared.logging import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    """Circuit breaker states."""
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, reject calls
    HALF_OPEN = "half_open"  # Testing recovery


class AIProvider(str, Enum):
    """AI model providers."""
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GOOGLE = "google"


class BudgetExceededError(Exception):
    """Raised when daily budget is exceeded."""
    pass


class CircuitOpenError(Exception):
    """Raised when circuit breaker is open."""
    pass


class BudgetGuard:
    """
    Track and enforce daily budget limits for AI model usage.
    Uses sliding window for real-time tracking.
    """
    
    def __init__(
        self,
        db_pool: asyncpg.Pool,
        redis_client: redis.Redis,
        daily_budget_usd: float = 100.0,
    ):
        self.db_pool = db_pool
        self.redis = redis_client
        self.daily_budget_usd = daily_budget_usd
        self.window_key_prefix = "aurea:budget:window:"
        
    async def check_budget(
        self,
        provider: AIProvider,
        estimated_cost: float,
    ) -> bool:
        """
        Check if we have budget for this request.
        Returns True if within budget, raises BudgetExceededError if not.
        """
        today = date.today()
        
        # Get current spend from database
        async with self.db_pool.acquire() as conn:
            result = await conn.fetchrow(
                """
                SELECT spent_usd, budget_usd
                FROM orchestrator_budgets
                WHERE provider = $1 AND date = $2
                """,
                provider.value,
                today,
            )
            
            if result:
                spent = float(result["spent_usd"])
                budget = float(result["budget_usd"])
            else:
                # Create budget entry for today
                await conn.execute(
                    """
                    INSERT INTO orchestrator_budgets 
                    (provider, date, budget_usd)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (provider, date) DO NOTHING
                    """,
                    provider.value,
                    today,
                    self.daily_budget_usd,
                )
                spent = 0.0
                budget = self.daily_budget_usd
                
        # Check if we'd exceed budget
        if spent + estimated_cost > budget:
            remaining = max(0, budget - spent)
            raise BudgetExceededError(
                f"Budget exceeded for {provider.value}: "
                f"spent=${spent:.2f}, budget=${budget:.2f}, "
                f"requested=${estimated_cost:.2f}, remaining=${remaining:.2f}"
            )
            
        return True
        
    async def record_usage(
        self,
        provider: AIProvider,
        actual_cost: float,
        tokens_used: int,
    ) -> None:
        """Record actual usage after API call."""
        today = date.today()
        
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO orchestrator_budgets 
                (provider, date, budget_usd, spent_usd, token_count, request_count)
                VALUES ($1, $2, $3, $4, $5, 1)
                ON CONFLICT (provider, date) 
                DO UPDATE SET
                    spent_usd = orchestrator_budgets.spent_usd + $4,
                    token_count = orchestrator_budgets.token_count + $5,
                    request_count = orchestrator_budgets.request_count + 1,
                    last_updated = NOW()
                """,
                provider.value,
                today,
                self.daily_budget_usd,
                actual_cost,
                tokens_used,
            )
            
        # Update sliding window in Redis
        window_key = f"{self.window_key_prefix}{provider.value}"
        now = time.time()
        
        # Add to sorted set with timestamp as score
        await self.redis.zadd(window_key, {f"{now}:{actual_cost}": now})
        
        # Remove entries older than 24 hours
        cutoff = now - 86400
        await self.redis.zremrangebyscore(window_key, 0, cutoff)
        
        # Set expiry for cleanup
        await self.redis.expire(window_key, 86400 * 2)  # 48 hours
        
    async def get_sliding_window_spend(
        self,
        provider: AIProvider,
        hours: int = 24,
    ) -> float:
        """Get spend in sliding window (last N hours)."""
        window_key = f"{self.window_key_prefix}{provider.value}"
        now = time.time()
        cutoff = now - (hours * 3600)
        
        # Get entries in window
        entries = await self.redis.zrangebyscore(
            window_key,
            cutoff,
            now,
            withscores=False
        )
        
        total = 0.0
        for entry in entries:
            if isinstance(entry, bytes):
                entry = entry.decode()
            # Format: "timestamp:cost"
            parts = entry.split(":")
            if len(parts) >= 2:
                try:
                    total += float(parts[1])
                except (ValueError, IndexError):
                    pass
                    
        return total
        
    async def get_remaining_budget(self, provider: AIProvider) -> float:
        """Get remaining budget for today."""
        today = date.today()
        
        async with self.db_pool.acquire() as conn:
            result = await conn.fetchrow(
                """
                SELECT spent_usd, budget_usd
                FROM orchestrator_budgets
                WHERE provider = $1 AND date = $2
                """,
                provider.value,
                today,
            )
            
        if result:
            return max(0, float(result["budget_usd"]) - float(result["spent_usd"]))
        return self.daily_budget_usd


class CircuitBreaker:
    """
    Circuit breaker pattern for external service calls.
    Tracks failures and opens circuit when threshold exceeded.
    """
    
    def __init__(
        self,
        db_pool: asyncpg.Pool,
        redis_client: redis.Redis,
        service_name: str,
        failure_threshold: float = 0.1,  # 10% error rate
        timeout_seconds: int = 600,  # 10 minutes
        window_size: int = 100,  # Last 100 requests
    ):
        self.db_pool = db_pool
        self.redis = redis_client
        self.service_name = service_name
        self.failure_threshold = failure_threshold
        self.timeout_seconds = timeout_seconds
        self.window_size = window_size
        
        # In-memory tracking for performance
        self.request_window = deque(maxlen=window_size)
        self.last_db_sync = time.time()
        
    async def call(
        self,
        func: Callable[..., Awaitable[T]],
        *args,
        **kwargs
    ) -> T:
        """
        Execute function through circuit breaker.
        Raises CircuitOpenError if circuit is open.
        """
        state = await self._get_state()
        
        if state == CircuitState.OPEN:
            # Check if we should transition to half-open
            if await self._should_attempt_reset():
                await self._set_state(CircuitState.HALF_OPEN)
                state = CircuitState.HALF_OPEN
            else:
                raise CircuitOpenError(f"Circuit open for {self.service_name}")
                
        try:
            # Execute the function
            result = await func(*args, **kwargs)
            
            # Record success
            await self._record_success()
            
            # If half-open and successful, close the circuit
            if state == CircuitState.HALF_OPEN:
                await self._set_state(CircuitState.CLOSED)
                logger.info(f"Circuit closed for {self.service_name}")
                
            return result
            
        except Exception as e:
            # Record failure
            await self._record_failure(str(e))
            
            # Check if we should open the circuit
            if await self._should_open_circuit():
                await self._set_state(CircuitState.OPEN)
                logger.warning(f"Circuit opened for {self.service_name}: {e}")
                
            raise
            
    async def _get_state(self) -> CircuitState:
        """Get current circuit state."""
        async with self.db_pool.acquire() as conn:
            result = await conn.fetchrow(
                """
                SELECT state FROM orchestrator_circuit_breakers
                WHERE service = $1
                """,
                self.service_name,
            )
            
        if result:
            return CircuitState(result["state"])
        return CircuitState.CLOSED
        
    async def _set_state(self, state: CircuitState) -> None:
        """Set circuit state."""
        now = datetime.utcnow()
        next_retry = None
        
        if state == CircuitState.OPEN:
            next_retry = now + timedelta(seconds=self.timeout_seconds)
            
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO orchestrator_circuit_breakers
                (service, state, opened_at, next_retry_at)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (service)
                DO UPDATE SET
                    state = $2,
                    opened_at = CASE WHEN $2 = 'open' THEN $3 ELSE orchestrator_circuit_breakers.opened_at END,
                    next_retry_at = $4
                """,
                self.service_name,
                state.value,
                now if state == CircuitState.OPEN else None,
                next_retry,
            )
            
    async def _record_success(self) -> None:
        """Record successful call."""
        self.request_window.append(1)
        
        # Sync to database periodically
        if time.time() - self.last_db_sync > 10:
            await self._sync_to_db()
            
    async def _record_failure(self, error: str) -> None:
        """Record failed call."""
        self.request_window.append(0)
        
        # Always sync failures immediately
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO orchestrator_circuit_breakers
                (service, failure_count, last_failure_at, metadata)
                VALUES ($1, 1, NOW(), $2)
                ON CONFLICT (service)
                DO UPDATE SET
                    failure_count = orchestrator_circuit_breakers.failure_count + 1,
                    last_failure_at = NOW(),
                    metadata = orchestrator_circuit_breakers.metadata || $2
                """,
                self.service_name,
                {"last_error": error, "timestamp": datetime.utcnow().isoformat()},
            )
            
    async def _should_open_circuit(self) -> bool:
        """Check if circuit should open based on error rate."""
        if len(self.request_window) < 10:  # Need minimum samples
            return False
            
        error_rate = 1 - (sum(self.request_window) / len(self.request_window))
        
        # Update error rate in database
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE orchestrator_circuit_breakers
                SET error_rate = $2
                WHERE service = $1
                """,
                self.service_name,
                error_rate,
            )
            
        return error_rate > self.failure_threshold
        
    async def _should_attempt_reset(self) -> bool:
        """Check if we should try half-open state."""
        async with self.db_pool.acquire() as conn:
            result = await conn.fetchrow(
                """
                SELECT next_retry_at FROM orchestrator_circuit_breakers
                WHERE service = $1
                """,
                self.service_name,
            )
            
        if result and result["next_retry_at"]:
            return datetime.utcnow() >= result["next_retry_at"]
        return True
        
    async def _sync_to_db(self) -> None:
        """Sync in-memory stats to database."""
        if not self.request_window:
            return
            
        success_count = sum(self.request_window)
        failure_count = len(self.request_window) - success_count
        
        async with self.db_pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO orchestrator_circuit_breakers
                (service, success_count, failure_count, last_success_at)
                VALUES ($1, $2, $3, NOW())
                ON CONFLICT (service)
                DO UPDATE SET
                    success_count = $2,
                    failure_count = $3,
                    last_success_at = NOW()
                """,
                self.service_name,
                success_count,
                failure_count,
            )
            
        self.last_db_sync = time.time()
        
    async def get_metrics(self) -> Dict[str, Any]:
        """Get circuit breaker metrics."""
        async with self.db_pool.acquire() as conn:
            result = await conn.fetchrow(
                """
                SELECT * FROM orchestrator_circuit_breakers
                WHERE service = $1
                """,
                self.service_name,
            )
            
        if result:
            return dict(result)
            
        # Calculate from in-memory if no DB record
        if self.request_window:
            success = sum(self.request_window)
            total = len(self.request_window)
            return {
                "service": self.service_name,
                "state": CircuitState.CLOSED.value,
                "success_count": success,
                "failure_count": total - success,
                "error_rate": 1 - (success / total) if total > 0 else 0,
            }
            
        return {
            "service": self.service_name,
            "state": CircuitState.CLOSED.value,
            "success_count": 0,
            "failure_count": 0,
            "error_rate": 0.0,
        }


class ModelFailover:
    """
    Manage failover between AI model providers.
    Tries providers in order based on preference and availability.
    """
    
    def __init__(
        self,
        providers: List[AIProvider],
        circuit_breakers: Dict[AIProvider, CircuitBreaker],
        budget_guard: BudgetGuard,
    ):
        self.providers = providers
        self.circuit_breakers = circuit_breakers
        self.budget_guard = budget_guard
        
    async def call_with_failover(
        self,
        func_map: Dict[AIProvider, Callable],
        estimated_costs: Dict[AIProvider, float],
        *args,
        **kwargs
    ) -> Tuple[Any, AIProvider]:
        """
        Try providers in order until one succeeds.
        Returns (result, provider_used).
        """
        errors = []
        
        for provider in self.providers:
            if provider not in func_map:
                continue
                
            try:
                # Check budget
                await self.budget_guard.check_budget(
                    provider,
                    estimated_costs.get(provider, 0.01)
                )
                
                # Try through circuit breaker
                circuit = self.circuit_breakers.get(provider)
                if circuit:
                    result = await circuit.call(
                        func_map[provider],
                        *args,
                        **kwargs
                    )
                else:
                    result = await func_map[provider](*args, **kwargs)
                    
                logger.info(f"Successfully used {provider.value}")
                return result, provider
                
            except (BudgetExceededError, CircuitOpenError) as e:
                errors.append(f"{provider.value}: {e}")
                logger.warning(f"Skipping {provider.value}: {e}")
                continue
                
            except Exception as e:
                errors.append(f"{provider.value}: {e}")
                logger.error(f"Error with {provider.value}: {e}")
                continue
                
        # All providers failed
        raise Exception(f"All providers failed: {'; '.join(errors)}")