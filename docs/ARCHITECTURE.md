# AUREA Orchestrator Architecture

## System Overview

The AUREA Orchestrator is a distributed task orchestration system designed for high reliability, scalability, and observability. It manages cross-AI tasks, integrates with multiple services, and ensures reliable execution with automatic retries and idempotency.

```
┌─────────────────────────────────────────────────────────────┐
│                         Clients                              │
│  (Claude Code, ChatGPT, Gemini, ClickUp, Make, GitHub)      │
└─────────────┬───────────────────────────────┬───────────────┘
              │                               │
              ▼                               ▼
┌─────────────────────────┐     ┌─────────────────────────┐
│    FastAPI Web Service  │     │      Webhooks           │
│    (REST API + SSE)     │     │  (GitHub/ClickUp/Make)  │
└─────────────┬───────────┘     └─────────────┬───────────┘
              │                               │
              ▼                               ▼
┌─────────────────────────────────────────────────────────────┐
│                        Redis Queue                           │
│                 (Task Queue + Distributed Locks)             │
└─────────────────────────┬───────────────────────────────────┘
                          │
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                     Worker Service                           │
│                  (Task Execution Engine)                     │
└─────────┬───────────────────────────────────────┬───────────┘
          │                                       │
          ▼                                       ▼
┌──────────────────┐                   ┌──────────────────────┐
│  Task Handlers   │                   │   External Services  │
│  - code_pr       │                   │   - GitHub API       │
│  - centerpoint   │                   │   - Anthropic API    │
│  - mrg_deploy    │                   │   - OpenAI API       │
│  - gen_content   │                   │   - Gemini API       │
│  - aurea_action  │                   │   - Supabase         │
└──────────────────┘                   └──────────────────────┘
```

## Components

### 1. FastAPI Web Service (`app/api`)

**Responsibilities:**
- REST API for task management
- Webhook ingestion
- Server-Sent Events (SSE) for real-time updates
- Prometheus metrics exposition
- Health checks

**Key Endpoints:**
- `POST /tasks` - Create new task
- `GET /tasks/{id}` - Get task status
- `POST /webhooks/*` - Process webhooks
- `GET /stream/{id}` - Stream task logs
- `GET /metrics` - Prometheus metrics
- `GET /health` - Health status

### 2. Worker Service (`orchestrator`)

**Responsibilities:**
- Dequeue tasks from Redis
- Execute task handlers
- Manage retries with exponential backoff
- Maintain distributed locks
- Report execution metrics

**Features:**
- Concurrent task execution (configurable)
- Graceful shutdown
- Lock extension for long-running tasks
- Idempotency checking

### 3. Redis Queue

**Purpose:**
- Priority queue for tasks
- Distributed locking mechanism
- Task status tracking
- Metrics counters

**Data Structures:**
- Sorted Set: `aurea:queue` (tasks by priority)
- Strings: `aurea:lock:{task_id}` (distributed locks)
- Hashes: `aurea:tasks:{task_id}` (task status)
- Counters: `aurea:counters:*` (metrics)

### 4. Supabase Database

**Tables:**
- `orchestrator_tasks` - Task records with full history
- `orchestrator_runs` - Execution runs with metrics

**Storage Buckets:**
- `orchestrator` - Logs and internal files
- `content` - Generated content

### 5. Task Handlers

**Available Handlers:**

#### code_pr
- Creates GitHub pull requests
- Uses Claude for code generation
- Manages branches and commits

#### centerpoint_sync
- Executes TypeScript sync scripts
- Manages CenterPoint data synchronization
- Captures execution logs

#### mrg_deploy
- Triggers MyRoofGenius deployments
- Monitors deployment status
- Supports rollback

#### gen_content
- Generates content using AI models
- Supports multiple providers (Claude, GPT, Gemini)
- Saves to Supabase storage

#### aurea_action
- Composite multi-step workflows
- Conditional execution
- Context passing between steps

## Data Flow

### Task Creation Flow

```
1. Client sends POST /tasks
2. API validates request
3. Check idempotency key
4. Create task in Supabase
5. Enqueue in Redis
6. Return task ID
```

### Task Execution Flow

```
1. Worker dequeues task (BZPOPMIN)
2. Acquire distributed lock
3. Update status to RUNNING
4. Create run record
5. Execute handler with retries
6. Update task/run status
7. Release lock
8. Update metrics
```

### Webhook Processing Flow

```
1. Webhook received
2. Verify signature
3. Create webhook_process task
4. Enqueue for processing
5. Return acknowledgment
```

## Reliability Features

### Idempotency

- Tasks with same `idempotency_key` are processed once
- Database constraint ensures uniqueness
- API returns existing task if duplicate

### Retries

- Exponential backoff with jitter
- Configurable max retries (default: 6)
- Network errors trigger retry
- 4xx errors (except 429) are terminal

### Distributed Locking

- Redis-based locks with TTL
- Automatic lock extension for long tasks
- Prevents duplicate processing
- Graceful handling of lock failures

### Graceful Shutdown

- Signal handlers for SIGINT/SIGTERM
- Completes current tasks
- Releases locks
- Closes connections cleanly

## Scalability

### Horizontal Scaling

- Multiple workers can run concurrently
- Redis coordinates work distribution
- Locks prevent duplicate processing
- Each worker has unique ID

### Vertical Scaling

- Configurable concurrency per worker
- Semaphore-based task limiting
- Memory-efficient async execution

### Queue Management

- Priority-based task ordering
- Configurable queue size limits
- Dead letter queue for failed tasks

## Observability

### Structured Logging

```json
{
  "timestamp": "2024-01-15T10:30:45Z",
  "level": "INFO",
  "logger": "orchestrator.worker",
  "message": "Processing task",
  "task_id": "uuid",
  "trace_id": "trace-123",
  "worker_id": "worker-01"
}
```

### Metrics

**Prometheus Metrics:**
- `aurea_tasks_total` - Task counter by type/status
- `aurea_queue_depth` - Current queue size
- `aurea_task_duration_seconds` - Task execution time
- `aurea_retries_total` - Retry counter
- `aurea_budget_used_usd` - AI budget tracking

### Tracing

- OpenTelemetry support
- Trace ID propagation
- Span creation for operations
- Context preservation

### Error Tracking

- Sentry integration
- Automatic error capture
- Context enrichment
- Performance monitoring

## Security

### Authentication

- Bearer token for API access
- GitHub App authentication
- Service role for Supabase

### Webhook Security

- HMAC signature verification
- Shared secrets
- Replay attack prevention

### Data Protection

- TLS for Redis (Upstash)
- Encrypted API keys
- Row-level security in Supabase

### Rate Limiting

- Task type limits
- Budget controls for AI
- Concurrency caps

## Configuration

### Environment Variables

```bash
# Core
ENV=production
PORT=8000

# Resources
MAX_CONCURRENCY=8
TASK_LEASE_SECONDS=900
TASK_MAX_RETRIES=6

# Budgets
MODEL_DAILY_BUDGET_USD=25

# Features
ENABLE_SSE=true
ENABLE_METRICS=true
```

### Dynamic Configuration

- Hot-reloadable via environment
- No code changes required
- Render service variables

## Deployment Architecture

### Render Services

```
┌─────────────────────────────────────┐
│         Render Platform             │
├─────────────────────────────────────┤
│  ┌─────────────────────────────┐   │
│  │   Web Service (API)         │   │
│  │   - Auto-scaling            │   │
│  │   - Health checks           │   │
│  │   - Custom domain           │   │
│  └─────────────────────────────┘   │
│                                     │
│  ┌─────────────────────────────┐   │
│  │   Worker Service            │   │
│  │   - Always running          │   │
│  │   - Auto-restart            │   │
│  │   - Resource monitoring     │   │
│  └─────────────────────────────┘   │
│                                     │
│  ┌─────────────────────────────┐   │
│  │   Cron Job (Maintenance)    │   │
│  │   - Daily cleanup           │   │
│  │   - Report generation       │   │
│  └─────────────────────────────┘   │
└─────────────────────────────────────┘
```

### External Dependencies

```
┌──────────────┐     ┌──────────────┐     ┌──────────────┐
│   Upstash    │     │   Supabase   │     │    GitHub    │
│    Redis     │     │   Database   │     │     API      │
└──────────────┘     └──────────────┘     └──────────────┘
       │                    │                      │
       └────────────────────┼──────────────────────┘
                           │
                    ┌──────────────┐
                    │ Orchestrator │
                    └──────────────┘
                           │
       ┌───────────────────┼───────────────────┐
       │                   │                   │
┌──────────────┐    ┌──────────────┐   ┌──────────────┐
│  Anthropic   │    │    OpenAI    │   │    Google    │
│     API      │    │     API      │   │   Gemini     │
└──────────────┘    └──────────────┘   └──────────────┘
```

## Performance Considerations

### Optimization Strategies

1. **Connection Pooling**
   - Reuse HTTP clients
   - Persistent Redis connection
   - Database connection pooling

2. **Caching**
   - Redis for temporary data
   - Local memory for configs
   - CDN for static content

3. **Batch Processing**
   - Group similar operations
   - Bulk database inserts
   - Concurrent API calls

4. **Resource Management**
   - Semaphore for concurrency
   - Memory limits per task
   - Timeout enforcement

## Failure Modes

### Handling Failures

1. **Redis Unavailable**
   - Tasks queue locally
   - Retry connection
   - Alert operations

2. **Database Down**
   - Continue with Redis only
   - Queue writes for later
   - Degrade gracefully

3. **AI Provider Issues**
   - Fallback to alternate providers
   - Retry with backoff
   - Budget-aware retries

4. **Worker Crash**
   - Auto-restart via Render
   - Lock expires naturally
   - Task re-queued

## Future Enhancements

### Planned Features

1. **WebSocket Support**
   - Real-time bidirectional communication
   - Live task updates
   - Interactive sessions

2. **Task Dependencies**
   - DAG-based workflows
   - Conditional execution
   - Parallel branches

3. **Advanced Scheduling**
   - Cron-based tasks
   - Delayed execution
   - Recurring tasks

4. **Multi-Region**
   - Geographic distribution
   - Latency optimization
   - Disaster recovery

5. **Enhanced Observability**
   - Custom dashboards
   - Anomaly detection
   - Cost analytics