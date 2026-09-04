from __future__ import annotations

import json
import os
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

LOG_FILE = Path(os.getenv("APP_LOG_FILE", "/var/log/machine/app.log"))
MAX_STACK_TRACE_CHARS = int(os.getenv("MAX_STACK_TRACE_CHARS", "12000"))


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_request_id() -> str:
    return str(uuid.uuid4())


def write_log(
    *,
    service: str,
    level: str,
    event_type: str,
    message: str,
    request_id: str | None = None,
    **fields: object,
) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    event = {
        "timestamp": utc_timestamp(),
        "host": os.getenv("HOST_ID", service),
        "machine_role": os.getenv("MACHINE_ROLE", service),
        "service": service,
        "event_type": event_type,
        "level": level,
        "message": message,
        **fields,
    }

    if request_id:
        event["request_id"] = request_id

    with LOG_FILE.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(event, separators=(",", ":")) + "\n")


def write_exception(
    *,
    service: str,
    event_type: str,
    message: str,
    exception: BaseException,
    request_id: str | None = None,
    **fields: object,
) -> None:
    stack_trace = "".join(
        traceback.format_exception(
            type(exception),
            exception,
            exception.__traceback__,
        )
    )
    stack_trace_truncated = len(stack_trace) > MAX_STACK_TRACE_CHARS

    write_log(
        service=service,
        level="ERROR",
        event_type=event_type,
        message=message,
        request_id=request_id,
        error_type=type(exception).__name__,
        error_message=str(exception),
        stack_trace=stack_trace[:MAX_STACK_TRACE_CHARS],
        stack_trace_truncated=stack_trace_truncated,
        **fields,
    )
