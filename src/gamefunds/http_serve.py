from __future__ import annotations

import hashlib
import inspect
import os
import secrets

from fastmcp import FastMCP
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


def _extract_bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _is_readonly_token(token: str) -> bool:
    if not token:
        return False
    readonly = os.getenv("GAMEFUNDS_TOKEN_READONLY", "").strip()
    if not readonly:
        return False
    return secrets.compare_digest(token, readonly)


def _full_token_key(request: Request) -> str:
    token = _extract_bearer_token(request)
    if not token or _is_readonly_token(token):
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _readonly_token_key(request: Request) -> str:
    token = _extract_bearer_token(request)
    if not token or not _is_readonly_token(token):
        return ""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def build_rate_limiters() -> tuple[Limiter, Limiter]:
    full_limit = os.getenv("GAMEFUNDS_RATE_LIMIT", "60/minute").strip() or "60/minute"
    readonly_limit = (
        os.getenv("GAMEFUNDS_RATE_LIMIT_READONLY", "300/minute").strip() or "300/minute"
    )
    full = Limiter(
        key_func=_full_token_key,
        default_limits=[full_limit],
        key_style="url",
        headers_enabled=True,
    )
    readonly = Limiter(
        key_func=_readonly_token_key,
        default_limits=[readonly_limit],
        key_style="url",
        headers_enabled=True,
    )
    full._auto_check = False
    readonly._auto_check = False
    return full, readonly


class GameFundsRateLimitMiddleware(BaseHTTPMiddleware):
    """Apply per-token rate limits (owner vs shared read-only token)."""

    async def dispatch(self, request: Request, call_next) -> Response:
        token = _extract_bearer_token(request)
        if _is_readonly_token(token):
            limiter = request.app.state.limiter_readonly
        elif token:
            limiter = request.app.state.limiter_full
        else:
            return await call_next(request)

        try:
            limiter._check_request_limit(request, endpoint_func=None, in_middleware=True)
        except RateLimitExceeded as exc:
            request.app.state.limiter = limiter
            handler = request.app.exception_handlers.get(
                RateLimitExceeded,
                _rate_limit_exceeded_handler,
            )
            if inspect.iscoroutinefunction(handler):
                return await handler(request, exc)
            return handler(request, exc)
        return await call_next(request)


def build_http_asgi_app(server: FastMCP):
    limiter_full, limiter_readonly = build_rate_limiters()
    app = server.http_app(
        middleware=[Middleware(GameFundsRateLimitMiddleware)],
        stateless_http=True,
    )
    app.state.limiter_full = limiter_full
    app.state.limiter_readonly = limiter_readonly
    app.state.limiter = limiter_full
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
