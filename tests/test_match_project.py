from pathlib import Path

import pytest

from gamefunds.core import match_project
from gamefunds.db import upsert_entities
from gamefunds.parser import parse_directory_markdown
from gamefunds.rubric_parser import FUNDING_TYPES


def _seed(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))
    md = (Path(__file__).parent / "fixtures" / "directory_2026-07.md").read_text(encoding="utf-8")
    rows = parse_directory_markdown(md)
    upsert_entities(rows, db_path=db)
    return db


def _query_poland_cozy():
    return dict(
        genre="cozy roguelike",
        budget_usd=75_000,
        stage="vertical_slice",
        country="Poland",
    )


def _all_candidates(out: dict) -> list[dict]:
    items: list[dict] = []
    for ft in FUNDING_TYPES:
        items.extend(out["by_type"][ft])
    return items


def test_match_project_returns_grouped_by_funding_type(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    out = match_project(**_query_poland_cozy())

    assert set(out["by_type"]) == set(FUNDING_TYPES)
    assert "note" in out
    assert "hard_filtered" in out
    assert "below_cutoff" in out
    assert "excluded_count" not in out
    assert "Scores are comparable only within each group" in out["note"]

    for ft in FUNDING_TYPES:
        candidates = out["by_type"][ft]
        assert isinstance(candidates, list)
        if candidates:
            assert len(candidates) <= 3
            assert all(c["funding_type"] == ft for c in candidates)
        elif "group_notes" in out:
            assert ft in out["group_notes"]


def test_match_project_poland_cozy_paths_include_key_candidates(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    out = match_project(**_query_poland_cozy())

    publishers = out["by_type"]["publisher"]
    grants = out["by_type"]["grant"]
    assert publishers
    assert grants

    assert any(c["slug"] in {"anshar-publishing", "whitethorn-games"} for c in publishers)

    grant_slugs = {g["slug"] for g in grants}
    assert "gameinn-ncbr" in grant_slugs
    assert "parp-crpk" in grant_slugs

    gameinn = next(g for g in grants if g["slug"] == "gameinn-ncbr")
    assert gameinn["confidence"] == "low"
    assert gameinn["breakdown"]["budget"] is None
    assert gameinn["score"] >= 0
    assert "budget data unavailable" in " | ".join(gameinn["reasons"]).lower()

    assert out["by_type"]["vc_equity"]
    assert out["by_type"]["project_investor"]


def test_match_project_scores_never_negative(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    out = match_project(**_query_poland_cozy(), limit_per_type=10)
    for c in _all_candidates(out):
        assert c["score"] >= 0


def test_match_project_scores_comparable_only_within_group(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    out = match_project(
        genre="pixel-art roguelike",
        budget_usd=300_000,
        stage="vertical_slice",
        country="Poland",
        platform="PC",
    )

    publishers = out["by_type"]["publisher"]
    grants = out["by_type"]["grant"]
    assert publishers
    assert grants

    best_publisher = publishers[0]
    unknown_budget_grants = [g for g in grants if g["slug"] in {"gameinn-ncbr", "parp-crpk"}]
    assert unknown_budget_grants
    assert best_publisher["confidence"] == "high"
    assert best_publisher["score"] > max(g["score"] for g in unknown_budget_grants)

    assert all(c["slug"] != "paradox-interactive" for c in publishers)


def test_match_project_grant_with_known_amount_can_rank_high_within_group(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    out = match_project(
        genre="roguelike",
        budget_usd=75_000,
        stage="concept",
        limit_per_type=10,
    )
    grants = out["by_type"]["grant"]
    known = [
        g
        for g in grants
        if g.get("amount_max_usd") and (g["breakdown"].get("budget") or 0) >= 20
    ]
    assert known
    assert known[0]["confidence"] in {"high", "medium"}


def test_match_project_ignores_genre_stopwords(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    out = match_project(
        genre="with the and of a",
        budget_usd=75_000,
        stage="vertical_slice",
        country="Poland",
    )
    for c in _all_candidates(out):
        reasons = " | ".join(c.get("reasons") or []).lower()
        assert "genre keywords" not in reasons


def test_match_project_respects_limit_per_type_and_cap(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    out = match_project(**_query_poland_cozy(), limit_per_type=2)
    for ft in FUNDING_TYPES:
        assert len(out["by_type"][ft]) <= 2

    capped = match_project(**_query_poland_cozy(), limit_per_type=50)
    for ft in FUNDING_TYPES:
        assert len(capped["by_type"][ft]) <= 10


def test_match_project_rejects_non_positive_budget(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="positive"):
        match_project(genre="cozy", budget_usd=-1000, stage="prototype")

    with pytest.raises(ValueError, match="positive"):
        match_project(genre="cozy", budget_usd=0, stage="prototype")


def test_match_project_budget_signal_above_range(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    out = match_project(
        genre="cozy roguelike",
        budget_usd=500_000_000,
        stage="vertical_slice",
        country="Poland",
    )

    assert out["budget_signal"] == "above_range"
    assert out.get("budget_warning")
    assert "wykracza poza skalę" in out["budget_warning"].lower()
    assert all(c["confidence"] == "low" for c in _all_candidates(out))


def test_match_project_budget_signal_below_range(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    out = match_project(
        genre="cozy roguelike",
        budget_usd=1_000,
        stage="prototype",
    )

    assert out["budget_signal"] == "below_range"
    assert out.get("budget_warning")
    assert all(c["confidence"] == "low" for c in _all_candidates(out))


def test_match_project_budget_signal_in_range_for_typical_ask(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    out = match_project(
        genre="cozy pottery crafting sim, pixel art",
        budget_usd=180_000,
        stage="vertical_slice",
        country="Poland",
        limit_per_type=5,
    )

    assert out["budget_signal"] == "in_range"
    assert "budget_warning" not in out
    assert any(c["confidence"] != "low" for c in out["by_type"]["publisher"])
