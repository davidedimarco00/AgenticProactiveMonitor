from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from pydantic import BaseModel, Field

SERVICE_NAME = "processing-service"
HOST_ID = os.getenv("HOST_ID", SERVICE_NAME)
MACHINE_ROLE = os.getenv("MACHINE_ROLE", SERVICE_NAME)
LOG_FILE = Path(os.getenv("APP_LOG_FILE", "/var/log/machine/app.log"))
PROCESSING_MULTIPLIER = float(os.getenv("PROCESSING_MULTIPLIER", "2"))
PROCESSING_DELAY_MS = int(os.getenv("PROCESSING_DELAY_MS", "50"))


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def write_log(
    *,
    level: str,
    event_type: str,
    message: str,
    request_id: str | None = None,
    **fields: object,
) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    event = {
        "timestamp": utc_timestamp(),
        "host": HOST_ID,
        "machine_role": MACHINE_ROLE,
        "service": SERVICE_NAME,
        "event_type": event_type,
        "level": level,
        "message": message,
        **fields,
    }

    if request_id is not None:
        event["request_id"] = request_id

    with LOG_FILE.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(event, separators=(",", ":")) + "\n")


class ProcessRequest(BaseModel):
    value: float = Field(description="Numeric value to process")
    request_id: str | None = Field(
        default=None,
        description="Optional request identifier propagated across services",
    )


class ProcessResponse(BaseModel):
    request_id: str
    input_value: float
    result: float
    service: str
    processing_time_ms: float


@asynccontextmanager
async def lifespan(_: FastAPI):
    write_log(
        level="INFO",
        event_type="service_started",
        message="Processing service started",
        multiplier=PROCESSING_MULTIPLIER,
        configured_delay_ms=PROCESSING_DELAY_MS,
    )
    yield
    write_log(
        level="INFO",
        event_type="service_stopped",
        message="Processing service stopped",
    )


app = FastAPI(
    title="Monitored Processing Service",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "host": HOST_ID,
    }


@app.post("/process", response_model=ProcessResponse)
async def process(payload: ProcessRequest) -> ProcessResponse:
    request_id = payload.request_id or str(uuid.uuid4())
    start_time = time.perf_counter()

    if PROCESSING_DELAY_MS > 0:
        await asyncio.sleep(PROCESSING_DELAY_MS / 1000)

    result = payload.value * PROCESSING_MULTIPLIER
    processing_time_ms = round((time.perf_counter() - start_time) * 1000, 3)

    write_log(
        level="INFO",
        event_type="request_processed",
        message="Processing request completed",
        request_id=request_id,
        input_value=payload.value,
        result=result,
        multiplier=PROCESSING_MULTIPLIER,
        latency_ms=processing_time_ms,
    )

    return ProcessResponse(
        request_id=request_id,
        input_value=payload.value,
        result=result,
        service=SERVICE_NAME,
        processing_time_ms=processing_time_ms,
    )
