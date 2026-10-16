from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import ClassVar

from app.core.config import LoggingSettings


class JsonFormatter(logging.Formatter):
    RESERVED: ClassVar[set[str]] = {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "thread",
        "threadName",
        "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self.RESERVED and not key.startswith("_"):
                payload[key] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["exceptionType"] = record.exc_info[0].__name__
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(settings: LoggingSettings, *, bounded: bool = False) -> None:
    if bounded:
        for handler in logging.getLogger().handlers[:]:
            logging.getLogger().removeHandler(handler)
            handler.close()
    log_file = Path(settings.file)
    log_file.parent.mkdir(parents=True, exist_ok=True)
    formatter = JsonFormatter()

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    if bounded:
        if log_file.is_symlink():
            raise ValueError("Cloud log file cannot be a symlink")
        for index in range(1, 4):
            prior = Path(str(log_file) + "." + str(index))
            if prior.is_symlink():
                raise ValueError("Cloud rotated log cannot be a symlink")
            if prior.exists() and prior.stat().st_mtime < time.time() - 14 * 86400:
                prior.unlink()
        rotating = RotatingFileHandler(log_file, maxBytes=2 * 1024**2,
                                       backupCount=3, encoding="utf-8")
        if log_file.stat().st_size:
            rotating.doRollover()  # Daily backup restarts bound each retained run's time span.
        file_handler: logging.FileHandler = rotating
    else:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(settings.level)
    root.addHandler(console)
    root.addHandler(file_handler)
