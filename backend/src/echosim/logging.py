import json
import logging
import sys
from datetime import UTC, datetime

_FIELDS = (
    "service",
    "request_id",
    "method",
    "status",
    "duration_ms",
    "dependency",
    "model",
    "configuration_version",
    "prompt_version",
    "timeout_seconds",
    "max_tokens",
    "temperature",
    "usage",
    "error_code",
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        result = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service", "runtime"),
            "event": record.getMessage() if record.name == "echosim" else "server_lifecycle",
            "request_id": getattr(record, "request_id", None),
        }
        for name in _FIELDS:
            if hasattr(record, name):
                result[name] = getattr(record, name)
        return json.dumps(result, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True


def emit(event: str, *, service: str, request_id: str | None = None, **fields) -> None:
    logging.getLogger("echosim").info(
        event, extra={"service": service, "request_id": request_id, **fields}
    )
