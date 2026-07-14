from __future__ import annotations

import hashlib
import inspect
import os

from fastmcp import FastMCP
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


def _token_rate_limit_key(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token:
            return hashlib.sha256(token.encode("utf-8")).hexdigest()
    return "unauthenticated"


def build_rate_limiter() -> Limiter:
    limit = os.getenv("GAMEFUNDS_RATE_LIMIT", "60/minute").strip() or "60/minute"
    limiter = Limiter(
        key_func=_token_rate_limit_key,
        default_limits=[limit],
        key_style="url",
        headers_enabled=True,
    )
    limiter._auto_check = False
    return limiter


class GameFundsRateLimitMiddleware(BaseHTTPMiddleware):
    """Apply slowapi default limits per bearer token without route decorators."""

    async def dispatch(self, request: Request, call_next) -> Response:
        limiter: Limiter = request.app.state.limiter
        try:
            limiter._check_request_limit(request, endpoint_func=None, in_middleware=True)
        except RateLimitExceeded as exc:
            handler = request.app.exception_handlers.get(
                RateLimitExceeded,
                _rate_limit_exceeded_handler,
            )
            if inspect.iscoroutinefunction(handler):
                return await handler(request, exc)
            return handler(request, exc)
        return await call_next(request)


def build_http_asgi_app(server: FastMCP):
    limiter = build_rate_limiter()
    app = server.http_app(
        middleware=[Middleware(GameFundsRateLimitMiddleware)],
        stateless_http=True,
    )
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    return app


def run_http_server(
    server: FastMCP,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> None:
    import uvicorn

    app = build_http_asgi_app(server)
    uvicorn.run(app, host=host, port=port, log_level="info")
