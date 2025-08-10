"""
Prometheus metrics endpoint with SLOs and custom metrics.
"""

import time
from typing import Dict, Any
from datetime import datetime, timedelta

from fastapi import APIRouter, Response, Depends
import asyncpg
import redis.asyncio as redis

from shared.clients import get_redis_client, get_db_pool
from shared.queue_v2 import EnhancedQueue
from shared.resilience import BudgetGuard, AIProvider
from shared.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()

# Metric collectors
class MetricsCollector:
    """Collect and format metrics for Prometheus."""
    
    def __init__(self):
        self.start_time = time.time()
        
    async def collect_metrics(
        self,
        db_pool: asyncpg.Pool,
        redis_client: redis.Redis,
    ) -> str:
        """Collect all metrics and format as Prometheus text."""
        metrics = []
        
        # Add standard headers
        metrics.append("# HELP aurea_up Is the AUREA orchestrator up (1 = yes, 0 = no)")
        metrics.append("# TYPE aurea_up gauge")
        metrics.append("aurea_up 1")
        
        # Uptime
        uptime = time.time() - self.start_time
        metrics.append("# HELP aurea_uptime_seconds Uptime in seconds")
        metrics.append("# TYPE aurea_uptime_seconds counter")
        metrics.append(f"aurea_uptime_seconds {uptime:.2f}")
        
        # Queue metrics
        queue_metrics = await self._collect_queue_metrics(redis_client)
        metrics.extend(queue_metrics)
        
        # Task metrics
        task_metrics = await self._collect_task_metrics(db_pool)
        metrics.extend(task_metrics)
        
        # Budget metrics
        budget_metrics = await self._collect_budget_metrics(db_pool)
        metrics.extend(budget_metrics)
        
        # Circuit breaker metrics
        circuit_metrics = await self._collect_circuit_metrics(db_pool)
        metrics.extend(circuit_metrics)
        
        # SLO metrics
        slo_metrics = await self._collect_slo_metrics(db_pool)
        metrics.extend(slo_metrics)
        
        # Dependencies health
        deps_metrics = await self._collect_deps_metrics(db_pool, redis_client)
        metrics.extend(deps_metrics)
        
        return "\n".join(metrics) + "\n"
        
    async def _collect_queue_metrics(self, redis_client: redis.Redis) -> list:
        """Collect queue-related metrics."""
        metrics = []
        
        try:
            # Initialize queue to get metrics
            queue = EnhancedQueue(redis_client)
            queue_stats = await queue.get_metrics()
            
            # Queue depth
            metrics.append("# HELP aurea_queue_depth Number of tasks in queue")
            metrics.append("# TYPE aurea_queue_depth gauge")
            metrics.append(f"aurea_queue_depth {queue_stats.get('queue_depth', 0)}")
            
            # DLQ depth
            metrics.append("# HELP aurea_dlq_total Number of tasks in dead letter queue")
            metrics.append("# TYPE aurea_dlq_total gauge")
            metrics.append(f"aurea_dlq_total {queue_stats.get('dlq_depth', 0)}")
            
            # Pending count
            metrics.append("# HELP aurea_pending_tasks Number of pending tasks")
            metrics.append("# TYPE aurea_pending_tasks gauge")
            metrics.append(f"aurea_pending_tasks {queue_stats.get('pending_count', 0)}")
            
            # Active leases
            metrics.append("# HELP aurea_active_leases Number of active task leases")
            metrics.append("# TYPE aurea_active_leases gauge")
            metrics.append(f"aurea_active_leases {queue_stats.get('active_leases', 0)}")
            
        except Exception as e:
            logger.error(f"Error collecting queue metrics: {e}")
            metrics.append("# Error collecting queue metrics")
            
        return metrics
        
    async def _collect_task_metrics(self, db_pool: asyncpg.Pool) -> list:
        """Collect task execution metrics."""
        metrics = []
        
        try:
            async with db_pool.acquire() as conn:
                # Task counts by status
                status_counts = await conn.fetch("""
                    SELECT status, COUNT(*) as count
                    FROM orchestrator_runs
                    WHERE created_at > NOW() - INTERVAL '24 hours'
                    GROUP BY status
                """)
                
                metrics.append("# HELP aurea_tasks_total Total tasks by status")
                metrics.append("# TYPE aurea_tasks_total counter")
                for row in status_counts:
                    status = row["status"]
                    count = row["count"]
                    metrics.append(f'aurea_tasks_total{{status="{status}"}} {count}')
                    
                # Task duration percentiles
                duration_stats = await conn.fetchrow("""
                    SELECT 
                        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY duration_ms) as p50,
                        PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) as p95,
                        PERCENTILE_CONT(0.99) WITHIN GROUP (ORDER BY duration_ms) as p99,
                        AVG(duration_ms) as avg
                    FROM (
                        SELECT EXTRACT(EPOCH FROM (completed_at - started_at)) * 1000 as duration_ms
                        FROM orchestrator_runs
                        WHERE completed_at IS NOT NULL
                        AND started_at IS NOT NULL
                        AND created_at > NOW() - INTERVAL '1 hour'
                    ) t
                """)
                
                if duration_stats and duration_stats["p50"]:
                    metrics.append("# HELP aurea_task_duration_seconds Task execution duration")
                    metrics.append("# TYPE aurea_task_duration_seconds summary")
                    metrics.append(f'aurea_task_duration_seconds{{quantile="0.5"}} {duration_stats["p50"]/1000:.3f}')
                    metrics.append(f'aurea_task_duration_seconds{{quantile="0.95"}} {duration_stats["p95"]/1000:.3f}')
                    metrics.append(f'aurea_task_duration_seconds{{quantile="0.99"}} {duration_stats["p99"]/1000:.3f}')
                    metrics.append(f'aurea_task_duration_seconds_sum {duration_stats["avg"]/1000:.3f}')
                    
                # Retry rate
                retry_stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(CASE WHEN retry_count > 0 THEN 1 END) as retried,
                        COUNT(*) as total
                    FROM orchestrator_runs
                    WHERE created_at > NOW() - INTERVAL '1 hour'
                """)
                
                if retry_stats and retry_stats["total"] > 0:
                    retry_rate = retry_stats["retried"] / retry_stats["total"]
                    metrics.append("# HELP aurea_retry_rate Percentage of tasks that required retry")
                    metrics.append("# TYPE aurea_retry_rate gauge")
                    metrics.append(f"aurea_retry_rate {retry_rate:.4f}")
                    
                # Throughput (tasks per hour)
                throughput = await conn.fetchval("""
                    SELECT COUNT(*)::float
                    FROM orchestrator_runs
                    WHERE completed_at > NOW() - INTERVAL '1 hour'
                """)
                
                metrics.append("# HELP aurea_throughput_fph Tasks completed per hour")
                metrics.append("# TYPE aurea_throughput_fph gauge")
                metrics.append(f"aurea_throughput_fph {throughput or 0}")
                
        except Exception as e:
            logger.error(f"Error collecting task metrics: {e}")
            metrics.append("# Error collecting task metrics")
            
        return metrics
        
    async def _collect_budget_metrics(self, db_pool: asyncpg.Pool) -> list:
        """Collect budget and cost metrics."""
        metrics = []
        
        try:
            async with db_pool.acquire() as conn:
                # Budget usage by provider
                budget_stats = await conn.fetch("""
                    SELECT provider, budget_usd, spent_usd, token_count, request_count
                    FROM orchestrator_budgets
                    WHERE date = CURRENT_DATE
                """)
                
                metrics.append("# HELP aurea_budget_spent_usd Amount spent today in USD")
                metrics.append("# TYPE aurea_budget_spent_usd gauge")
                
                metrics.append("# HELP aurea_budget_remaining_usd Remaining budget today in USD")
                metrics.append("# TYPE aurea_budget_remaining_usd gauge")
                
                metrics.append("# HELP aurea_tokens_used Total tokens used today")
                metrics.append("# TYPE aurea_tokens_used counter")
                
                for row in budget_stats:
                    provider = row["provider"]
                    spent = float(row["spent_usd"] or 0)
                    budget = float(row["budget_usd"] or 100)
                    remaining = max(0, budget - spent)
                    tokens = row["token_count"] or 0
                    
                    metrics.append(f'aurea_budget_spent_usd{{provider="{provider}"}} {spent:.2f}')
                    metrics.append(f'aurea_budget_remaining_usd{{provider="{provider}"}} {remaining:.2f}')
                    metrics.append(f'aurea_tokens_used{{provider="{provider}"}} {tokens}')
                    
        except Exception as e:
            logger.error(f"Error collecting budget metrics: {e}")
            metrics.append("# Error collecting budget metrics")
            
        return metrics
        
    async def _collect_circuit_metrics(self, db_pool: asyncpg.Pool) -> list:
        """Collect circuit breaker metrics."""
        metrics = []
        
        try:
            async with db_pool.acquire() as conn:
                circuit_stats = await conn.fetch("""
                    SELECT service, state, error_rate, failure_count, success_count
                    FROM orchestrator_circuit_breakers
                """)
                
                metrics.append("# HELP aurea_circuit_open_total Number of open circuits")
                metrics.append("# TYPE aurea_circuit_open_total gauge")
                
                open_count = sum(1 for row in circuit_stats if row["state"] == "open")
                metrics.append(f"aurea_circuit_open_total {open_count}")
                
                metrics.append("# HELP aurea_circuit_error_rate Error rate by service")
                metrics.append("# TYPE aurea_circuit_error_rate gauge")
                
                for row in circuit_stats:
                    service = row["service"]
                    error_rate = float(row["error_rate"] or 0)
                    metrics.append(f'aurea_circuit_error_rate{{service="{service}"}} {error_rate:.4f}')
                    
        except Exception as e:
            logger.error(f"Error collecting circuit metrics: {e}")
            metrics.append("# Error collecting circuit metrics")
            
        return metrics
        
    async def _collect_slo_metrics(self, db_pool: asyncpg.Pool) -> list:
        """Collect SLO (Service Level Objective) metrics."""
        metrics = []
        
        try:
            async with db_pool.acquire() as conn:
                # SLO 1: P95 task latency < 60s
                latency_slo = await conn.fetchrow("""
                    SELECT 
                        PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms) as p95
                    FROM (
                        SELECT EXTRACT(EPOCH FROM (completed_at - started_at)) * 1000 as duration_ms
                        FROM orchestrator_runs
                        WHERE completed_at IS NOT NULL
                        AND started_at IS NOT NULL
                        AND type IN ('code_pr', 'gen_content')
                        AND created_at > NOW() - INTERVAL '1 hour'
                    ) t
                """)
                
                if latency_slo and latency_slo["p95"]:
                    p95_seconds = latency_slo["p95"] / 1000
                    slo_met = 1 if p95_seconds < 60 else 0
                    
                    metrics.append("# HELP aurea_slo_latency_met P95 latency < 60s (1=met, 0=violated)")
                    metrics.append("# TYPE aurea_slo_latency_met gauge")
                    metrics.append(f"aurea_slo_latency_met {slo_met}")
                    
                # SLO 2: DLQ rate < 0.5% per hour
                dlq_slo = await conn.fetchrow("""
                    SELECT 
                        (SELECT COUNT(*) FROM orchestrator_runs 
                         WHERE created_at > NOW() - INTERVAL '1 hour'
                         AND status = 'failed' AND retry_count >= 3) as dlq_count,
                        (SELECT COUNT(*) FROM orchestrator_runs 
                         WHERE created_at > NOW() - INTERVAL '1 hour') as total_count
                """)
                
                if dlq_slo and dlq_slo["total_count"] > 0:
                    dlq_rate = dlq_slo["dlq_count"] / dlq_slo["total_count"]
                    slo_met = 1 if dlq_rate < 0.005 else 0
                    
                    metrics.append("# HELP aurea_slo_dlq_met DLQ rate < 0.5% (1=met, 0=violated)")
                    metrics.append("# TYPE aurea_slo_dlq_met gauge")
                    metrics.append(f"aurea_slo_dlq_met {slo_met}")
                    
                # SLO 3: Error budget < 1% per day
                error_slo = await conn.fetchrow("""
                    SELECT 
                        (SELECT COUNT(*) FROM orchestrator_runs 
                         WHERE created_at > NOW() - INTERVAL '24 hours'
                         AND status = 'failed') as error_count,
                        (SELECT COUNT(*) FROM orchestrator_runs 
                         WHERE created_at > NOW() - INTERVAL '24 hours') as total_count
                """)
                
                if error_slo and error_slo["total_count"] > 0:
                    error_rate = error_slo["error_count"] / error_slo["total_count"]
                    slo_met = 1 if error_rate < 0.01 else 0
                    
                    metrics.append("# HELP aurea_slo_error_budget_met Error rate < 1% (1=met, 0=violated)")
                    metrics.append("# TYPE aurea_slo_error_budget_met gauge")
                    metrics.append(f"aurea_slo_error_budget_met {slo_met}")
                    
        except Exception as e:
            logger.error(f"Error collecting SLO metrics: {e}")
            metrics.append("# Error collecting SLO metrics")
            
        return metrics
        
    async def _collect_deps_metrics(
        self,
        db_pool: asyncpg.Pool,
        redis_client: redis.Redis,
    ) -> list:
        """Collect dependency health metrics."""
        metrics = []
        
        metrics.append("# HELP aurea_deps_health Dependency health (1=healthy, 0=unhealthy)")
        metrics.append("# TYPE aurea_deps_health gauge")
        
        # Check database
        try:
            async with db_pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            metrics.append('aurea_deps_health{service="postgres"} 1')
        except:
            metrics.append('aurea_deps_health{service="postgres"} 0')
            
        # Check Redis
        try:
            await redis_client.ping()
            metrics.append('aurea_deps_health{service="redis"} 1')
        except:
            metrics.append('aurea_deps_health{service="redis"} 0')
            
        # Check migrations
        try:
            async with db_pool.acquire() as conn:
                latest = await conn.fetchval("""
                    SELECT COUNT(*) FROM orchestrator_outbox
                    LIMIT 1
                """)
            metrics.append('aurea_deps_health{service="migrations"} 1')
        except:
            metrics.append('aurea_deps_health{service="migrations"} 0')
            
        return metrics
        
    async def _collect_outbox_metrics(self, db_pool: asyncpg.Pool) -> list:
        """Collect outbox pattern metrics."""
        metrics = []
        
        try:
            async with db_pool.acquire() as conn:
                outbox_stats = await conn.fetchrow("""
                    SELECT 
                        COUNT(CASE WHEN status = 'pending' THEN 1 END) as pending,
                        COUNT(CASE WHEN status = 'delivered' THEN 1 END) as delivered,
                        COUNT(CASE WHEN status = 'failed' THEN 1 END) as failed
                    FROM orchestrator_outbox
                    WHERE created_at > NOW() - INTERVAL '1 hour'
                """)
                
                metrics.append("# HELP aurea_outbox_pending Pending outbox messages")
                metrics.append("# TYPE aurea_outbox_pending gauge")
                metrics.append(f"aurea_outbox_pending {outbox_stats['pending'] or 0}")
                
                # Inbox replay blocks
                replay_blocks = await conn.fetchval("""
                    SELECT COUNT(*)
                    FROM orchestrator_inbox
                    WHERE status = 'rejected'
                    AND rejection_reason LIKE '%replay%'
                    AND received_at > NOW() - INTERVAL '1 hour'
                """)
                
                metrics.append("# HELP aurea_inbox_replay_blocked_total Blocked replay attempts")
                metrics.append("# TYPE aurea_inbox_replay_blocked_total counter")
                metrics.append(f"aurea_inbox_replay_blocked_total {replay_blocks or 0}")
                
        except Exception as e:
            logger.error(f"Error collecting outbox metrics: {e}")
            
        return metrics


# Global collector instance
collector = MetricsCollector()


@router.get("/metrics", response_class=Response)
async def get_metrics(
    db_pool: asyncpg.Pool = Depends(get_db_pool),
    redis_client: redis.Redis = Depends(get_redis_client),
) -> Response:
    """
    Prometheus metrics endpoint.
    Returns metrics in Prometheus text format.
    """
    try:
        metrics_text = await collector.collect_metrics(db_pool, redis_client)
        return Response(
            content=metrics_text,
            media_type="text/plain; version=0.0.4",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Expires": "0",
            }
        )
    except Exception as e:
        logger.error(f"Error generating metrics: {e}")
        # Return minimal metrics on error
        error_metrics = "# Error generating full metrics\naurea_up 0\n"
        return Response(
            content=error_metrics,
            media_type="text/plain; version=0.0.4",
        )


@router.get("/internal/health/deps")
async def check_dependencies(
    db_pool: asyncpg.Pool = Depends(get_db_pool),
    redis_client: redis.Redis = Depends(get_redis_client),
) -> Dict[str, Any]:
    """Check health of all dependencies."""
    health = {
        "status": "healthy",
        "dependencies": {},
        "timestamp": datetime.utcnow().isoformat(),
    }
    
    # Check database
    try:
        async with db_pool.acquire() as conn:
            version = await conn.fetchval("SELECT version()")
        health["dependencies"]["postgres"] = {
            "status": "healthy",
            "version": version.split()[0] if version else "unknown",
        }
    except Exception as e:
        health["status"] = "degraded"
        health["dependencies"]["postgres"] = {
            "status": "unhealthy",
            "error": str(e),
        }
        
    # Check Redis
    try:
        info = await redis_client.info()
        health["dependencies"]["redis"] = {
            "status": "healthy",
            "version": info.get("redis_version", "unknown"),
        }
    except Exception as e:
        health["status"] = "degraded"
        health["dependencies"]["redis"] = {
            "status": "unhealthy",
            "error": str(e),
        }
        
    # Check migrations
    try:
        async with db_pool.acquire() as conn:
            tables = await conn.fetch("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name IN (
                    'orchestrator_outbox',
                    'orchestrator_inbox',
                    'orchestrator_api_keys',
                    'orchestrator_budgets',
                    'orchestrator_circuit_breakers'
                )
            """)
        found = {row["table_name"] for row in tables}
        expected = {
            "orchestrator_outbox",
            "orchestrator_inbox",
            "orchestrator_api_keys",
            "orchestrator_budgets",
            "orchestrator_circuit_breakers",
        }
        
        if found == expected:
            health["dependencies"]["migrations"] = {"status": "healthy"}
        else:
            health["status"] = "degraded"
            health["dependencies"]["migrations"] = {
                "status": "incomplete",
                "missing": list(expected - found),
            }
    except Exception as e:
        health["status"] = "degraded"
        health["dependencies"]["migrations"] = {
            "status": "error",
            "error": str(e),
        }
        
    return health