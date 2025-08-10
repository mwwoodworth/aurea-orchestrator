"""
Shared utilities and clients for AUREA orchestrator.
"""
from shared.ai_clients import AIClientManager, AnthropicClient, GeminiClient, OpenAIClient
from shared.github_client import GitHubClient
from shared.logging import get_logger, get_task_logger, setup_logging
from shared.redis_client import RedisClient
from shared.schemas import (
    AureaActionPayload,
    CenterPointSyncPayload,
    CodePRPayload,
    ContentGenerationPayload,
    ErrorRecord,
    HealthStatus,
    MRGDeployPayload,
    MetricsSnapshot,
    RunRecord,
    RunStatus,
    Task,
    TaskPriority,
    TaskRequest,
    TaskResult,
    TaskStatus,
    TaskType,
    WebhookPayload,
)
from shared.supabase_client import SupabaseClient

__all__ = [
    # Clients
    "RedisClient",
    "SupabaseClient",
    "GitHubClient",
    "AnthropicClient",
    "OpenAIClient",
    "GeminiClient",
    "AIClientManager",
    # Logging
    "setup_logging",
    "get_logger",
    "get_task_logger",
    # Schemas
    "Task",
    "TaskRequest",
    "TaskResult",
    "TaskStatus",
    "TaskType",
    "TaskPriority",
    "RunRecord",
    "RunStatus",
    "ErrorRecord",
    "WebhookPayload",
    "HealthStatus",
    "MetricsSnapshot",
    "CodePRPayload",
    "CenterPointSyncPayload",
    "MRGDeployPayload",
    "ContentGenerationPayload",
    "AureaActionPayload",
]