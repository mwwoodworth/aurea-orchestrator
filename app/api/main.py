"""
AUREA Orchestrator FastAPI Web Service
"""
import asyncio
import hashlib
import hmac
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncGenerator, Dict, Optional
from uuid import UUID

import sentry_sdk
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from prometheus_client import Counter, Gauge, Histogram, generate_latest
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware

from shared import (
    HealthStatus,
    MetricsSnapshot,
    RedisClient,
    SupabaseClient,
    Task,
    TaskRequest,
    TaskResult,
    TaskStatus,
    WebhookPayload,
    get_logger,
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

# Prometheus metrics
task_counter = Counter("aurea_tasks_total", "Total tasks processed", ["type", "status"])
queue_depth_gauge = Gauge("aurea_queue_depth", "Current queue depth")
task_duration_histogram = Histogram("aurea_task_duration_seconds", "Task duration", ["type"])
retry_counter = Counter("aurea_retries_total", "Total retries")
budget_gauge = Gauge("aurea_budget_used_usd", "Budget used in USD")

# Global clients
redis_client: Optional[RedisClient] = None
supabase_client: Optional[SupabaseClient] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global redis_client, supabase_client
    
    # Startup
    logger.info("Starting AUREA Orchestrator API")
    
    # Initialize Redis
    redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
    redis_tls = os.getenv("REDIS_TLS", "false").lower() == "true"
    redis_client = RedisClient(redis_url, ssl=redis_tls)
    await redis_client.connect()
    
    # Initialize Supabase
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if supabase_url and supabase_key:
        supabase_client = SupabaseClient(supabase_url, supabase_key)
        await supabase_client.connect()
    
    yield
    
    # Shutdown
    logger.info("Shutting down AUREA Orchestrator API")
    if redis_client:
        await redis_client.disconnect()
    if supabase_client:
        await supabase_client.disconnect()


# Create FastAPI app
app = FastAPI(
    title="AUREA Orchestrator",
    version="1.0.0",
    description="Always-on orchestration layer for AUREA",
    lifespan=lifespan
)

# Add CORS middleware
cors_origins = os.getenv("CORS_ORIGINS", '["*"]')
app.add_middleware(
    CORSMiddleware,
    allow_origins=eval(cors_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Add Sentry middleware
if os.getenv("SENTRY_DSN"):
    app.add_middleware(SentryAsgiMiddleware)


def verify_webhook_signature(payload: bytes, signature: str, secret: str) -> bool:
    """Verify webhook signature."""
    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


def verify_api_key(api_key: str) -> bool:
    """Verify API key."""
    expected = os.getenv("API_KEY")
    if not expected:
        return True  # No auth configured
    return api_key == expected


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": "AUREA Orchestrator",
        "version": "1.0.0",
        "status": "operational"
    }


@app.get("/health", response_model=HealthStatus)
async def health():
    """Health check endpoint."""
    checks = {}
    
    # Check Redis
    if redis_client:
        try:
            await redis_client.client.ping()
            checks["redis"] = True
        except Exception:
            checks["redis"] = False
    
    # Check Supabase
    if supabase_client:
        try:
            await supabase_client.client.get("/rest/v1/")
            checks["supabase"] = True
        except Exception:
            checks["supabase"] = False
    
    status = "healthy" if all(checks.values()) else "degraded"
    
    return HealthStatus(
        status=status,
        version="1.0.0",
        checks=checks
    )


@app.get("/metrics")
async def metrics():
    """Prometheus metrics endpoint."""
    # Update queue depth gauge
    if redis_client:
        depth = await redis_client.get_queue_depth()
        queue_depth_gauge.set(depth)
    
    # Generate Prometheus metrics
    return Response(
        content=generate_latest(),
        media_type="text/plain"
    )


@app.post("/tasks", response_model=TaskResult)
async def create_task(request: Request, task_request: TaskRequest):
    """Create a new task."""
    # Verify API key
    api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not verify_api_key(api_key):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Check idempotency
    if task_request.idempotency_key and supabase_client:
        existing = await supabase_client.check_idempotency(task_request.idempotency_key)
        if existing:
            logger.info(f"Task with idempotency key {task_request.idempotency_key} already exists")
            return TaskResult(
                task_id=UUID(existing["id"]),
                status=TaskStatus(existing["status"])
            )
    
    # Create task
    task = Task(
        type=task_request.type,
        payload=task_request.payload,
        priority=task_request.priority,
        trace_id=task_request.trace_id,
        idempotency_key=task_request.idempotency_key,
        metadata=task_request.metadata
    )
    
    # Store in database
    if supabase_client:
        await supabase_client.create_task(task)
    
    # Enqueue task
    if redis_client:
        await redis_client.enqueue_task(task)
    
    # Update metrics
    task_counter.labels(type=task.type, status="created").inc()
    
    logger.info(f"Created task {task.id} of type {task.type}")
    
    return TaskResult(
        task_id=task.id,
        status=task.status
    )


@app.get("/tasks/{task_id}", response_model=TaskResult)
async def get_task(request: Request, task_id: UUID):
    """Get task status."""
    # Verify API key
    api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not verify_api_key(api_key):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Get from database first
    if supabase_client:
        task_data = await supabase_client.get_task(task_id)
        if task_data:
            return TaskResult(
                task_id=UUID(task_data["id"]),
                status=TaskStatus(task_data["status"]),
                result=task_data.get("payload"),
                error=task_data.get("last_error")
            )
    
    # Fall back to Redis
    if redis_client:
        status_data = await redis_client.get_task_status(task_id)
        if status_data:
            return TaskResult(
                task_id=task_id,
                status=TaskStatus(status_data["status"]),
                error=status_data.get("last_error")
            )
    
    raise HTTPException(status_code=404, detail="Task not found")


@app.get("/admin/runs")
async def get_runs(request: Request, limit: int = 100):
    """Get recent run records."""
    # Verify API key
    api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not verify_api_key(api_key):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    if not supabase_client:
        return []
    
    runs = await supabase_client.get_recent_runs(limit)
    return runs


@app.post("/webhooks/clickup")
async def webhook_clickup(request: Request):
    """Handle ClickUp webhooks."""
    # Verify signature
    signature = request.headers.get("X-Signature", "")
    secret = os.getenv("WEBHOOK_SECRET", "")
    body = await request.body()
    
    if not verify_webhook_signature(body, signature, secret):
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    # Parse webhook
    data = await request.json()
    webhook = WebhookPayload(
        source="clickup",
        event_type=data.get("event", "unknown"),
        data=data
    )
    
    # Create task for webhook processing
    task = Task(
        type="webhook_process",
        payload=webhook.dict()
    )
    
    if redis_client:
        await redis_client.enqueue_task(task)
    
    return {"status": "accepted", "task_id": str(task.id)}


@app.post("/webhooks/make")
async def webhook_make(request: Request):
    """Handle Make.com webhooks."""
    # Verify signature
    signature = request.headers.get("X-Signature", "")
    secret = os.getenv("WEBHOOK_SECRET", "")
    body = await request.body()
    
    if not verify_webhook_signature(body, signature, secret):
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    # Parse webhook
    data = await request.json()
    webhook = WebhookPayload(
        source="make",
        event_type=data.get("event", "unknown"),
        data=data
    )
    
    # Create task for webhook processing
    task = Task(
        type="webhook_process",
        payload=webhook.dict()
    )
    
    if redis_client:
        await redis_client.enqueue_task(task)
    
    return {"status": "accepted", "task_id": str(task.id)}


@app.post("/webhooks/github")
async def webhook_github(request: Request):
    """Handle GitHub webhooks."""
    # Verify signature
    signature = request.headers.get("X-Hub-Signature-256", "").replace("sha256=", "")
    secret = os.getenv("WEBHOOK_SECRET", "")
    body = await request.body()
    
    if not verify_webhook_signature(body, signature, secret):
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    # Parse webhook
    data = await request.json()
    event_type = request.headers.get("X-GitHub-Event", "unknown")
    
    webhook = WebhookPayload(
        source="github",
        event_type=event_type,
        data=data
    )
    
    # Create task for webhook processing
    task = Task(
        type="webhook_process",
        payload=webhook.dict()
    )
    
    if redis_client:
        await redis_client.enqueue_task(task)
    
    return {"status": "accepted", "task_id": str(task.id)}


async def stream_task_logs(task_id: UUID) -> AsyncGenerator[str, None]:
    """Stream task logs via SSE."""
    last_check = time.time()
    
    while True:
        # Check for new logs every second
        await asyncio.sleep(1)
        
        # Get task status
        if redis_client:
            status_data = await redis_client.get_task_status(task_id)
            if status_data:
                yield f"data: {{'status': '{status_data['status']}'}}\n\n"
                
                # Stop streaming if task is done
                if status_data["status"] in ["done", "failed", "canceled"]:
                    yield "data: {'event': 'complete'}\n\n"
                    break
        
        # Timeout after 10 minutes
        if time.time() - last_check > 600:
            yield "data: {'event': 'timeout'}\n\n"
            break


@app.get("/stream/{task_id}")
async def stream_task(request: Request, task_id: UUID):
    """Stream task logs and updates via SSE."""
    # Verify API key
    api_key = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not verify_api_key(api_key):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    if not os.getenv("ENABLE_SSE", "true").lower() == "true":
        raise HTTPException(status_code=501, detail="SSE not enabled")
    
    return StreamingResponse(
        stream_task_logs(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        }
    )


@app.post("/internal/maintenance")
async def maintenance(request: Request):
    """Internal maintenance endpoint."""
    # Verify internal key
    internal_key = request.headers.get("X-Internal-Key", "")
    if internal_key != os.getenv("INTERNAL_KEY", ""):
        raise HTTPException(status_code=401, detail="Unauthorized")
    
    # Create maintenance task
    task = Task(
        type="maintenance",
        payload={
            "action": "daily_cleanup",
            "timestamp": datetime.utcnow().isoformat()
        }
    )
    
    if redis_client:
        await redis_client.enqueue_task(task)
    
    return {"status": "maintenance task created", "task_id": str(task.id)}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    logger.exception(f"Unhandled exception: {exc}")
    
    if os.getenv("ENV") == "development":
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc)}
        )
    
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"}
    )


if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=os.getenv("ENV") == "development"
    )