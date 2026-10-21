import json
import time
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Request


MUTATING_METHODS = {"POST", "PUT", "DELETE"}
SENSITIVE_FIELDS = {"password", "refresh_token"}


def mask(value):
    if isinstance(value, dict):
        return {key: "***" if key in SENSITIVE_FIELDS else mask(item) for key, item in value.items()}
    if isinstance(value, list):
        return [mask(item) for item in value]
    return value


def parse_body(body: bytes):
    if not body:
        return None
    try:
        return mask(json.loads(body))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return "unavailable"


async def log_requests(request: Request, call_next):
    request_id = str(uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    started = time.perf_counter()
    body = await request.body()
    sent = False

    async def receive():
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    request._receive = receive
    response = await call_next(request)
    response.headers["X-Request-Id"] = request_id
    record = {
        "request_id": request_id,
        "method": request.method,
        "endpoint": request.url.path,
        "status_code": response.status_code,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "user_id": getattr(request.state, "user_id", None),
        "timestamp": timestamp,
    }
    if request.method in MUTATING_METHODS:
        record["request_body"] = parse_body(body)
    print(json.dumps(record, ensure_ascii=False), flush=True)
    return response
