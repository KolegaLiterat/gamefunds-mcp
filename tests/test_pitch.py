from pathlib import Path

import pytest

from gamefunds.core import get_submission_brief, review_pitch
from gamefunds.db import upsert_entities
from gamefunds.parser import parse_directory_markdown


def _seed(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))
    md = (Path(__file__).parent / "fixtures" / "directory_2026-07.md").read_text(encoding="utf-8")
    rows = parse_directory_markdown(md)
    upsert_entities(rows, db_path=db)
    return db


def test_get_submission_brief_extracts_hard_filter(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    brief = get_submission_brief("team17")
    hard = " ".join(brief["hard_filters"]).lower()
    assert "sandbox" in hard


def test_review_pitch_flags_missing_budget_and_sandbox_blocker(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    deck = """\
# Hook
Cozy sandbox roguelike with pixel art.

# Game
Core loop described here.

# Build
https://example.com/demo
"""
    out = review_pitch(deck, target_slug="team17", funding_type="publisher")
    issues = " | ".join(f["issue"] for f in out["hard_findings"]).lower()
    assert "missing concrete budget" in issues
    assert "no sandbox" in issues

