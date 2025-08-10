# AUREA Orchestrator Makefile

.PHONY: help install dev test lint format type-check build deploy clean

help:
	@echo "AUREA Orchestrator - Available commands:"
	@echo "  make install    - Install dependencies"
	@echo "  make dev        - Run local development environment"
	@echo "  make test       - Run tests"
	@echo "  make lint       - Run linting"
	@echo "  make format     - Format code"
	@echo "  make type-check - Run type checking"
	@echo "  make build      - Build Docker images"
	@echo "  make deploy     - Deploy to Render"
	@echo "  make clean      - Clean up"

install:
	pip install -r requirements.txt

dev:
	@echo "Starting local development environment..."
	@echo "Starting Redis..."
	docker run -d --name aurea-redis -p 6379:6379 redis:alpine || true
	@echo "Starting API..."
	ENV=development python -m uvicorn app.api.main:app --reload --port 8000 &
	@echo "Starting Worker..."
	ENV=development python -m orchestrator.worker &
	@echo "Development environment started!"
	@echo "API: http://localhost:8000"
	@echo "Docs: http://localhost:8000/docs"

test:
	pytest tests/ -v --asyncio-mode=auto

lint:
	ruff check app/ orchestrator/ shared/ tests/

format:
	black app/ orchestrator/ shared/ tests/
	ruff check --fix app/ orchestrator/ shared/ tests/

type-check:
	mypy app/ orchestrator/ shared/ --ignore-missing-imports

build:
	docker build -f infra/Dockerfile.api -t aurea-orchestrator-api .
	docker build -f infra/Dockerfile.worker -t aurea-orchestrator-worker .

deploy:
	@echo "Deploying to Render..."
	@echo "Please use Render dashboard or CLI to deploy"
	@echo "Or commit and push to trigger auto-deploy"

clean:
	docker stop aurea-redis || true
	docker rm aurea-redis || true
	find . -type d -name "__pycache__" -exec rm -rf {} + || true
	find . -type f -name "*.pyc" -delete || true
	rm -rf .pytest_cache/ || true
	rm -rf .mypy_cache/ || true
	rm -rf .ruff_cache/ || true