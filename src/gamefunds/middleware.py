from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from fastmcp.exceptions import AuthorizationError
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware
from fastmcp.tools.tool import ToolResult

from .paths import tool_calls_log_path

_PIPELINE_TOOLS = frozenset({"set_status", "add_note", "list_pipeline"})
_ADMIN_TOOLS = frozenset({"sync_directory"})
_WRITE_REQUIRED_TOOLS = _PIPELINE_TOOLS | _ADMIN_TOOLS

SHARED_READONLY_DENIAL = (
    "This is a shared read-only endpoint — the funding catalog is public, but "
    "pipeline tracking (set_status, add_note, list_pipeline) is private to the "
    "server owner.\n\n"
    "To track your own outreach, run GameFunds MCP yourself — it is free, open "
    "source, and needs no token when run locally:\n"
    "https://github.com/KolegaLiterat/gamefunds-mcp"
)

_MAX_LOG_BYTES = 5 * 1024 * 1024
_REDACT_STRING_LEN = 100


def _approx_tokens(obj: Any) -> int:
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        s = str(obj)
    return max(1, len(s) // 4)


def _known_auth_tokens() -> frozenset[str]:
    tokens: set[str] = set()
    for name in ("GAMEFUNDS_TOKEN", "GAMEFUNDS_TOKEN_READONLY"):
        raw = os.getenv(name, "").strip()
        if raw:
            tokens.add(raw)
    return frozenset(tokens)


def _redact_for_log(value: Any) -> Any:
    if isinstance(value, str):
        if value in _known_auth_tokens():
            return f"<str:{len(value)} chars>"
        if len(value) <= _REDACT_STRING_LEN:
            return value
        return f"<str:{len(value)} chars>"
    if isinstance(value, dict):
        return {k: _redact_for_log(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_for_log(v) for v in value]
    return value


def _rotate_log_if_needed(path: Path) -> None:
    if not path.exists() or path.stat().st_size < _MAX_LOG_BYTES:
        return
    backup = path.with_name(path.name + ".1")
    if backup.exists():
        backup.unlink()
    path.rename(backup)


def _truncate_results_payload(payload: dict[str, Any], max_tokens: int) -> dict[str, Any] | None:
    full = payload.get("results")
    if not isinstance(full, list) or not full:
        return None

    lo, hi = 0, len(full)
    best_len = 0
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = {**payload, "results": full[:mid]}
        if _approx_tokens(candidate) <= max_tokens:
            best_len = mid
            lo = mid + 1
        else:
            hi = mid - 1

    if best_len == 0:
        trimmed = {**payload, "results": []}
    else:
        trimmed = {**payload, "results": full[:best_len]}

    trimmed["shown"] = len(trimmed["results"])
    trimmed["total"] = len(full)
    trimmed["truncated"] = True
    trimmed["hint"] = "narrow filters or use offset"
    if _approx_tokens(trimmed) > max_tokens:
        trimmed["oversized"] = True
        trimmed["tokens"] = _approx_tokens(trimmed)
        trimmed["limit"] = max_tokens
    return trimmed


def _has_write_scope() -> bool:
    access = get_access_token()
    if access is None:
        return True
    return "write" in set(access.scopes)


def _strip_pipeline_from_result(result: Any) -> Any:
    if isinstance(result, ToolResult) and isinstance(result.structured_content, dict):
        payload = dict(result.structured_content)
        payload.pop("pipeline", None)
        return ToolResult(structured_content=payload)
    if isinstance(result, dict):
        payload = dict(result)
        payload.pop("pipeline", None)
        return payload
    return result


class TokenGuardMiddleware(Middleware):
    """
    Token budget guard middleware.

    If a tool returns a large payload (estimated tokens > max_tokens), truncate
    list-like `results` to fit and attach truncation metadata.
    """

    def __init__(self, *, max_tokens: int = 2000):
        self.max_tokens = max_tokens

    async def on_call_tool(self, context, call_next):
        result = await call_next(context)

        payload: Any = result
        if isinstance(result, ToolResult) and isinstance(result.structured_content, dict):
            payload = result.structured_content

        tokens = _approx_tokens(payload)
        if tokens <= self.max_tokens:
            return result

        if isinstance(payload, dict) and isinstance(payload.get("results"), list):
            trimmed = _truncate_results_payload(payload, self.max_tokens)
            if trimmed is not None:
                return ToolResult(structured_content=trimmed)

        if isinstance(payload, dict):
            return ToolResult(
                structured_content={
                    **payload,
                    "oversized": True,
                    "tokens": tokens,
                    "limit": self.max_tokens,
                    "hint": (
                        "Result exceeds the token limit and could not be trimmed. "
                        "Narrow the query or use a lighter tool "
                        "(e.g. get_entity instead of filter_funding, get_pitch_rubric instead of "
                        "full rubric inside review_pitch)."
                    ),
                }
            )
        return result


class ScopeMiddleware(Middleware):
    """Enforce read/write scopes for HTTP-authenticated requests."""

    def __init__(self, *, enforce: bool = False) -> None:
        self.enforce = enforce

    async def on_call_tool(self, context, call_next):
        if not self.enforce:
            return await call_next(context)

        msg = getattr(context, "message", None)
        tool_name = getattr(msg, "name", None)

        if tool_name in _WRITE_REQUIRED_TOOLS and not _has_write_scope():
            raise AuthorizationError(SHARED_READONLY_DENIAL)

        result = await call_next(context)

        if tool_name == "get_entity" and not _has_write_scope():
            return _strip_pipeline_from_result(result)
        return result


class LoggingMiddleware(Middleware):
    """Log tool calls to a local JSONL file for debugging."""

    def __init__(self, *, log_path: str | Path | None = None):
        self.log_path = str(log_path if log_path is not None else tool_calls_log_path())
        self.log_args = os.getenv("GAMEFUNDS_LOG_ARGS", "0").strip().lower() in {"1", "true", "yes"}

    async def on_call_tool(self, context, call_next):
        start = time.perf_counter()
        ok = True
        err: str | None = None
        try:
            result = await call_next(context)
            return result
        except Exception as e:  # pragma: no cover
            ok = False
            err = repr(e)
            raise
        finally:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            msg = getattr(context, "message", None)
            tool_name = getattr(msg, "name", None)
            args = getattr(msg, "arguments", None)

            payload: dict[str, Any] = {
                "ts": time.time(),
                "tool": tool_name,
                "ok": ok,
                "error": err,
                "elapsed_ms": elapsed_ms,
            }
            if self.log_args and args is not None:
                payload["args"] = _redact_for_log(args)

            p = Path(self.log_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            _rotate_log_if_needed(p)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
