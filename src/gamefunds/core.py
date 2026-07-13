from __future__ import annotations

import json
import os
import sqlite3
from datetime import date, datetime, timezone
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
    init_db(_db_path())
    allowed = {"not_contacted", "contacted", "in_talks", "rejected", "signed", "passed"}
    if status not in allowed:
        raise ValueError(f"Invalid status: {status}")

    now = datetime.now(timezone.utc).isoformat()
    today = date.today().isoformat()

    with connect(_db_path()) as conn:
        exists = conn.execute("SELECT 1 FROM entities WHERE slug=?;", (slug,)).fetchone()
        if not exists:
            raise KeyError(f"Unknown slug: {slug}")

        current = conn.execute("SELECT * FROM pipeline WHERE slug=?;", (slug,)).fetchone()
        existing_notes = (current["notes"] if current else None) or ""

        notes_out = existing_notes
        if note:
            prefix = f"[{today}] "
            entry = prefix + note.strip()
            notes_out = (notes_out + ("\n" if notes_out else "") + entry).strip()

        last_contact_date = (current["last_contact_date"] if current else None) if current else None
        if status in {"contacted", "in_talks"}:
            last_contact_date = today

        conn.execute(
            """
            INSERT INTO pipeline(slug, status, project, last_contact_date, next_followup, notes, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
              status=excluded.status,
              project=COALESCE(excluded.project, pipeline.project),
              last_contact_date=COALESCE(excluded.last_contact_date, pipeline.last_contact_date),
              next_followup=COALESCE(excluded.next_followup, pipeline.next_followup),
              notes=excluded.notes,
              updated_at=excluded.updated_at;
            """,
            (slug, status, project, last_contact_date, next_followup, notes_out or None, now),
        )
        conn.commit()

        out = conn.execute("SELECT * FROM pipeline WHERE slug=?;", (slug,)).fetchone()
    return dict(out)


def add_note(slug: str, note: str) -> dict[str, Any]:
    init_db(_db_path())
    today = date.today().isoformat()
    now = datetime.now(timezone.utc).isoformat()
    note = note.strip()
    if not note:
        raise ValueError("note must be non-empty")

    with connect(_db_path()) as conn:
        exists = conn.execute("SELECT 1 FROM entities WHERE slug=?;", (slug,)).fetchone()
        if not exists:
            raise KeyError(f"Unknown slug: {slug}")
        cur = conn.execute("SELECT notes FROM pipeline WHERE slug=?;", (slug,)).fetchone()
        existing_notes = (cur["notes"] if cur else None) or ""
        entry = f"[{today}] {note}"
        notes_out = (existing_notes + ("\n" if existing_notes else "") + entry).strip()

        conn.execute(
            """
            INSERT INTO pipeline(slug, notes, updated_at)
            VALUES(?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
              notes=excluded.notes,
              updated_at=excluded.updated_at;
            """,
            (slug, notes_out, now),
        )
        conn.commit()

        count = notes_out.count("\n") + (1 if notes_out else 0)
    return {"slug": slug, "note_count": count}


def list_pipeline(*, status: str | None = None, stale_days: int | None = None) -> dict[str, Any]:
    init_db(_db_path())

    where = []
    params: list[Any] = []
    if status:
        where.append("status = ?")
        params.append(status)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    with connect(_db_path()) as conn:
        by_status_rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM pipeline GROUP BY status ORDER BY status;"
        ).fetchall()
        by_status = {r["status"]: int(r["n"]) for r in by_status_rows}

        rows = conn.execute(
            f"""
            SELECT p.*, e.name
            FROM pipeline p
            LEFT JOIN entities e ON e.slug = p.slug
            {where_sql}
            ORDER BY p.updated_at DESC;
            """,
            params,
        ).fetchall()

    def parse_dt(s: str | None) -> datetime | None:
        if not s:
            return None
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except Exception:
            return None

    def parse_d(s: str | None) -> date | None:
        if not s:
            return None
        try:
            return date.fromisoformat(s[:10])
        except Exception:
            return None

    today = date.today()

    entries = []
    for r in rows:
        last_contact = parse_d(r["last_contact_date"])
        next_follow = parse_d(r["next_followup"])

        days_stale = None
        if last_contact:
            days_stale = (today - last_contact).days

        last_note = None
        if r["notes"]:
            last_note = str(r["notes"]).splitlines()[-1]

        entry = {
            "slug": r["slug"],
            "name": r["name"],
            "status": r["status"],
            "last_contact": r["last_contact_date"],
            "next_followup": r["next_followup"],
            "days_stale": days_stale,
            "last_note": last_note,
        }

        if stale_days is not None:
            if r["status"] not in {"contacted", "in_talks"}:
                continue
            overdue = next_follow is not None and next_follow < today
            too_old = days_stale is not None and days_stale > stale_days
            if not (overdue or too_old):
                continue

        entries.append(entry)

    return {"by_status": by_status, "entries": entries}


def gamefunds_help(topic: str) -> str:
    raise NotImplementedError()

