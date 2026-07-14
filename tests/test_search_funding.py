import random
import string
from pathlib import Path

import pytest
import sqlite3

from gamefunds.core import search_funding
from gamefunds.db import connect, upsert_entities
from gamefunds.fts_query import build_fts5_match_query, parse_search_terms
from gamefunds.parser import parse_directory_markdown

REALISTIC_SEARCH_QUERIES = [
    "turn-based",
    "free-to-play",
    "co-op",
    "point-and-click",
    "story-rich",
    "AAA/AA",
    "sim/strategy",
    "hack & slash",
    "indie (PC)",
    "NOT horror",
    "Paradox: Arc",
    "roguelike",
    '"cozy games"',
    "4X",
    "pixel*",
    "Devolver",
]


@pytest.fixture
def catalog_db(tmp_path, monkeypatch):
    db = tmp_path / "search.db"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))
    md = (Path(__file__).parent / "fixtures" / "directory_2026-07.md").read_text(encoding="utf-8")
    upsert_entities(parse_directory_markdown(md), db_path=db)
    return db


@pytest.mark.parametrize("query", REALISTIC_SEARCH_QUERIES)
def test_realistic_search_queries_never_raise(catalog_db, query):
    out = search_funding(query, limit=15)
    assert isinstance(out["total_matched"], int)
    assert out["total_matched"] >= 0
    assert isinstance(out["results"], list)


def test_turn_based_finds_hyphenated_notes(catalog_db):
    out = search_funding("turn-based", limit=20)
    assert out["total_matched"] >= 1
    assert any(r["slug"] == "ishtar-games" for r in out["results"])


def test_pixel_prefix_search_not_regressed(catalog_db):
    out = search_funding("pixel*", limit=50)
    assert out["total_matched"] >= 3


@pytest.mark.parametrize("query", ["---", "!!!", "   ", "..."])
def test_garbage_queries_return_empty(catalog_db, query):
    out = search_funding(query, limit=15)
    assert out == {"total_matched": 0, "results": []}


def test_fuzz_special_char_queries_never_raise(catalog_db):
    rng = random.Random(0)
    alphabet = string.ascii_letters + string.digits + "-/&():!*\"'@#$. "

    for _ in range(200):
        length = rng.randint(0, 40)
        query = "".join(rng.choice(alphabet) for _ in range(length))
        out = search_funding(query, limit=5)
        assert isinstance(out["total_matched"], int)
        assert isinstance(out["results"], list)


def test_compound_terms_normalize_to_spaced_phrases():
    assert parse_search_terms("turn-based") == ["turn based"]
    assert parse_search_terms("free-to-play") == ["free to play"]
    assert parse_search_terms("AAA/AA") == ["AAA AA"]
    assert parse_search_terms("hack & slash") == ["hack", "slash"]
    assert parse_search_terms("indie (PC)") == ["indie", "PC"]
    assert parse_search_terms("Paradox: Arc") == ["Paradox", "Arc"]
    assert parse_search_terms("pixel*") == ["pixel*"]
    assert parse_search_terms('"cozy games"') == ["cozy games"]


def test_build_fts_query_quotes_literals_and_preserves_prefix():
    assert build_fts5_match_query("turn-based") == '"turn based"'
    assert build_fts5_match_query("pixel*") == "pixel*"
    assert build_fts5_match_query("NOT horror") == '"NOT" OR "horror"'
    assert build_fts5_match_query("---") is None


def test_fts_query_escapes_embedded_quotes():
    assert build_fts5_match_query('cozy "pixel art" sim') == '"cozy" OR "pixel art" OR "sim"'


def test_search_raises_on_database_errors(tmp_path, monkeypatch):
    bad = tmp_path / "notadb"
    bad.mkdir()
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(bad))

    with pytest.raises(sqlite3.OperationalError):
        search_funding("roguelike", limit=5)


def test_search_uses_sanitized_match_expression(catalog_db):
    with connect(catalog_db) as conn:
        raw_count = conn.execute("SELECT COUNT(*) AS n FROM entities_fts;").fetchone()["n"]
    assert raw_count > 0

    out = search_funding("sim/strategy", limit=5)
    assert out["total_matched"] >= 1
    assert build_fts5_match_query("sim/strategy") == '"sim strategy"'
