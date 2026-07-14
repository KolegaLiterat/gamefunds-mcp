import pytest

from fastmcp import Client

from gamefunds.core import gamefunds_help
from gamefunds.server import build_server

EXPECTED_TOOLS = {
    "search_funding",
    "filter_funding",
    "get_entity",
    "match_project",
    "get_submission_brief",
    "get_pitch_rubric",
    "review_pitch",
    "set_status",
    "add_note",
    "list_pipeline",
    "check_updates",
    "sync_directory",
    "gamefunds_help",
}


async def _tool_descriptions() -> dict[str, str]:
    server = build_server()
    async with Client(server) as client:
        tools = await client.list_tools()
    return {t.name: (t.description or "").strip() for t in tools}


@pytest.mark.anyio
async def test_all_tools_have_substantial_descriptions():
    descriptions = await _tool_descriptions()
    assert set(descriptions) == EXPECTED_TOOLS

    for name, description in descriptions.items():
        assert len(description) > 150, f"{name} description too short ({len(description)} chars)"


@pytest.mark.anyio
async def test_match_project_docstring_mentions_genre_signal_and_confidence():
    descriptions = await _tool_descriptions()
    text = descriptions["match_project"].lower()
    assert "genre_signal" in text
    assert "confidence" in text


@pytest.mark.anyio
async def test_review_pitch_docstring_mentions_include_rubric():
    descriptions = await _tool_descriptions()
    assert "include_rubric" in descriptions["review_pitch"]


@pytest.mark.anyio
async def test_gamefunds_help_docstring_points_to_guides():
    descriptions = await _tool_descriptions()
    text = descriptions["gamefunds_help"].lower()
    assert "guides" in text
    assert "funding-types" in text
    assert "do not answer from memory" in text


def test_gamefunds_help_funding_types_returns_guide_content():
    text = gamefunds_help("funding-types")
    assert "Project Investment" in text or "project investment" in text.lower()
    assert "equity" in text.lower()


def test_gamefunds_help_unknown_topic_returns_catalog_not_exception():
    text = gamefunds_help("funding-types-bzdura")
    assert "Available `gamefunds_help` topics" in text
    assert "funding-types" in text


def test_gamefunds_help_empty_topic_returns_catalog_not_exception():
    text = gamefunds_help("")
    assert "Available `gamefunds_help` topics" in text
    assert "guides" in text


def test_gamefunds_help_guides_returns_index():
    text = gamefunds_help("guides")
    assert "gamefunds://guide/" in text
    assert "project investment" in text.lower()
