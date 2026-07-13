from pathlib import Path

import pytest

from gamefunds.core import add_note, list_pipeline, set_status
from gamefunds.db import upsert_entities
from gamefunds.parser import parse_directory_markdown


def _seed(tmp_path: Path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))
    md = (Path(__file__).parent / "fixtures" / "directory_small_base.md").read_text(encoding="utf-8")
    rows = parse_directory_markdown(md)
    upsert_entities(rows, db_path=db)
    return db


def test_set_status_validates_slug(tmp_path: Path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    with pytest.raises(KeyError):
        set_status("missing", "contacted")


def test_set_status_and_notes(tmp_path: Path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    s = set_status("alpha-pub", "contacted", project="TestGame", next_followup="2030-01-01", note="sent email")
    assert s["status"] == "contacted"
    assert s["project"] == "TestGame"

    n = add_note("alpha-pub", "followed up")
    assert n["note_count"] >= 2

    out = list_pipeline()
    assert out["by_status"]["contacted"] >= 1
    assert any(e["slug"] == "alpha-pub" for e in out["entries"])


def test_list_pipeline_stale_days_filters(tmp_path: Path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    set_status("alpha-pub", "contacted", next_followup="2000-01-01")
    stale = list_pipeline(stale_days=30)
    assert any(e["slug"] == "alpha-pub" for e in stale["entries"])

