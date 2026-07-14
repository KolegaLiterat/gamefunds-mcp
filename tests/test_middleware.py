import json
from pathlib import Path

import pytest

from gamefunds.middleware import LoggingMiddleware, _redact_for_log


def test_redact_for_log_hides_long_strings():
    secret = "x" * 500
    redacted = _redact_for_log({"deck_markdown": secret, "target_slug": "team17"})
    assert redacted["deck_markdown"] == "<str:500 chars>"
    assert redacted["target_slug"] == "team17"


@pytest.mark.anyio
async def test_logging_middleware_redacts_args_when_enabled(tmp_path, monkeypatch):
    log_path = tmp_path / "tool_calls.log"
    monkeypatch.setenv("GAMEFUNDS_LOG_ARGS", "1")

    middleware = LoggingMiddleware(log_path=str(log_path))

    class _Msg:
        name = "review_pitch"
        arguments = {"deck_markdown": "SECRET " * 200, "target_slug": "team17"}

    class _Ctx:
        message = _Msg()

    async def _next(_ctx):
        return {"ok": True}

    await middleware.on_call_tool(_Ctx(), _next)

    line = log_path.read_text(encoding="utf-8").strip()
    payload = json.loads(line)
    assert "SECRET" not in line
    assert payload["args"]["deck_markdown"] == "<str:1400 chars>"
    assert payload["args"]["target_slug"] == "team17"


@pytest.mark.anyio
async def test_logging_middleware_omits_args_by_default(tmp_path, monkeypatch):
    log_path = tmp_path / "tool_calls.log"
    monkeypatch.delenv("GAMEFUNDS_LOG_ARGS", raising=False)

    middleware = LoggingMiddleware(log_path=str(log_path))

    class _Msg:
        name = "search_funding"
        arguments = {"query": "publisher"}

    class _Ctx:
        message = _Msg()

    async def _next(_ctx):
        return {"ok": True}

    await middleware.on_call_tool(_Ctx(), _next)

    payload = json.loads(log_path.read_text(encoding="utf-8").strip())
    assert "args" not in payload
