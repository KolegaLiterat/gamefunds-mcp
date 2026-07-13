from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastmcp.server.middleware import Middleware
from fastmcp.tools.tool import ToolResult


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

        payload: Any = result
        if isinstance(result, ToolResult) and isinstance(result.structured_content, dict):
            payload = result.structured_content

        tokens = _approx_tokens(payload)
        if tokens <= self.max_tokens:
            return result

        if isinstance(payload, dict) and isinstance(payload.get("results"), list):
            full = payload["results"]
            total = len(full)
            shown = total

            # truncate progressively
            truncated = list(full)
            while truncated and _approx_tokens({**payload, "results": truncated}) > self.max_tokens:
                new_len = max(1, int(len(truncated) * 0.8))
                if new_len >= len(truncated):
                    break
                truncated = truncated[:new_len]
            shown = len(truncated)

            new_payload = {
                **payload,
                "results": truncated,
                "truncated": True,
                "shown": shown,
                "total": total,
                "hint": "zawęź filtry albo użyj offset",
            }
            return ToolResult(structured_content=new_payload)

        # fallback: attach a hint, don't attempt lossy truncation on unknown shapes
        if isinstance(payload, dict):
            return ToolResult(structured_content={**payload, "truncated": True, "hint": "wynik zbyt duży — zawęź zapytanie"})
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
