import json
import os
from pathlib import Path

import pytest

from fastmcp import Client

from gamefunds.db import upsert_entities
from gamefunds.parser import parse_directory_markdown
from gamefunds.server import build_server

RUBRICS_PATH = Path(__file__).resolve().parents[1] / "data" / "rubrics.json"
EXPECTED_TAILORING = {"publisher", "project_investor", "vc_equity", "grant"}


def test_rubrics_json_exists_with_four_tailoring_types():
    assert RUBRICS_PATH.is_file(), "data/rubrics.json must be committed for offline fallback"
    data = json.loads(RUBRICS_PATH.read_text(encoding="utf-8"))
    tailoring = set((data.get("tailoring") or {}).keys())
    assert tailoring == EXPECTED_TAILORING


@pytest.mark.anyio
async def test_search_funding_does_not_leak_file_descriptors(tmp_path, monkeypatch):
    if not Path("/dev/fd").exists():
        pytest.skip("/dev/fd not available on this platform")

    db = tmp_path / "t.db"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))
    monkeypatch.setenv("GAMEFUNDS_LOG_ARGS", "0")

    md = (Path(__file__).parent / "fixtures" / "directory_2026-07.md").read_text(encoding="utf-8")
    upsert_entities(parse_directory_markdown(md), db_path=db)

    server = build_server()
    client = Client(server)

    fd_before = len(os.listdir("/dev/fd"))
    async with client:
        for _ in range(200):
            out = (await client.call_tool("search_funding", {"query": "publisher", "limit": 3})).data
            assert isinstance(out["total_matched"], int)
    fd_after = len(os.listdir("/dev/fd"))

    assert fd_after - fd_before < 10
