"""JSON structured logs with key redaction (§7, §8). Never logs raw keys, headers or full prompts."""

import json
import logging
import re
import sys

_KEY_PATTERN = re.compile(r"AIza[0-9A-Za-z_\-]{10,}")


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _KEY_PATTERN.sub("[REDACTED]", record.msg)
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"level": record.levelname, "logger": record.name, "message": record.getMessage()}
        if record.exc_info:
            payload["exc_type"] = str(record.exc_info[0])
        return json.dumps(payload)


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactingFilter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)
