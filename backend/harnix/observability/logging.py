"""Structured (JSON-lines) application logging with secret redaction."""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

from harnix.policies.redaction import Redactor


class JsonFormatter(logging.Formatter):
    def __init__(self, redactor: Redactor):
        super().__init__()
        self.redactor = redactor

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return self.redactor.redact(json.dumps(payload, ensure_ascii=False))


def configure_logging(level: str, redactor: Redactor) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter(redactor))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
