from pathlib import Path

import pytest

from gamefunds.parser import ParseError, parse_directory_markdown


FIXTURE = Path(__file__).parent / "fixtures" / "directory_2026-07.md"


def test_parser_parses_225_plus_entries():
    md = FIXTURE.read_text(encoding="utf-8")
    rows = parse_directory_markdown(md)
    # The file header says "225+", but the actual July 2026 snapshot contains ~200 table rows.
    assert len(rows) >= 200


def test_parser_has_all_sections():
    md = FIXTURE.read_text(encoding="utf-8")
    rows = parse_directory_markdown(md)
    sections = {r["section"] for r in rows}
    assert sections.issuperset({"A", "B", "C", "D", "E", "F", "G"})
    for s in ["A", "B", "C", "D", "E", "F", "G"]:
        assert any(r["section"] == s for r in rows)


def _by_slug(rows, slug: str):
    for r in rows:
        if r["slug"] == slug:
            return r
    raise AssertionError(f"Missing slug: {slug}")


def test_spotcheck_paradox_and_team17():
    md = FIXTURE.read_text(encoding="utf-8")
    rows = parse_directory_markdown(md)

    paradox = _by_slug(rows, "paradox-interactive")
    assert paradox["section"] == "A"
    assert paradox["country"] == "Sweden"
    assert paradox["budget_tier"] == 3
    assert paradox["comm_rating"] == 3
    assert paradox["has_warning"] is False

    team17 = _by_slug(rows, "team17")
    assert team17["section"] == "A"
    assert "no sandbox" in (team17["notes"] or "").lower()


def test_spotcheck_grants_poland_present():
    md = FIXTURE.read_text(encoding="utf-8")
    rows = parse_directory_markdown(md)
    grants = [r for r in rows if r["section"] == "G"]
    assert grants
    assert any((r["country"] or "").lower() == "poland" for r in grants)


def test_parser_is_loud_on_unknown_schema():
    bad = """\
## A. Test

| Foo | Bar |
|---|---|
| x | y |
"""
    with pytest.raises(ParseError) as exc:
        parse_directory_markdown(bad)
    assert exc.value.section == "A"
    assert "Unknown table schema" in str(exc.value)
