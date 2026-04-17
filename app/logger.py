"""Structured logging configuration for the application.

Provides both JSON and text formatters for different log levels and environments.
Supports console and file logging with context-aware formatting.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import settings


class JSONFormatter(logging.Formatter):
    """JSON formatter for structured logging."""

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as JSON."""
        log_data = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add exception info if present
        if record.exc_info:
            log_data["exception"] = {
                "type": record.exc_info[0].__name__,
                "message": str(record.exc_info[1]),
                "traceback": self.formatException(record.exc_info),
            }

        # Add extra fields (if passed via logger.info(msg, extra={...}))
        if hasattr(record, "extra_fields") and record.extra_fields:
            log_data.update(record.extra_fields)

        # Add performance metrics if available
        if hasattr(record, "duration_ms"):
            log_data["duration_ms"] = record.duration_ms

        return json.dumps(log_data, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable text formatter for logging."""

    # Color codes for terminal output
    _COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        """Format log record as human-readable text."""
        timestamp = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        level = record.levelname
        logger_name = record.name
        message = record.getMessage()

        # Add colors only to stderr (terminal)
        if sys.stderr.isatty():
            color = self._COLORS.get(level, "")
            level_str = f"{color}{level}{self._RESET}"
        else:
            level_str = level

        # Build base message
        log_line = f"[{timestamp}] {level_str:<20} {logger_name:<25} {message}"

        # Add location info for DEBUG level
        if record.levelno == logging.DEBUG:
            location = f"{record.module}:{record.funcName}:{record.lineno}"
            log_line += f" ({location})"

        # Add exception traceback
        if record.exc_info:
            log_line += f"\n{self.formatException(record.exc_info)}"

        # Add extra fields
        if hasattr(record, "extra_fields") and record.extra_fields:
            extra_str = " | ".join(f"{k}={v}" for k, v in record.extra_fields.items())
            log_line += f" | {extra_str}"

        return log_line


class ContextFilter(logging.Filter):
    """Add contextual information to log records."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Add context to record if available."""
        if not hasattr(record, "extra_fields"):
            record.extra_fields = {}
        return True


def configure_logging() -> None:
    """Configure logging for the application.

    Sets up:
    - Console handler (stdout)
    - File handler (if log_file is configured)
    - Appropriate formatters based on configuration
    """
    # Root logger configuration
    root_logger = logging.getLogger()
    root_logger.setLevel(settings.log_level)

    # Remove existing handlers to avoid duplicates
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add context filter
    root_logger.addFilter(ContextFilter())

    # Select formatter
    formatter = JSONFormatter() if settings.log_format == "json" else TextFormatter()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(settings.log_level)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler (if configured)
    if settings.log_file:
        log_path = Path(settings.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        file_handler = logging.FileHandler(log_path)
        file_handler.setLevel(settings.log_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    # Reduce noise from libraries
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.pool").setLevel(logging.WARNING)
    logging.getLogger("aiosmtplib").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance with the specified name.

    Args:
        name: Logger name (typically __name__)

    Returns:
        Configured logger instance
    """
    return logging.getLogger(name)


class LogContext:
    """Context manager for adding contextual information to logs.

    Usage:
        with LogContext({"email_id": "123", "recipient": "user@example.com"}):
            logger.info("Sending email")  # Will include context
    """

    def __init__(self, context: dict[str, Any]):
        """Initialize context."""
        self.context = context
        self.old_factory = None

    def __enter__(self) -> LogContext:
        """Enter context."""
        self.old_factory = logging.getLogRecordFactory()

        def with_context(**kwargs):
            record = self.old_factory(**kwargs)
            record.extra_fields = self.context
            return record

        logging.setLogRecordFactory(with_context)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context."""
        logging.setLogRecordFactory(self.old_factory)


# Initialize logging on module import
configure_logging()
