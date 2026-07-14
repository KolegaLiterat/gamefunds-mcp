from pathlib import Path

from gamefunds.core import gamefunds_help, match_project
from gamefunds.db import upsert_entities
from gamefunds.parser import parse_directory_markdown
from gamefunds.rubric_parser import FUNDING_TYPES

COZY_FULL_GENRE = "cozy pottery crafting sim, pixel art"
COZY_QUERY = dict(
    budget_usd=180_000,
    stage="vertical_slice",
    country="Poland",
    limit_per_type=5,
)


def _seed(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))
    md = (Path(__file__).parent / "fixtures" / "directory_2026-07.md").read_text(encoding="utf-8")
    rows = parse_directory_markdown(md)
    upsert_entities(rows, db_path=db)
    return db


def _publisher_slugs(out: dict) -> list[str]:
    return [c["slug"] for c in out["by_type"]["publisher"]]


def _assert_cozy_publisher_quality(out: dict) -> None:
    pubs = out["by_type"]["publisher"]
    assert pubs
    assert out["genre_signal"] == "good"
    assert "genre_warning" not in out

    slugs = _publisher_slugs(out)
    assert "whitethorn-games" in slugs[:3]
    whitethorn = next(c for c in pubs if c["slug"] == "whitethorn-games")
    assert whitethorn["breakdown"]["genre"] > 0
    assert whitethorn["confidence"] != "low"

    assert "rokaplay" in slugs
    assert "mooneye-studios" in slugs
    assert "feardemic" not in slugs
    assert "black-lantern-collective" not in slugs[:4]
    assert slugs[:3].count("silesia-games") == 0


def test_cozy_full_natural_description_regression(tmp_path, monkeypatch):
    """Regression: scoring must work on full game descriptions, not single keywords."""
    _seed(tmp_path, monkeypatch)

    out = match_project(genre=COZY_FULL_GENRE, **COZY_QUERY)
    _assert_cozy_publisher_quality(out)

    matched = {t.lower() for t in out["genre_terms"]["matched"]}
    assert "cozy" in matched
    assert out["genre_terms"]["unmatched"]  # pottery/crafting are fine to list


def test_cozy_single_keyword_not_better_than_full_description(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    full = match_project(genre=COZY_FULL_GENRE, **COZY_QUERY)
    minimal = match_project(genre="cozy", **COZY_QUERY)

    _assert_cozy_publisher_quality(minimal)

    full_whitethorn = next(c for c in full["by_type"]["publisher"] if c["slug"] == "whitethorn-games")
    min_whitethorn = next(c for c in minimal["by_type"]["publisher"] if c["slug"] == "whitethorn-games")
    assert full_whitethorn["breakdown"]["genre"] >= min_whitethorn["breakdown"]["genre"]


def test_horror_survival_prefers_horror_publishers(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    out = match_project(
        genre="brutal horror survival",
        budget_usd=180_000,
        stage="vertical_slice",
        country="Poland",
        limit_per_type=5,
    )

    slugs = _publisher_slugs(out)
    assert out["genre_signal"] == "good"
    assert "feardemic" in slugs
    assert "black-lantern-collective" in slugs
    assert slugs.index("feardemic") < len(slugs)
    assert "whitethorn-games" not in slugs[:2]


def test_sandbox_survival_filters_team17(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    out = match_project(
        genre="sandbox survival",
        budget_usd=500_000,
        stage="vertical_slice",
        country="UK",
        limit_per_type=10,
    )
    assert "team17" not in _publisher_slugs(out)
    assert out["hard_filtered"] > 0


def test_unknown_genre_signals_none_and_low_confidence(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    out = match_project(
        genre="adult game NSFW mature",
        budget_usd=400_000,
        stage="prototype",
        limit_per_type=5,
    )

    assert out["genre_signal"] == "none"
    assert out.get("genre_warning")
    assert "nie zawiera" in out["genre_warning"].lower()

    all_candidates = []
    for ft in FUNDING_TYPES:
        all_candidates.extend(out["by_type"][ft])
    assert all_candidates
    assert all(c["confidence"] == "low" for c in all_candidates)
    assert "hooded-horse" not in _publisher_slugs(out)


def test_adult_compound_genre_signal_none(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    out = match_project(
        genre="adult game, NSFW, mature content, visual novel",
        budget_usd=400_000,
        stage="prototype",
        limit_per_type=5,
    )

    assert out["genre_signal"] == "none"
    assert out.get("genre_warning")
    unmatched = {t.lower() for t in out["genre_terms"]["unmatched"]}
    assert "adult" in unmatched or any("adult" in t for t in unmatched)
    assert "nsfw" in unmatched or any("nsfw" in t for t in unmatched)
    assert not out["genre_terms"]["matched"]

    all_candidates = []
    for ft in FUNDING_TYPES:
        all_candidates.extend(out["by_type"][ft])
    assert all_candidates
    assert all(c["confidence"] == "low" for c in all_candidates)


def test_game_never_in_genre_reasons(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)

    out = match_project(
        genre="adult game, NSFW, mature content, visual novel",
        budget_usd=400_000,
        stage="prototype",
        limit_per_type=10,
    )

    for ft in FUNDING_TYPES:
        for cand in out["by_type"][ft]:
            for reason in cand.get("reasons", []):
                assert "genre keywords: game" not in reason.lower()
                assert "genre phrase: game" not in reason.lower()


def test_scoring_help_documents_weights(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch)
    text = gamefunds_help("scoring")
    assert "genre" in text.lower()
    assert "hard_filtered" in text.lower()
    assert "genre_signal" in text.lower()
    assert "genre_terms" in text.lower()
