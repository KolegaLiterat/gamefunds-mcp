from pathlib import Path

from gamefunds.core import match_project
from gamefunds.db import upsert_entities
from gamefunds.parser import parse_directory_markdown


def test_match_project_prefers_poland_grants_and_not_paradox(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))

    md = (Path(__file__).parent / "fixtures" / "directory_2026-07.md").read_text(encoding="utf-8")
    rows = parse_directory_markdown(md)
    upsert_entities(rows, db_path=db)

    out = match_project(
        genre="pixel-art roguelike",
        budget_usd=300_000,
        stage="vertical_slice",
        country="Poland",
        platform="PC",
    )
    top10 = out["candidates"][:10]
    assert any(c["section"] == "G" and (c["country"] == "Poland") for c in top10)
    assert all(c["slug"] != "paradox-interactive" for c in top10)

