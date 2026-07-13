from pathlib import Path

from gamefunds.db import init_db, upsert_entities
from gamefunds.parser import parse_directory_markdown


def test_init_db_creates_schema(tmp_path: Path):
    db = tmp_path / "t.db"
    init_db(db)
    assert db.exists()


def test_upsert_entities_populates_counts(tmp_path: Path):
    md_path = Path(__file__).parent / "fixtures" / "directory_2026-07.md"
    rows = parse_directory_markdown(md_path.read_text(encoding="utf-8"))
    db = tmp_path / "t.db"
    counts = upsert_entities(rows, db_path=db)
    assert sum(counts.values()) == len(rows)
    assert counts["A"] > 0
    assert counts["G"] > 0

