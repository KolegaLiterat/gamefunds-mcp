from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastmcp.server.middleware import Middleware


def _approx_tokens(obj: Any) -> int:
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except Exception:
        s = str(obj)
    # very rough heuristic: ~4 chars per token
    return max(1, len(s) // 4)


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

        # FastMCP tool result is typically JSON-serializable; we only truncate a common pattern
        tokens = _approx_tokens(result)
        if tokens <= self.max_tokens:
            return result

        if isinstance(result, dict) and isinstance(result.get("results"), list):
            full = result["results"]
            total = len(full)
            shown = total

            # truncate progressively
            truncated = list(full)
            while truncated and _approx_tokens({**result, "results": truncated}) > self.max_tokens:
                truncated = truncated[: max(1, int(len(truncated) * 0.8))]
            shown = len(truncated)

            return {
                **result,
                "results": truncated,
                "truncated": True,
                "shown": shown,
                "total": total,
                "hint": "zawęź filtry albo użyj offset",
            }

        # fallback: attach a hint, don't attempt lossy truncation on unknown shapes
        if isinstance(result, dict):
            return {**result, "truncated": True, "hint": "wynik zbyt duży — zawęź zapytanie"}
        return result


class LoggingMiddleware(Middleware):
    """Log tool calls to a local JSONL file for debugging."""

    def __init__(self, *, log_path: str = "data/tool_calls.log"):
        self.log_path = log_path

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

            payload = {
                "ts": time.time(),
                "tool": tool_name,
                "ok": ok,
                "error": err,
                "elapsed_ms": elapsed_ms,
                "args": args,
            }

            p = Path(self.log_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload, ensure_ascii=False) + "\n")
