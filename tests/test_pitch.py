from pathlib import Path

import pytest

from gamefunds.core import get_pitch_rubric, get_submission_brief, review_pitch
from gamefunds.db import upsert_entities
from gamefunds.parser import parse_directory_markdown
from gamefunds.rubric_parser import parse_pitch_tutorial
from gamefunds.rubrics import load_rubrics, sync_rubrics_from_tutorial


def _seed(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))
    md = (Path(__file__).parent / "fixtures" / "directory_2026-07.md").read_text(encoding="utf-8")
    rows = parse_directory_markdown(md)
    upsert_entities(rows, db_path=db)
    return db


def _sample_deck() -> str:
    return """\
# Title / Hook
A cozy roguelite for fans of Moonlighter and Hades.

# The Game
Players cook for dungeon monsters instead of fighting them.

# Why It Will Sell (USP)
Unique cooking-for-monsters mechanic with strong art direction.

# Gameplay
Session loop: gather ingredients, cook, serve, upgrade kitchen.

# Look & Feel
Warm pixel art, cozy audio, readable UI on phone screens.

# Market & Comparables
Comps: Dungeon Munchies, Moonlighter, Cult of the Lamb with median-case revenue.

# Traction
8,000 wishlists in 2 months, 25% demo to wishlist conversion.

# Team
Lead programmer shipped 2 titles; producer with 4 shipped AA games.

# Production Plan
Vertical slice done; alpha in Q3 2026; release Q1 2027 with cut-list ready.

# Budget & The Ask
Total budget $420K, asking publisher for $250K for marketing and ports.

# Contact
hello@studio.example — https://example.com/demo
"""


def test_get_submission_brief_extracts_hard_filter(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    brief = get_submission_brief("team17")
    hard = " ".join(brief["hard_filters"]).lower()
    assert "sandbox" in hard
    assert len(brief["submit_links"]) == 2
    urls = {link["url"] for link in brief["submit_links"]}
    assert any("team17.com/submit-game" in url for url in urls)


def test_get_submission_brief_anshar_form_url(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    brief = get_submission_brief("anshar-publishing")
    assert brief["channel"] == "form"
    assert brief["target"] and "forms.office.com" in brief["target"]
    assert any("forms.office.com" in link["url"] for link in brief["submit_links"])


def test_get_submission_brief_form_channel_has_submit_links(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    md = (Path(__file__).parent / "fixtures" / "directory_2026-07.md").read_text(encoding="utf-8")
    rows = parse_directory_markdown(md)
    for row in rows:
        if not row.get("submit_links_json"):
            continue
        brief = get_submission_brief(row["slug"])
        if brief["channel"] != "form":
            continue
        assert brief["submit_links"], row["slug"]
        assert brief["target"]


def test_get_pitch_rubric_uses_tutorial_structure(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    rubric = get_pitch_rubric(funding_type="publisher")
    assert rubric["slide_range"] == [10, 20]
    assert len(rubric["slides"]) == 11
    assert rubric["slides"][0]["title"] == "Title / Hook"
    assert rubric["emphasize"]
    assert rubric["deemphasize"] == ["company vision", "exit strategy", "market TAM"]
    assert rubric["common_mistakes"]


def test_infer_funding_type_from_section(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    assert get_pitch_rubric(target_slug="team17")["funding_type"] == "publisher"
    assert get_pitch_rubric(target_slug="paradox-interactive")["funding_type"] == "publisher"


def test_review_pitch_recognizes_h1_title_hook(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    deck = """\
# Kiln
A cozy pottery sim where you glaze living vessels, for fans of Potion Craft and Dredge.

# The Game
Players run a village kiln with meaningful choices each day.

# Why It Will Sell (USP)
Only game combining pottery rhythm and village relationships.

# Gameplay
Daily loop: gather clay, shape, glaze, sell, upgrade tools.

# Look & Feel
Warm watercolor UI and tactile animation.

# Market & Comparables
Comps: Potion Craft, Spiritfarer, Unpacking — median case $800K, floor $200K, ceiling $2M.

# Traction
14,000 wishlists, 31% demo→wishlist, Next Fest top 50.

# Team
Lead shipped 2 titles; producer with 4 AA releases.

# Production Plan
Vertical slice complete; alpha Q3 2026; release Q1 2027.

# Budget & The Ask
Total budget $420K; asking $250K for marketing and ports.

# Contact
hello@studio.example — https://example.com/demo
"""
    out = review_pitch(deck, funding_type="publisher")
    assert out["coverage"]["Title / Hook"] == "present"
    short = [f for f in out["hard_findings"] if "very short" in f["issue"].lower()]
    assert not short


def test_review_pitch_does_not_flag_concise_traction_slide(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    out = review_pitch(_sample_deck(), funding_type="publisher")
    traction = [f for f in out["hard_findings"] if f.get("slide") == "Traction"]
    assert not traction
    assert out["coverage"]["Traction"] == "present"


def test_review_pitch_flags_missing_budget_and_sandbox_blocker(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    deck = """\
# Title / Hook
Cozy sandbox roguelike with pixel art.

# The Game
Core loop described here with enough words to avoid thin coverage on this slide for testing.

# Contact
https://example.com/demo
"""
    out = review_pitch(deck, target_slug="team17", funding_type="publisher")
    assert out["hard_findings"][0]["severity"] == "blocker"
    assert "no sandbox" in out["hard_findings"][0]["issue"].lower()
    issues = " | ".join(f["issue"] for f in out["hard_findings"]).lower()
    assert "budget" in issues


def test_review_pitch_flags_short_deck(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    deck = ""
    for i in range(1, 6):
        deck += f"# Slide {i}\nContent for slide {i} with enough words here.\n\n"
    deck += "# Contact\nhttps://example.com/demo\n"
    out = review_pitch(deck, funding_type="publisher")
    short = [f for f in out["hard_findings"] if "fewer than" in f["issue"].lower()]
    assert short
    assert short[0]["severity"] == "major"


def test_review_pitch_flags_deemphasized_exit_strategy_for_publisher(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    deck = _sample_deck() + "\n\n# Exit Strategy\nWe plan acquisition by a major publisher in 5 years.\n"
    out = review_pitch(deck, funding_type="publisher")
    deemph = [f for f in out["hard_findings"] if "de-emphasized" in f["issue"].lower()]
    assert deemph
    assert deemph[0]["severity"] == "minor"
    assert "exit strategy" in deemph[0]["issue"].lower()


def test_rubric_parser_on_fixture():
    md = (Path(__file__).parent / "fixtures" / "PitchDeckTutorial.md").read_text(encoding="utf-8")
    parsed = parse_pitch_tutorial(md)
    assert parsed["slide_range"] == [10, 20]
    assert len(parsed["slides"]) == 11
    assert set(parsed["tailoring"]) == {"publisher", "project_investor", "vc_equity", "grant"}
    assert parsed["slides"][2]["must_contain"]


def test_review_pitch_survives_bad_tutorial_sync(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    rubrics = tmp_path / "rubrics.json"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))
    monkeypatch.setattr("gamefunds.rubrics.RUBRICS_PATH", rubrics)
    monkeypatch.setattr("gamefunds.core.load_rubrics", lambda **kw: load_rubrics(db_path=db))

    good_md = (Path(__file__).parent / "fixtures" / "PitchDeckTutorial.md").read_text(encoding="utf-8")
    sync_rubrics_from_tutorial(good_md, db_path=db)
    good = rubrics.read_text(encoding="utf-8")

    ok, err = sync_rubrics_from_tutorial("## broken\nno tables here", db_path=db)
    assert ok is False
    assert err
    assert rubrics.read_text(encoding="utf-8") == good

    bundle = load_rubrics(db_path=db)
    assert bundle.stale is True

    out = review_pitch(_sample_deck(), funding_type="publisher", include_rubric=True)
    assert out["rubric_stale"] is True
    assert out["rubric_synced_at"]
    assert len(out["rubric"]["slides"]) == 11
