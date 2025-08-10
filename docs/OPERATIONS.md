# AUREA Orchestrator Operations Manual v1.1

## Overview

The AUREA Orchestrator is a production-hardened, always-on orchestration layer that manages cross-AI tasks with enterprise-grade reliability, security, and observability. Version 1.1 introduces autoscaling, enhanced security, budget controls, and comprehensive monitoring.

## Environment Setup

### Required Environment Variables

```bash
# Core Configuration
PORT=8000
ENV=production
ENVIRONMENT=production  # or staging
LOG_LEVEL=INFO

# Security
API_KEY_SALT=your-salt-for-api-keys
WEBHOOK_SECRET=your-webhook-secret
JWT_SECRET=your-jwt-secret
INTERNAL_KEY=your-internal-api-key

# Redis
REDIS_URL=redis://default:password@redis.upstash.io:6379
REDIS_TLS=true

# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key

# GitHub App
GITHUB_APP_ID=your-app-id
GITHUB_INSTALLATION_ID=your-installation-id
GITHUB_PRIVATE_KEY_BASE64=base64-encoded-private-key

# AI Providers
ANTHROPIC_API_KEY=your-anthropic-key
OPENAI_API_KEY=your-openai-key
GOOGLE_API_KEY=your-google-key

# Observability
SENTRY_DSN=your-sentry-dsn
OTEL_EXPORTER_OTLP_ENDPOINT=https://otel-collector.example.com

# Rate Limits & Controls
MAX_CONCURRENCY=10
WORKER_CONCURRENCY=10
WORKER_REPLICAS=2
TASK_LEASE_SECONDS=900
TASK_MAX_RETRIES=3
TASK_BACKOFF_MAX_SEC=60

# Budget Controls
MODEL_DAILY_BUDGET_USD=100
TASK_MAX_TOKENS=4000
MAX_QUEUE_DEPTH=1000

# Circuit Breaker
CIRCUIT_BREAKER_THRESHOLD=0.1
CIRCUIT_BREAKER_TIMEOUT=600
```

## Local Development

### Prerequisites

- Python 3.11+
- Docker (for Redis)
- Node.js 18+ (for CenterPoint sync)

### Setup

```bash
# Clone repository
git clone https://github.com/your-org/aurea-orchestrator.git
cd aurea-orchestrator

# Install dependencies
make install

# Copy environment file
cp .env.example .env
# Edit .env with your configuration

# Run database migrations
psql $DATABASE_URL < migrations/001_initial.sql
psql $DATABASE_URL < migrations/002_outbox_inbox.sql

# Or in Supabase SQL Editor:
# 1. Copy contents of migrations/001_initial.sql
# 2. Copy contents of migrations/002_outbox_inbox.sql
```

### Running Locally

```bash
# Start development environment (API + Worker + Redis)
make dev

# Or run components separately:

# Start Redis
docker run -d --name aurea-redis -p 6379:6379 redis:alpine

# Start API
ENV=development python -m uvicorn app.api.main:app --reload --port 8000

# Start Worker
ENV=development python -m orchestrator.worker
```

### Testing

```bash
# Run all tests
make test

# Run linting
make lint

# Format code
make format

# Type checking
make type-check
```

## Task Examples

### 1. Code PR Task

Create a pull request with automated code changes:

```bash
curl -X POST https://aurea-orchestrator-api.onrender.com/tasks \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "code_pr",
    "payload": {
      "repo_url": "https://github.com/your-org/your-repo",
      "base_branch": "main",
      "goals": [
        "Add error handling to API endpoints",
        "Improve logging"
      ],
      "constraints": [
        "Maintain backward compatibility",
        "Follow existing code style"
      ],
      "files_to_modify": ["src/api/handlers.py"],
      "test_command": "pytest tests/",
      "pr_title": "feat: Improve error handling and logging",
      "pr_description": "This PR adds comprehensive error handling..."
    },
    "priority": 10
  }'
```

### 2. CenterPoint Sync Task

Run a CenterPoint synchronization:

```bash
curl -X POST https://aurea-orchestrator-api.onrender.com/tasks \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "centerpoint_sync",
    "payload": {
      "sync_type": "incremental",
      "entities": ["customers", "jobs", "invoices"],
      "since_timestamp": "2024-01-01T00:00:00Z",
      "force": false
    },
    "priority": 100
  }'
```

### 3. MyRoofGenius Deploy Task

Trigger a deployment:

```bash
curl -X POST https://aurea-orchestrator-api.onrender.com/tasks \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "mrg_deploy",
    "payload": {
      "environment": "production",
      "version": "v3.1.251",
      "services": ["api", "frontend"],
      "rollback_on_failure": true
    },
    "priority": 1
  }'
```

### 4. Content Generation Task

Generate content using AI:

```bash
curl -X POST https://aurea-orchestrator-api.onrender.com/tasks \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "gen_content",
    "payload": {
      "prompt": "Write a technical blog post about microservices architecture",
      "model": "claude-3-opus-20240229",
      "max_tokens": 4000,
      "temperature": 0.7,
      "output_format": "markdown",
      "save_to_storage": true
    }
  }'
```

### 5. Composite AUREA Action

Execute a multi-step workflow:

```bash
curl -X POST https://aurea-orchestrator-api.onrender.com/tasks \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "aurea_action",
    "payload": {
      "workflow": "data_analysis",
      "steps": [
        {
          "name": "fetch_data",
          "type": "tool_call",
          "tool": "database_query",
          "params": {"query": "SELECT * FROM metrics"}
        },
        {
          "name": "analyze",
          "type": "ai_task",
          "prompt": "Analyze this data: {step_1_result}",
          "max_tokens": 2000
        },
        {
          "name": "generate_report",
          "type": "ai_task",
          "prompt": "Create a report based on: {step_2_result}"
        }
      ],
      "timeout_seconds": 3600
    }
  }'
```

## Check Task Status

```bash
# Get task status
curl -X GET https://aurea-orchestrator-api.onrender.com/tasks/{task_id} \
  -H "Authorization: Bearer $API_KEY"

# Stream task logs (SSE)
curl -X GET https://aurea-orchestrator-api.onrender.com/stream/{task_id} \
  -H "Authorization: Bearer $API_KEY" \
  -H "Accept: text/event-stream"
```

## Admin Endpoints

### View Recent Runs

```bash
curl -X GET https://aurea-orchestrator-api.onrender.com/admin/runs?limit=50 \
  -H "Authorization: Bearer $API_KEY"
```

### Metrics

```bash
# Prometheus metrics
curl https://aurea-orchestrator-api.onrender.com/metrics

# Health check
curl https://aurea-orchestrator-api.onrender.com/health
```

## Deployment

### Deploy to Render

1. **Initial Setup:**
   ```bash
   # Create services in Render dashboard
   # Configure environment variables
   # Connect GitHub repository
   ```

2. **Manual Deploy:**
   ```bash
   # Push to main branch triggers auto-deploy
   git push origin main
   
   # Or use Render API
   curl -X POST \
     "https://api.render.com/v1/services/$SERVICE_ID/deploys" \
     -H "Authorization: Bearer $RENDER_API_KEY"
   ```

3. **Monitor Deployment:**
   - Check Render dashboard for deployment status
   - Monitor logs in Render
   - Verify health check: `curl https://your-service.onrender.com/health`

## Production Deployment (v1.1+)

### Staging to Production Cutover

1. **Deploy to Staging:**
   ```bash
   # Set staging environment variables
   render env:set --group staging
   
   # Deploy to staging
   git push origin main
   
   # Wait for deployment
   curl -f https://aurea-orchestrator-api-staging.onrender.com/health
   ```

2. **Run Smoke Tests:**
   ```bash
   # Trigger GitHub Action
   gh workflow run post_deploy_validation.yml -f environment=staging
   
   # Or run locally
   ./tests/smoke_test.sh staging
   ```

3. **Canary Deployment:**
   ```bash
   # Deploy 1 worker replica first
   render service:update aurea-orchestrator-worker --num-instances 1
   
   # Monitor for 15 minutes
   watch 'curl -s https://aurea-orchestrator-api.onrender.com/metrics | grep error_rate'
   
   # If stable, scale to full
   render service:update aurea-orchestrator-worker --num-instances 2
   ```

4. **Promote to Production:**
   ```bash
   # Copy environment group
   render env:copy --from staging --to production
   
   # Deploy production
   render deploy --service aurea-orchestrator-api
   render deploy --service aurea-orchestrator-worker
   ```

## Rollback & Disaster Recovery

### Rollback Procedure

1. **Quick Rollback (< 5 minutes):**
   ```bash
   # Scale workers to zero immediately
   render service:scale aurea-orchestrator-worker --count 0
   
   # Rollback API
   render deploy:rollback aurea-orchestrator-api
   
   # Rollback workers
   render deploy:rollback aurea-orchestrator-worker
   
   # Scale workers back up
   render service:scale aurea-orchestrator-worker --count 2
   ```

2. **Full Rollback with Queue Drain:**
   ```bash
   # Stop workers
   render service:scale aurea-orchestrator-worker --count 0
   
   # Drain queue to prevent task loss
   python bin/dlq-drain.py drain --redis-url $REDIS_URL --dry-run
   python bin/dlq-drain.py drain --redis-url $REDIS_URL --max 1000
   
   # Rollback services
   render deploy:rollback aurea-orchestrator-api --to <deployment-id>
   render deploy:rollback aurea-orchestrator-worker --to <deployment-id>
   
   # Restart workers
   render service:scale aurea-orchestrator-worker --count 2
   ```

3. **Database Rollback (if needed):**
   ```sql
   -- Connect to Supabase SQL Editor
   -- Restore from backup or run rollback scripts
   ```

### Disaster Recovery

1. **Service Down:**
   - Check Render service status
   - Verify Redis connectivity
   - Check Supabase status
   - Review error logs

2. **Data Recovery:**
   - Supabase provides automatic backups
   - Redis data can be restored from snapshots
   - Task history is preserved in Supabase

3. **Emergency Maintenance Mode:**
   ```bash
   # Scale workers to 0
   # Put API in maintenance mode
   # Fix issues
   # Scale back up
   ```

## Operational Playbooks

### Circuit Breaker Open

**Symptoms:**
- Metrics show `aurea_circuit_open_total > 0`
- Tasks failing with `CircuitOpenError`

**Resolution:**
```bash
# Check which service has open circuit
curl -s $API_URL/metrics | grep circuit_open

# Check circuit state in database
psql $DATABASE_URL -c "SELECT * FROM orchestrator_circuit_breakers WHERE state = 'open'"

# Manual reset if needed (after fixing root cause)
psql $DATABASE_URL -c "UPDATE orchestrator_circuit_breakers SET state = 'closed', next_retry_at = NOW() WHERE service = 'SERVICE_NAME'"
```

### DLQ Growing

**Symptoms:**
- `aurea_dlq_total` increasing
- Tasks failing after max retries

**Resolution:**
```bash
# Check DLQ stats
python bin/dlq-drain.py stats --redis-url $REDIS_URL

# Investigate failures
curl -s $API_URL/admin/runs?status=failed&limit=10 | jq '.[] | {task_id, type, error}'

# Drain selectively after fixing issue
python bin/dlq-drain.py drain --filter-type problematic_type --max 50
```

### Budget Exceeded

**Symptoms:**
- Tasks rejected with `BudgetExceededError`
- `aurea_budget_remaining_usd` near 0

**Resolution:**
```bash
# Check current spend
curl -s $API_URL/metrics | grep budget

# Emergency budget increase
render env:set MODEL_DAILY_BUDGET_USD=150

# Investigate high-cost tasks
psql $DATABASE_URL -c "SELECT type, COUNT(*), AVG(model_cost_usd) FROM orchestrator_runs WHERE created_at > NOW() - INTERVAL '24 hours' GROUP BY type ORDER BY AVG(model_cost_usd) DESC"
```

### Webhook Replay Attack

**Symptoms:**
- `aurea_inbox_replay_blocked_total` increasing
- Duplicate webhook attempts in logs

**Resolution:**
```bash
# Check blocked webhooks
psql $DATABASE_URL -c "SELECT source, external_id, COUNT(*) FROM orchestrator_inbox WHERE status = 'rejected' AND rejection_reason LIKE '%replay%' GROUP BY source, external_id"

# Rotate webhook secret if compromised
NEW_SECRET=$(openssl rand -hex 32)
render env:set WEBHOOK_SECRET=$NEW_SECRET

# Update webhook configuration in GitHub/ClickUp/Make
```

## Budget Management

### Monitor AI Usage

```bash
# Real-time budget status
curl -s $API_URL/metrics | grep -E "budget_(spent|remaining)_usd"

# Historical analysis
psql $DATABASE_URL << SQL
SELECT 
  date,
  provider,
  budget_usd,
  spent_usd,
  token_count,
  request_count,
  ROUND(spent_usd::numeric / NULLIF(request_count, 0), 4) as avg_cost_per_request
FROM orchestrator_budgets
WHERE date >= CURRENT_DATE - INTERVAL '7 days'
ORDER BY date DESC, provider;
SQL
```

### Safely Increase Budget

1. Monitor current usage patterns
2. Identify high-cost operations
3. Gradually increase budget:
   - Start with 10% increase
   - Monitor for 24 hours
   - Adjust as needed

## Monitoring & Alerts

### Key Metrics to Monitor

- **Queue Depth:** `aurea_queue_depth`
- **Task Success Rate:** `aurea_tasks_total{status="done"}`
- **Task Duration:** `aurea_task_duration_seconds`
- **Retry Rate:** `aurea_retries_total`
- **Budget Usage:** `aurea_budget_used_usd`

### Setting Up Alerts

1. **Sentry Alerts:**
   - Error rate > 5% in 5 minutes
   - New error types
   - Performance degradation

2. **Prometheus Alerts:**
   ```yaml
   - alert: HighQueueDepth
     expr: aurea_queue_depth > 1000
     for: 5m
     
   - alert: HighFailureRate
     expr: rate(aurea_tasks_total{status="failed"}[5m]) > 0.1
     for: 5m
   
   - alert: BudgetNearLimit
     expr: aurea_budget_used_usd > 20
     for: 1m
   ```

## Security Operations

### API Key Management

```bash
# Create new API key
python << EOF
import asyncio
import asyncpg
from shared.security import APIKeyManager, UserRole

async def create_key():
    pool = await asyncpg.create_pool("$DATABASE_URL")
    manager = APIKeyManager(pool, "$API_KEY_SALT")
    key_id, raw_key = await manager.create_key(
        name="production_service",
        role=UserRole.SERVICE,
        description="Production service key",
        expires_in_days=90,
        created_by="ops"
    )
    print(f"Key ID: {key_id}")
    print(f"API Key: {raw_key}")
    print("Save this key securely - it cannot be retrieved again!")
    await pool.close()

asyncio.run(create_key())
EOF

# Rotate API key
python bin/rotate-api-key.py OLD_KEY_ID --overlap-minutes 60

# List active keys
psql $DATABASE_URL -c "SELECT id, name, role, created_at, last_used_at FROM orchestrator_api_keys WHERE is_active = true"

# Revoke compromised key
psql $DATABASE_URL -c "UPDATE orchestrator_api_keys SET is_active = false WHERE id = 'KEY_ID'"
```

### Webhook Secret Rotation

```bash
# Generate new secret
NEW_SECRET=$(openssl rand -hex 32)
echo "New webhook secret: $NEW_SECRET"

# Update in Render (staged rollout)
render env:set WEBHOOK_SECRET=$NEW_SECRET --service aurea-orchestrator-api

# Update in external services:
# - GitHub: Settings → Webhooks → Edit → Secret
# - ClickUp: App Settings → Webhooks → Update
# - Make: Scenario → Webhook module → Update
```

## Troubleshooting

### Common Issues

1. **Tasks Not Processing:**
   - Check worker logs
   - Verify Redis connection
   - Check for lock issues
   - Restart worker if needed

2. **High Failure Rate:**
   - Review error logs
   - Check external service status
   - Verify API keys
   - Check rate limits

3. **Slow Performance:**
   - Monitor queue depth
   - Check concurrent task limit
   - Review task duration metrics
   - Scale workers if needed

4. **Budget Exceeded:**
   - Review task types consuming budget
   - Optimize prompts
   - Implement caching
   - Increase budget if justified

## Security Considerations

1. **API Keys:**
   - Rotate regularly
   - Use separate keys per environment
   - Never commit to repository

2. **Webhook Security:**
   - Always verify signatures
   - Use HTTPS only
   - Implement rate limiting

3. **Database Security:**
   - Use service role key only in backend
   - Enable RLS policies
   - Regular backups

4. **Network Security:**
   - Use TLS for Redis
   - Encrypt sensitive data
   - Implement IP allowlisting where possible

## Maintenance Schedule

### Daily
- Review error logs
- Check budget usage
- Monitor queue depth

### Weekly
- Review performance metrics
- Update dependencies
- Test disaster recovery

### Monthly
- Rotate API keys
- Review and optimize slow queries
- Capacity planning

## Operational Scripts

### Task Enqueue Helper

```bash
# Basic usage
./bin/enqueue.sh gen_content -p '{"prompt": "Test content"}'

# With priority and idempotency
./bin/enqueue.sh code_pr -f request.json --priority high --idempotency pr-fix-123

# Check result
curl -s $API_URL/tasks/$TASK_ID | jq '.status'
```

### DLQ Management

```bash
# View DLQ statistics
python bin/dlq-drain.py stats

# Drain with dry run
python bin/dlq-drain.py drain --dry-run --max 10

# Drain specific task type
python bin/dlq-drain.py drain --filter-type gen_content --max 50

# Drain all with lower priority
python bin/dlq-drain.py drain --max 1000
```

### API Key Rotation

```bash
# Automated rotation
python bin/rotate-api-key.py $OLD_KEY_ID --overlap-minutes 60

# Manual rotation
# 1. Create new key
# 2. Update services to use new key
# 3. Wait for overlap period
# 4. Revoke old key
```

## Monitoring Dashboards

### Grafana Configuration

```yaml
# Import these queries for key metrics

# Task Processing Rate
rate(aurea_tasks_total[5m])

# Error Rate
rate(aurea_tasks_total{status="failed"}[5m]) / rate(aurea_tasks_total[5m])

# P95 Latency
aurea_task_duration_seconds{quantile="0.95"}

# Budget Utilization
aurea_budget_spent_usd / 100

# Queue Depth
aurea_queue_depth

# Circuit Breaker Status
aurea_circuit_open_total
```

## Support

For issues or questions:
1. Check health: `curl $API_URL/health`
2. Review metrics: `curl $API_URL/metrics`
3. Check logs in Render dashboard
4. Review Sentry for errors
5. Run diagnostics: `curl $API_URL/internal/health/deps`
6. Consult this operations manual
7. Contact the development team

## Appendix: Quick Commands

```bash
# Health check
curl -f $API_URL/health

# Dependencies check
curl $API_URL/internal/health/deps | jq

# Current metrics
curl -s $API_URL/metrics | grep -E "(queue_depth|throughput|error_rate|budget)"

# Recent failures
curl -H "Authorization: Bearer $API_KEY" $API_URL/admin/runs?status=failed&limit=5 | jq

# Queue stats
curl -s $API_URL/metrics | grep -E "(queue|dlq|pending)"

# Budget status
curl -s $API_URL/metrics | grep budget

# Circuit breaker status
curl -s $API_URL/metrics | grep circuit
```