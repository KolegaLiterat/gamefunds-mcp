from __future__ import annotations

import json
import os
import sqlite3
from typing import Any

from .db import DEFAULT_DB_PATH, connect, init_db


def _db_path():
    return DEFAULT_DB_PATH.__class__(os.getenv("GAMEFUNDS_DB_PATH", str(DEFAULT_DB_PATH)))


def _entity_brief(row: sqlite3.Row) -> dict[str, Any]:
    notes = row["notes"] or ""
    headline = notes[:80]
    if len(notes) > 80:
        headline = headline.rstrip() + "…"
    return {
        "slug": row["slug"],
        "name": row["name"],
        "country": row["country"],
        "section": row["section"],
        "budget_tier": row["budget_tier"],
        "comm_rating": row["comm_rating"],
        "has_warning": bool(row["has_warning"]),
        "headline": headline or None,
    }


def _clamp_limit(limit: int) -> int:
    if limit <= 0:
        return 1
    return min(limit, 50)


def search_funding(query: str, *, limit: int = 15, offset: int = 0) -> dict[str, Any]:
    init_db(_db_path())
    limit = _clamp_limit(limit)
    offset = max(offset, 0)

    with connect(_db_path()) as conn:
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM entities_fts WHERE entities_fts MATCH ?;",
            (query,),
        ).fetchone()["n"]
        rows = conn.execute(
            """
            SELECT e.*
            FROM entities_fts
            JOIN entities e ON e.rowid = entities_fts.rowid
            WHERE entities_fts MATCH ?
            ORDER BY bm25(entities_fts)
            LIMIT ? OFFSET ?;
            """,
            (query, limit, offset),
        ).fetchall()

    return {"total_matched": int(total), "results": [_entity_brief(r) for r in rows]}


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
    init_db(_db_path())
    limit = _clamp_limit(limit)
    offset = max(offset, 0)

    where = []
    params: list[Any] = []

    filters_applied: dict[str, Any] = {}

    if section:
        where.append("section = ?")
        params.append(section)
        filters_applied["section"] = section
    if country:
        where.append("country = ?")
        params.append(country)
        filters_applied["country"] = country
    if budget_tier is not None:
        where.append("budget_tier = ?")
        params.append(int(budget_tier))
        filters_applied["budget_tier"] = int(budget_tier)
    if min_comm is not None:
        where.append("comm_rating >= ?")
        params.append(int(min_comm))
        filters_applied["min_comm"] = int(min_comm)
    if has_email is not None:
        if has_email:
            where.append("contact_email IS NOT NULL AND contact_email != ''")
        else:
            where.append("(contact_email IS NULL OR contact_email = '')")
        filters_applied["has_email"] = bool(has_email)
    if exclude_warnings:
        where.append("(has_warning IS NULL OR has_warning = 0)")
        filters_applied["exclude_warnings"] = True

    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    with connect(_db_path()) as conn:
        total = conn.execute(f"SELECT COUNT(*) AS n FROM entities {where_sql};", params).fetchone()["n"]
        rows = conn.execute(
            f"SELECT * FROM entities {where_sql} ORDER BY name ASC LIMIT ? OFFSET ?;",
            [*params, limit, offset],
        ).fetchall()

    return {
        "total_matched": int(total),
        "results": [_entity_brief(r) for r in rows],
        "filters_applied": filters_applied,
    }


def get_entity(slug: str) -> dict[str, Any]:
    init_db(_db_path())
    with connect(_db_path()) as conn:
        r = conn.execute("SELECT * FROM entities WHERE slug = ?;", (slug,)).fetchone()
        if not r:
            raise KeyError(f"Unknown slug: {slug}")
        pipe = conn.execute("SELECT * FROM pipeline WHERE slug = ?;", (slug,)).fetchone()

    entity = dict(r)
    if entity.get("links_json"):
        entity["links"] = json.loads(entity["links_json"])
    else:
        entity["links"] = []
    entity.pop("links_json", None)

    pipeline = dict(pipe) if pipe else None
    return {"entity": entity, "pipeline": pipeline}


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

