from pathlib import Path

import os
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
    guides = tmp_path / "guides"
    guides.mkdir()
    (guides / "Definitions.md").write_text("# synced definitions", encoding="utf-8")
    (guides / "DefinitionsPL.md").write_text("# synced definitions PL", encoding="utf-8")

    import gamefunds.guides as guides_mod

    monkeypatch.setattr(guides_mod, "guides_dir", lambda: guides)

    server = build_server()
    client = Client(server)
    async with client:
        en = await client.read_resource("gamefunds://guide/definitions")
        pl = await client.read_resource("gamefunds://guide/definitions/pl")
        assert en[0].text == "# synced definitions"
        assert pl[0].text == "# synced definitions PL"


@pytest.mark.anyio
async def test_tools_work_when_cwd_is_not_writable(tmp_path, monkeypatch):
    """Regression: MCP clients (claude mcp add) do not set cwd — paths must be absolute."""
    data_dir = tmp_path / "pkg-data"
    data_dir.mkdir()
    db = data_dir / "gamefunds.db"
    monkeypatch.setenv("GAMEFUNDS_DATA_DIR", str(data_dir))

    md = (Path(__file__).parent / "fixtures" / "directory_small_modified.md").read_text(encoding="utf-8")
    upsert_entities(parse_directory_markdown(md), db_path=db)

    readonly_cwd = tmp_path / "readonly"
    readonly_cwd.mkdir()
    readonly_cwd.chmod(0o555)

    old_cwd = os.getcwd()
    try:
        os.chdir(readonly_cwd)
        server = build_server()
        async with Client(server) as client:
            out = (await client.call_tool("search_funding", {"query": "Alpha", "limit": 15})).data
            assert out["total_matched"] >= 1
    finally:
        os.chdir(old_cwd)
        readonly_cwd.chmod(0o755)

    assert (data_dir / "tool_calls.log").exists()

