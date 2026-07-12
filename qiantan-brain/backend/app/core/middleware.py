"""Request ID middleware — injects a unique request_id into every request context
and every JSON response body (§5.6, §5.14).

The request_id is stored in a ContextVar so any downstream code (routers,
services, logging) can access it without threading it through every call.
"""

from __future__ import annotations

import json
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


# Context variable scoped to each request (no cross-request leakage).
_request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def get_request_id() -> str:
    """Return the current request's unique ID, or empty string if not set."""
    return _request_id_var.get()


def set_request_id(rid: str) -> None:
    """Set the current request's unique ID (used internally by middleware)."""
    _request_id_var.set(rid)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Inject X-Request-ID header; also injects request_id into JSON response body.

    Per §5.6: every API response should include request_id for tracing.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Accept client-provided request ID or generate one
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        _request_id_var.set(request_id)

        response = await call_next(request)

        # Echo back the request ID in header so clients can correlate
        response.headers["X-Request-ID"] = request_id

        # Inject request_id into JSON response body (§5.6)
        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type and hasattr(response, "body"):
            try:
                body = response.body
                if body:
                    data = json.loads(body)
                    if isinstance(data, dict) and "request_id" not in data:
                        data["request_id"] = request_id
                        response = Response(
                            content=json.dumps(data, ensure_ascii=False),
                            status_code=response.status_code,
                            headers=dict(response.headers),
                            media_type="application/json",
                        )
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass  # Non-JSON response body — leave as-is

        return response
