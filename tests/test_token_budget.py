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
    monkeypatch.setenv("GAMEFUNDS_MAX_TOOL_TOKENS", "400")

    md = (Path(__file__).parent / "fixtures" / "directory_2026-07.md").read_text(encoding="utf-8")
    rows = parse_directory_markdown(md)
    upsert_entities(rows, db_path=db)

    server = build_server()
    client = Client(server)

    async with client:
        out = (await client.call_tool("filter_funding", {"limit": 50, "offset": 0})).data
        assert out.get("truncated") is True
        assert out.get("shown") <= 50
        assert out.get("oversized") is not True


@pytest.mark.anyio
async def test_token_guard_returns_truncated_payload_when_still_oversized(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))
    monkeypatch.setenv("GAMEFUNDS_MAX_TOOL_TOKENS", "20")

    md = (Path(__file__).parent / "fixtures" / "directory_2026-07.md").read_text(encoding="utf-8")
    rows = parse_directory_markdown(md)
    upsert_entities(rows, db_path=db)

    server = build_server()
    client = Client(server)

    async with client:
        out = (await client.call_tool("filter_funding", {"limit": 50, "offset": 0})).data
        assert out.get("truncated") is True
        assert out.get("shown", 0) < out.get("total", 0)
        assert out.get("oversized") is True
        assert approx_tokens(out) >= 20


@pytest.mark.anyio
async def test_match_project_default_fits_token_budget(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))

    md = (Path(__file__).parent / "fixtures" / "directory_2026-07.md").read_text(encoding="utf-8")
    rows = parse_directory_markdown(md)
    upsert_entities(rows, db_path=db)

    server = build_server()
    client = Client(server)

    async with client:
        out = (
            await client.call_tool(
                "match_project",
                {
                    "genre": "cozy roguelike",
                    "budget_usd": 75000,
                    "stage": "vertical_slice",
                    "country": "Poland",
                },
            )
        ).data
        assert approx_tokens(out) < 2000
        assert out.get("oversized") is not True
        assert out.get("truncated") is not True


@pytest.mark.anyio
async def test_review_pitch_default_fits_token_budget(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))

    md = (Path(__file__).parent / "fixtures" / "directory_2026-07.md").read_text(encoding="utf-8")
    rows = parse_directory_markdown(md)
    upsert_entities(rows, db_path=db)

    deck = "# Title / Hook\nTest deck.\n\n# Contact\nhttps://example.com/demo\n"

    server = build_server()
    client = Client(server)

    async with client:
        out = (
            await client.call_tool(
                "review_pitch",
                {"deck_markdown": deck, "target_slug": "team17"},
            )
        ).data
        assert "rubric" not in out
        assert approx_tokens(out) < 2000
        assert out.get("truncated") is not True
        assert out.get("oversized") is not True

