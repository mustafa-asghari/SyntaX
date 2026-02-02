"""
Request-scoped performance telemetry for FastAPI handlers.

Collects stage timings in a context variable, emits optional stage logs,
and builds Server-Timing response headers.
"""

from __future__ import annotations

import contextvars
import os
import time
import uuid
from contextlib import contextmanager
from typing import Any

import orjson


_ctx: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "request_telemetry_ctx",
    default=None,
)

_STAGE_LOGS_ENABLED = os.getenv("PERF_STAGE_LOGS", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def start_request(request_id: str) -> contextvars.Token:
    ctx = {
        "request_id": request_id,
        "started_at": time.perf_counter(),
        "stages": {},
        "fields": {},
    }
    return _ctx.set(ctx)


def finish_request(token: contextvars.Token) -> None:
    _ctx.reset(token)


def _ctx_or_none() -> dict[str, Any] | None:
    return _ctx.get()


def set_field(key: str, value: Any) -> None:
    ctx = _ctx_or_none()
    if ctx is None:
        return
    ctx["fields"][key] = value


def get_request_id() -> str:
    ctx = _ctx_or_none()
    if ctx is None:
        return ""
    return str(ctx.get("request_id", ""))


def add_stage(stage: str, ms: float) -> None:
    ctx = _ctx_or_none()
    if ctx is None:
        return
    stages: dict[str, float] = ctx["stages"]
    rounded = round(ms, 3)
    stages[stage] = round(stages.get(stage, 0.0) + rounded, 3)
    if _STAGE_LOGS_ENABLED:
        print(
            orjson.dumps(
                {
                    "event": "stage_timing",
                    "request_id": ctx["request_id"],
                    "stage": stage,
                    "ms": rounded,
                }
            ).decode()
        )


@contextmanager
def stage(name: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        add_stage(name, (time.perf_counter() - start) * 1000.0)


def snapshot() -> dict[str, Any]:
    ctx = _ctx_or_none()
    if ctx is None:
        return {
            "request_id": "",
            "total_ms": 0.0,
            "stages": {},
            "fields": {},
        }
    total_ms = round((time.perf_counter() - ctx["started_at"]) * 1000.0, 3)
    return {
        "request_id": ctx["request_id"],
        "total_ms": total_ms,
        "stages": dict(ctx["stages"]),
        "fields": dict(ctx["fields"]),
    }


def server_timing_header() -> str:
    snap = snapshot()
    parts = []
    for stage_name, dur in snap["stages"].items():
        token = (
            stage_name.replace(" ", "_")
            .replace(":", "_")
            .replace("/", "_")
            .replace(".", "_")
        )
        parts.append(f"{token};dur={float(dur):.1f}")
    return ", ".join(parts)
