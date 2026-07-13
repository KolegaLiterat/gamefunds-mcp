from __future__ import annotations


class TokenGuardMiddleware:
    """Token budget guard middleware (implemented later)."""

    def __init__(self, *, max_tokens: int = 2000):
        self.max_tokens = max_tokens


class LoggingMiddleware:
    """Tool call logger middleware (implemented later)."""

    def __init__(self, *, log_path: str = "data/tool_calls.log"):
        self.log_path = log_path

