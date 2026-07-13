import os
from pathlib import Path

from gamefunds.core import filter_funding, get_entity, search_funding
from gamefunds.db import upsert_entities
from gamefunds.parser import parse_directory_markdown


def test_search_and_filter_and_get_entity(tmp_path: Path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))

    md = (Path(__file__).parent / "fixtures" / "directory_small_modified.md").read_text(encoding="utf-8")
    rows = parse_directory_markdown(md)
    upsert_entities(rows, db_path=db)

    out = search_funding("Alpha", limit=15, offset=0)
    assert out["total_matched"] >= 1
    assert any(r["slug"] == "alpha-pub" for r in out["results"])

    f = filter_funding(country="UK", limit=15, offset=0)
    assert f["total_matched"] == 1
    assert f["results"][0]["slug"] == "beta-pub"

    e = get_entity("alpha-pub")
    assert e["entity"]["country"] == "Sweden"
    assert isinstance(e["entity"]["links"], list)

