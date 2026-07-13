from pathlib import Path

import pytest

from fastmcp import Client

from gamefunds.db import upsert_entities
from gamefunds.parser import parse_directory_markdown
from gamefunds.server import build_server


@pytest.mark.anyio
async def test_tools_work_through_mcp_layer(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))

    md = (Path(__file__).parent / "fixtures" / "directory_small_modified.md").read_text(encoding="utf-8")
    rows = parse_directory_markdown(md)
    upsert_entities(rows, db_path=db)

    server = build_server()
    client = Client(server)

    async with client:
        out = (await client.call_tool("search_funding", {"query": "Alpha", "limit": 15, "offset": 0})).data
        assert out["total_matched"] >= 1

        ent = (await client.call_tool("get_entity", {"slug": "alpha-pub"})).data
        assert ent["entity"]["name"] == "Alpha Pub"

        pipe = (await client.call_tool(
            "set_status",
            {"slug": "alpha-pub", "status": "contacted", "project": "X", "note": "hello"},
        )).data
        assert pipe["status"] == "contacted"

        lst = (await client.call_tool("list_pipeline", {})).data
        assert lst["by_status"]["contacted"] >= 1


@pytest.mark.anyio
async def test_resources_read(tmp_path, monkeypatch):
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(tmp_path / "t.db"))
    server = build_server()
    client = Client(server)
    async with client:
        contents = await client.read_resource("gamefunds://guide/definitions")
        assert contents
        assert hasattr(contents[0], "text")
        assert isinstance(contents[0].text, str)

