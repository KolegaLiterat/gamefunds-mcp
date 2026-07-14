from pathlib import Path

import pytest

import json
import re

from gamefunds.core import get_entity, get_submission_brief
from gamefunds.parser import ParseError, parse_directory_markdown, text_has_money_signal

FIXTURE = Path(__file__).parent / "fixtures" / "directory_2026-07.md"


def _rows():
    return parse_directory_markdown(FIXTURE.read_text(encoding="utf-8"))


def _by_slug(slug: str):
    for r in _rows():
        if r["slug"] == slug:
            return r
    raise AssertionError(slug)


def test_every_entity_has_terms_raw():
    rows = _rows()
    missing = [r["slug"] for r in rows if not r.get("terms_raw")]
    assert not missing, f"missing terms_raw: {missing[:5]}"
    assert len(rows) == 207


def test_terms_raw_db_coverage(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))
    from gamefunds.db import connect, init_db, upsert_entities

    rows = _rows()
    upsert_entities(rows, db_path=db)
    init_db(db)
    with connect(db) as conn:
        total = conn.execute("SELECT COUNT(*) FROM entities;").fetchone()[0]
        with_terms = conn.execute(
            "SELECT COUNT(*) FROM entities WHERE terms_raw IS NOT NULL;"
        ).fetchone()[0]
    assert total == with_terms == len(rows)


def test_transcend_fund_terms_raw_preserves_stage():
    row = _by_slug("transcend-fund")
    assert row["section"] == "E"
    assert "Pre-seed–Series A" in row["terms_raw"]
    assert row.get("amount_raw") is None


def test_gameinn_terms_raw_without_amount_raw():
    row = _by_slug("gameinn-ncbr")
    assert row["section"] == "G"
    assert "varies" in row["terms_raw"].lower()
    assert row.get("amount_raw") is None


def test_parser_stores_submit_links_json():
    rows = _rows()
    anshar = _by_slug("anshar-publishing")
    assert anshar.get("submit_links_json")
    team17 = _by_slug("team17")
    links = json.loads(team17["submit_links_json"])
    assert len(links) == 2


def test_get_entity_and_brief_expose_terms_raw(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))
    from gamefunds.db import upsert_entities

    upsert_entities(_rows(), db_path=db)
    entity = get_entity("transcend-fund")["entity"]
    assert entity.get("terms_raw")
    assert entity.get("submit_links_json") is not None or entity.get("contact")
    brief = get_submission_brief("transcend-fund")
    assert brief.get("terms_raw")


def test_each_section_has_budget_or_amount_raw():
    rows = _rows()
    for sec in ["A", "B", "C", "D", "E", "F", "G"]:
        sec_rows = [r for r in rows if r["section"] == sec]
        assert sec_rows, f"section {sec} empty"
        covered = []
        for r in sec_rows:
            if r.get("budget_tier") is not None or r.get("amount_raw"):
                covered.append(r)
            elif sec == "C" and r.get("backing"):
                covered.append(r)
            elif sec == "D" and (r.get("funding_terms") or r.get("target_scope")):
                covered.append(r)
        assert covered, f"section {sec} has no budget/amount/backing metadata"


def test_sections_c_and_d_amount_raw_only_when_monetary():
    rows = _rows()
    for r in rows:
        if r["section"] not in {"C", "D"}:
            continue
        raw = r.get("amount_raw")
        if raw is None:
            continue
        assert text_has_money_signal(raw), f"{r['slug']} amount_raw without money signal: {raw!r}"


def test_section_c_stores_backing_not_amount():
    row = _by_slug("bigmode")
    assert row["section"] == "C"
    assert row.get("backing")
    assert row.get("amount_raw") is None


def test_section_d_stores_funding_terms_not_amount():
    row = _by_slug("ea-originals")
    assert row["section"] == "D"
    assert row.get("funding_terms")
    assert row.get("target_scope")
    assert row.get("amount_raw") is None


def test_get_submission_brief_includes_section_metadata(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))
    from gamefunds.db import upsert_entities

    upsert_entities(_rows(), db_path=db)
    brief = get_submission_brief("bigmode")
    assert brief.get("backing")
    brief_d = get_submission_brief("ea-originals")
    assert brief_d.get("funding_terms")
    assert brief_d.get("target_scope")


def test_section_g_submission_channels_not_unknown(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))
    from gamefunds.db import upsert_entities

    upsert_entities(_rows(), db_path=db)
    grants = [r for r in _rows() if r["section"] == "G"]
    for g in grants:
        brief = get_submission_brief(g["slug"])
        assert brief["channel"] != "unknown", g["slug"]


def test_uk_games_fund_amount_and_eligibility():
    row = _by_slug("uk-games-fund")
    assert row["eligibility"] and "uk-registered" in row["eligibility"].lower()
    assert row["pitch"] is None or "uk-registered" not in (row["pitch"] or "").lower()
    assert row["amount_max_usd"] is not None
    assert 250_000 <= row["amount_max_usd"] <= 400_000


def test_no_grant_has_eligibility_in_pitch():
    for r in _rows():
        if r["section"] != "G":
            continue
        pitch = (r.get("pitch") or "").lower()
        elig = (r.get("eligibility") or "").lower()
        if elig:
            assert elig not in pitch


def test_unknown_header_raises_with_section():
    bad = """\
## C. Test

| Foo | Bar |
|---|---|
| x | y |
"""
    with pytest.raises(ParseError) as exc:
        parse_directory_markdown(bad)
    assert exc.value.section == "C"
    assert "Unknown table schema" in str(exc.value)
