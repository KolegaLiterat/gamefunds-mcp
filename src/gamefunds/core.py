from __future__ import annotations

from typing import Any


def search_funding(query: str, *, limit: int = 15, offset: int = 0) -> dict[str, Any]:
    raise NotImplementedError()


def filter_funding(
    *,
    section: str | None = None,
    country: str | None = None,
    budget_tier: int | None = None,
    min_comm: int | None = None,
    has_email: bool | None = None,
    exclude_warnings: bool = False,
    limit: int = 15,
    offset: int = 0,
) -> dict[str, Any]:
    raise NotImplementedError()


def get_entity(slug: str) -> dict[str, Any]:
    raise NotImplementedError()


def match_project(
    genre: str,
    budget_usd: int,
    stage: str,
    *,
    country: str | None = None,
    platform: str | None = None,
) -> dict[str, Any]:
    raise NotImplementedError()


def get_submission_brief(slug: str) -> dict[str, Any]:
    raise NotImplementedError()


def get_pitch_rubric(*, target_slug: str | None = None, funding_type: str | None = None) -> dict[str, Any]:
    raise NotImplementedError()


def review_pitch(
    deck_markdown: str,
    *,
    target_slug: str | None = None,
    funding_type: str | None = None,
) -> dict[str, Any]:
    raise NotImplementedError()


def set_status(
    slug: str,
    status: str,
    *,
    project: str | None = None,
    next_followup: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    raise NotImplementedError()


def add_note(slug: str, note: str) -> dict[str, Any]:
    raise NotImplementedError()


def list_pipeline(*, status: str | None = None, stale_days: int | None = None) -> dict[str, Any]:
    raise NotImplementedError()


def gamefunds_help(topic: str) -> str:
    raise NotImplementedError()

