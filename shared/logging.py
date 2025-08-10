"""
Structured logging configuration for AUREA orchestrator.
"""
import json
import logging
import sys
from datetime import datetime
from typing import Any, Dict, Optional
from uuid import UUID


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_obj = {
            "timestamp": datetime.utcnow().isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add extra fields
        if hasattr(record, "task_id"):
            log_obj["task_id"] = str(record.task_id) if isinstance(record.task_id, UUID) else record.task_id
        if hasattr(record, "trace_id"):
            log_obj["trace_id"] = record.trace_id
        if hasattr(record, "worker_id"):
            log_obj["worker_id"] = record.worker_id
        if hasattr(record, "extra_data"):
            log_obj["data"] = record.extra_data

        # Add exception info if present
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_obj)


class TaskLogger:
    """Logger with task context."""

    def __init__(self, logger: logging.Logger, task_id: Optional[UUID] = None, trace_id: Optional[str] = None):
        self.logger = logger
        self.task_id = task_id
        self.trace_id = trace_id

    def _log(self, level: int, msg: str, extra_data: Optional[Dict[str, Any]] = None, **kwargs):
        """Log with task context."""
        extra = kwargs.get("extra", {})
        if self.task_id:
            extra["task_id"] = self.task_id
        if self.trace_id:
            extra["trace_id"] = self.trace_id
        if extra_data:
            extra["extra_data"] = extra_data
        kwargs["extra"] = extra
        self.logger.log(level, msg, **kwargs)

    def debug(self, msg: str, **kwargs):
        self._log(logging.DEBUG, msg, **kwargs)

    def info(self, msg: str, **kwargs):
        self._log(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, **kwargs):
        self._log(logging.WARNING, msg, **kwargs)

    def error(self, msg: str, **kwargs):
        self._log(logging.ERROR, msg, **kwargs)

    def exception(self, msg: str, **kwargs):
        self._log(logging.ERROR, msg, exc_info=True, **kwargs)


def setup_logging(level: str = "INFO", json_output: bool = True) -> None:
    """Configure logging for the application."""
    log_level = getattr(logging, level.upper(), logging.INFO)
    
    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)
    
    # Clear existing handlers
    root_logger.handlers.clear()
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    
    if json_output:
        console_handler.setFormatter(JSONFormatter())
    else:
        console_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
            )
        )
    
    root_logger.addHandler(console_handler)
    
    # Suppress noisy loggers
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance."""
    return logging.getLogger(name)


def get_task_logger(name: str, task_id: Optional[UUID] = None, trace_id: Optional[str] = None) -> TaskLogger:
    """Get a task-aware logger instance."""
    return TaskLogger(get_logger(name), task_id, trace_id)