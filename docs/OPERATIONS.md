# AUREA Orchestrator Operations Manual

## Overview

The AUREA Orchestrator is an always-on orchestration layer that manages cross-AI tasks, integrates with multiple services, and provides reliable task execution with retries, idempotency, and observability.

## Environment Setup

### Required Environment Variables

```bash
# Core Configuration
PORT=8000
ENV=production
API_KEY=your-secure-api-key
WEBHOOK_SECRET=your-webhook-secret

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

# Rate Limits
MAX_CONCURRENCY=8
TASK_LEASE_SECONDS=900
TASK_MAX_RETRIES=6
MODEL_DAILY_BUDGET_USD=25
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

# Run database migrations in Supabase SQL Editor
# Copy contents of infra/migrations/001_create_orchestrator_tables.sql
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

## Rollback & Disaster Recovery

### Rollback Procedure

1. **Identify Issue:**
   ```bash
   # Check health
   curl https://aurea-orchestrator-api.onrender.com/health
   
   # Check recent errors in Sentry
   # Review logs in Render
   ```

2. **Rollback in Render:**
   - Go to Render dashboard
   - Select the service
   - Click "Rollback" and select previous deployment

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

## Budget Management

### Monitor AI Usage

```bash
# Check current usage
curl https://aurea-orchestrator-api.onrender.com/metrics | grep budget

# Adjust budget
# Update MODEL_DAILY_BUDGET_USD in environment variables
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

## Support

For issues or questions:
- Check logs in Render dashboard
- Review Sentry for errors
- Consult this operations manual
- Contact the development team