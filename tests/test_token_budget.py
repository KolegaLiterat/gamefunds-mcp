from __future__ import annotations

import json
from pathlib import Path

import pytest

from fastmcp import Client

from gamefunds.db import upsert_entities
from gamefunds.parser import parse_directory_markdown
from gamefunds.server import build_server


def approx_tokens(obj) -> int:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return max(1, len(s) // 4)


@pytest.mark.anyio
async def test_default_calls_under_token_budget(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))

    md = (Path(__file__).parent / "fixtures" / "directory_2026-07.md").read_text(encoding="utf-8")
    rows = parse_directory_markdown(md)
    upsert_entities(rows, db_path=db)

    server = build_server()
    client = Client(server)

    async with client:
        out = (await client.call_tool("search_funding", {"query": "publisher"})).data
        assert approx_tokens(out) < 2000

        # Brief objects should remain compact
        if out["results"]:
            assert approx_tokens(out["results"][0]) < 50 * 4  # generous bound


@pytest.mark.anyio
async def test_token_guard_truncates_when_limit_low(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))
    monkeypatch.setenv("GAMEFUNDS_MAX_TOOL_TOKENS", "50")  # force truncation

    md = (Path(__file__).parent / "fixtures" / "directory_2026-07.md").read_text(encoding="utf-8")
    rows = parse_directory_markdown(md)
    upsert_entities(rows, db_path=db)

    server = build_server()
    client = Client(server)

    async with client:
        out = (await client.call_tool("filter_funding", {"limit": 50, "offset": 0})).data
        assert out.get("truncated") is True
        assert out.get("shown") <= 50

