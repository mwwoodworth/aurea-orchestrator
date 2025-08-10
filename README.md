# AUREA Orchestrator

Always-on orchestration layer for AUREA that manages cross-AI tasks with reliability, scalability, and observability.

## Features

- 🚀 **Always-On Worker** - Runs 24/7 as a Render Worker for long jobs
- 🌐 **FastAPI Web Service** - REST API, webhooks, health/metrics, and real-time streaming (SSE)
- 🤖 **Multi-AI Support** - Manages tasks across Claude, ChatGPT, and Gemini
- 🔄 **Reliable Execution** - Queue, retries, exponential backoff, idempotency
- 🔗 **Integrations** - Supabase, Redis, GitHub, ClickUp, Make webhooks
- 📊 **Observability** - Sentry, OpenTelemetry, Prometheus metrics
- 🛡️ **Production-Ready** - Docker, CI/CD, comprehensive documentation

## Quick Start

```bash
# Clone repository
git clone https://github.com/your-org/aurea-orchestrator.git
cd aurea-orchestrator

# Install dependencies
pip install -r requirements.txt

# Set up environment
cp .env.example .env
# Edit .env with your configuration

# Run locally
make dev

# Access API
curl http://localhost:8000/health
```

## Task Types

- **code_pr** - Create GitHub PRs with AI-generated code
- **centerpoint_sync** - Synchronize CenterPoint data
- **mrg_deploy** - Deploy MyRoofGenius services
- **gen_content** - Generate content using AI
- **aurea_action** - Composite multi-step workflows

## Example Usage

```bash
# Create a task
curl -X POST http://localhost:8000/tasks \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "type": "gen_content",
    "payload": {
      "prompt": "Write a haiku about distributed systems",
      "model": "claude-3-opus-20240229"
    }
  }'

# Check status
curl http://localhost:8000/tasks/{task_id} \
  -H "Authorization: Bearer $API_KEY"
```

## Architecture

```
Clients → API → Redis Queue → Workers → Handlers → External Services
                     ↓
                 Supabase
                (Persistence)
```

## Documentation

- [Operations Manual](docs/OPERATIONS.md) - Deployment, monitoring, troubleshooting
- [Architecture](docs/ARCHITECTURE.md) - System design and data flows

## Development

```bash
# Run tests
make test

# Lint code
make lint

# Format code
make format

# Build Docker images
make build
```

## Deployment

The system deploys to Render with:
- Web service for API
- Worker service for task execution
- Cron job for maintenance

See [render.yaml](infra/render.yaml) for configuration.

## License

Proprietary - All rights reserved