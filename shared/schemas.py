"""
Shared data models for the AUREA orchestrator.
"""
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class TaskPriority(int, Enum):
    """Task priority levels."""
    CRITICAL = 1
    HIGH = 10
    NORMAL = 100
    LOW = 1000


class TaskStatus(str, Enum):
    """Task execution status."""
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELED = "canceled"


class TaskType(str, Enum):
    """Supported task types."""
    CODE_PR = "code_pr"
    CENTERPOINT_SYNC = "centerpoint_sync"
    MRG_DEPLOY = "mrg_deploy"
    GEN_CONTENT = "gen_content"
    AUREA_ACTION = "aurea_action"
    WEBHOOK_PROCESS = "webhook_process"
    MAINTENANCE = "maintenance"


class RunStatus(str, Enum):
    """Run execution status."""
    STARTED = "started"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELED = "canceled"


class TaskRequest(BaseModel):
    """Request to create a new task."""
    type: TaskType
    payload: Dict[str, Any]
    priority: TaskPriority = TaskPriority.NORMAL
    trace_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class Task(BaseModel):
    """Task model for orchestration."""
    id: UUID = Field(default_factory=uuid4)
    type: TaskType
    payload: Dict[str, Any]
    priority: TaskPriority = TaskPriority.NORMAL
    status: TaskStatus = TaskStatus.QUEUED
    trace_id: Optional[str] = None
    idempotency_key: Optional[str] = None
    enqueued_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    last_error: Optional[str] = None
    retry_count: int = 0
    metadata: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        json_encoders = {
            UUID: str,
            datetime: lambda v: v.isoformat()
        }


class TaskResult(BaseModel):
    """Result of task execution."""
    task_id: UUID
    status: TaskStatus
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration_seconds: Optional[float] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)

    class Config:
        json_encoders = {
            UUID: str
        }


class RunRecord(BaseModel):
    """Record of a task execution run."""
    id: UUID = Field(default_factory=uuid4)
    task_id: UUID
    started_at: datetime = Field(default_factory=datetime.utcnow)
    ended_at: Optional[datetime] = None
    status: RunStatus = RunStatus.STARTED
    attempts: int = 1
    metrics: Dict[str, Any] = Field(default_factory=dict)
    logs_url: Optional[str] = None
    error_details: Optional[Dict[str, Any]] = None

    class Config:
        json_encoders = {
            UUID: str,
            datetime: lambda v: v.isoformat()
        }


class ErrorRecord(BaseModel):
    """Structured error information."""
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None
    traceback: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    context: Dict[str, Any] = Field(default_factory=dict)


class WebhookPayload(BaseModel):
    """Incoming webhook payload."""
    source: str
    event_type: str
    data: Dict[str, Any]
    signature: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class HealthStatus(BaseModel):
    """Health check response."""
    status: str
    version: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    checks: Dict[str, bool] = Field(default_factory=dict)


class MetricsSnapshot(BaseModel):
    """Metrics snapshot for observability."""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    queue_depth: int = 0
    tasks_per_hour: float = 0.0
    retry_rate: float = 0.0
    hard_fail_rate: float = 0.0
    avg_duration_seconds: float = 0.0
    p95_duration_seconds: float = 0.0
    active_workers: int = 0
    budget_used_usd: float = 0.0
    budget_limit_usd: float = 25.0


class CodePRPayload(BaseModel):
    """Payload for code PR tasks."""
    repo_url: str
    base_branch: str = "main"
    goals: List[str]
    constraints: List[str] = Field(default_factory=list)
    files_to_modify: List[str] = Field(default_factory=list)
    test_command: Optional[str] = None
    pr_title: str
    pr_description: Optional[str] = None


class CenterPointSyncPayload(BaseModel):
    """Payload for CenterPoint sync tasks."""
    sync_type: str  # "full", "incremental", "status"
    entities: List[str] = Field(default_factory=list)
    since_timestamp: Optional[datetime] = None
    force: bool = False


class MRGDeployPayload(BaseModel):
    """Payload for MyRoofGenius deployment tasks."""
    environment: str  # "staging", "production"
    version: Optional[str] = None
    services: List[str] = Field(default_factory=list)
    rollback_on_failure: bool = True


class ContentGenerationPayload(BaseModel):
    """Payload for content generation tasks."""
    prompt: str
    model: str = "claude-3-opus-20240229"
    max_tokens: int = 4000
    temperature: float = 0.7
    output_format: str = "markdown"
    save_to_storage: bool = True


class AureaActionPayload(BaseModel):
    """Payload for composite AUREA actions."""
    workflow: str
    steps: List[Dict[str, Any]]
    context: Dict[str, Any] = Field(default_factory=dict)
    timeout_seconds: int = 3600