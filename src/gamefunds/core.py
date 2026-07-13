from __future__ import annotations

import json
import os
import re
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
    init_db(_db_path())

    def project_budget_tier(b: int) -> int:
        if b < 200_000:
            return 1
        if b <= 2_000_000:
            return 2
        return 3

    proj_tier = project_budget_tier(int(budget_usd))
    stage = stage.strip().lower()
    genre_terms = [t.lower() for t in genre.replace("-", " ").split() if len(t) >= 3]
    country_norm = country.strip() if country else None

    prefer_sections: set[str] = set()
    if stage in {"concept", "prototype"}:
        prefer_sections = {"E", "F", "G"}
    elif stage in {"vertical_slice", "alpha", "beta"}:
        prefer_sections = {"A", "B", "C"}

    with connect(_db_path()) as conn:
        rows = conn.execute("SELECT * FROM entities;").fetchall()

    excluded = 0
    scored: list[dict[str, Any]] = []

    for r in rows:
        sec = r["section"]
        ent_country = r["country"]
        ent_tier = r["budget_tier"]
        ent_class = r["class_tier"] or ""

        # hard filter: budget tiers too far away
        if ent_tier is not None and abs(int(ent_tier) - proj_tier) >= 2:
            excluded += 1
            continue

        # hard filter: grants out of country (usually pointless)
        if sec == "G" and country_norm and ent_country and ent_country != country_norm:
            excluded += 1
            continue

        score = 0
        reasons: list[str] = []

        if ent_tier is not None:
            d = abs(int(ent_tier) - proj_tier)
            if d == 0:
                score += 30
                reasons.append(f"budget match: tier {proj_tier}")
            elif d == 1:
                score += 10
                reasons.append(f"budget near-match: tier {ent_tier} vs {proj_tier}")

        if country_norm and ent_country and ent_country == country_norm:
            if sec == "G":
                score += 90
                reasons.append(f"{country_norm} grant")
            else:
                score += 12
                reasons.append(f"country match: {country_norm}")

        if prefer_sections:
            if sec in prefer_sections:
                score += 10
                reasons.append(f"stage fit: {stage} → section {sec}")
            elif stage in {"concept", "prototype"} and sec in {"A", "B"}:
                score -= 5

        if r["comm_rating"]:
            score += int(r["comm_rating"]) * 4
            reasons.append(f"comm rating: {r['comm_rating']}★")

        if r["has_warning"]:
            score -= 15
            reasons.append("reputation warning")

        # genre match heuristic (deterministic, no LLM)
        hay = " ".join(
            [
                (r["notable_titles"] or ""),
                (r["notes"] or ""),
                (r["name"] or ""),
            ]
        ).lower()
        hits = [t for t in genre_terms if t in hay]
        if hits:
            score += min(15, 3 * len(hits))
            reasons.append(f"genre keywords: {', '.join(sorted(set(hits)))}")

        # keep AAA $3 publishers from dominating indie/mid budgets unless clear genre fit
        if proj_tier <= 2 and sec == "A" and str(ent_tier) == "3" and not hits:
            score -= 25
            reasons.append("too high-budget for project (no genre fit)")

        if platform:
            # placeholder for later refinement; deterministic but currently no strong signal in data
            pass

        scored.append(
            {
                "slug": r["slug"],
                "name": r["name"],
                "country": ent_country,
                "section": sec,
                "budget_tier": ent_tier,
                "comm_rating": r["comm_rating"],
                "has_warning": bool(r["has_warning"]),
                "headline": (r["notes"] or "")[:80] + ("…" if (r["notes"] and len(r["notes"]) > 80) else ""),
                "score": int(score),
                "reasons": reasons,
            }
        )

    scored.sort(key=lambda x: (x["score"], x["comm_rating"] or 0), reverse=True)
    candidates = scored[:15]
    return {
        "candidates": candidates,
        "excluded_count": excluded,
        "scoring_note": "Deterministic heuristic scoring (budget tier, country, stage section fit, comm, warnings, keyword hits).",
    }


def get_submission_brief(slug: str) -> dict[str, Any]:
    data = get_entity(slug)
    e = data["entity"]

    pitch = (e.get("pitch") or "").strip()
    contact = (e.get("contact") or "").strip()
    notes = (e.get("notes") or "").strip()

    channel = "unknown"
    target = None
    if pitch:
        if "@" in pitch:
            channel = "email"
            target = pitch
        if "form" in pitch.lower() or "portal" in pitch.lower():
            channel = "form"
            target = pitch
    if channel == "unknown" and contact and "@" in contact:
        channel = "email"
        target = contact

    text = notes
    # sentence-ish split
    parts = [p.strip() for p in re.split(r"[.;]\s+|\n+", text) if p.strip()]

    hard_filters: list[str] = []
    stated_criteria: list[str] = []
    warnings: list[str] = []

    hard_patterns = [
        re.compile(r"^only\s", re.I),
        re.compile(r"^strictly\s+no\s", re.I),
        re.compile(r"\bno\s+sandbox\b", re.I),
        re.compile(r"\bminimum\b", re.I),
        re.compile(r"\bcurrently signing\b", re.I),
        re.compile(r"\bat capacity\b", re.I),
        re.compile(r"\bnot taking\b", re.I),
    ]

    for p in parts:
        if "⚠️" in p or "warning" in p.lower() or "due diligence" in p.lower():
            warnings.append(p.replace("⚠️", "").strip())
            continue
        if any(rx.search(p) for rx in hard_patterns):
            hard_filters.append(p)
        else:
            stated_criteria.append(p)

    portfolio = []
    if e.get("notable_titles"):
        portfolio = [t.strip() for t in str(e["notable_titles"]).split(",") if t.strip()]

    return {
        "slug": e["slug"],
        "name": e["name"],
        "channel": channel,
        "target": target,
        "stated_criteria": stated_criteria,
        "hard_filters": hard_filters,
        "warnings": warnings,
        "portfolio": portfolio,
        "comm_rating": e.get("comm_rating"),
    }


def get_pitch_rubric(*, target_slug: str | None = None, funding_type: str | None = None) -> dict[str, Any]:
    # funding_type inference
    if not funding_type and target_slug:
        ent = get_entity(target_slug)["entity"]
        sec = ent.get("section")
        if sec == "E":
            funding_type = "vc_equity"
        elif sec == "G":
            funding_type = "grant"
        else:
            funding_type = "publisher"
    funding_type = funding_type or "publisher"

    def slide(n: int, title: str, must: list[str], weight: int, mistakes: list[str]):
        return {
            "n": n,
            "title": title,
            "must_contain": must,
            "weight": weight,
            "common_mistakes": mistakes,
        }

    if funding_type == "vc_equity":
        slides = [
            slide(1, "Vision", ["one-liner", "why now"], 3, ["vague vision"]),
            slide(2, "Market", ["target audience", "comps"], 3, ["no TAM/SOM context"]),
            slide(3, "Team", ["team size", "track record"], 3, ["missing roles"]),
            slide(4, "Traction", ["wishlist", "demo metrics"], 4, ["no proof points"]),
            slide(5, "Business model", ["price", "LTV/ARPU assumptions"], 3, ["no unit economics"]),
            slide(6, "Roadmap", ["milestones", "timeline"], 3, ["no dates"]),
            slide(7, "Funding", ["ask", "use of funds", "runway"], 5, ["no ask"]),
            slide(8, "Risks", ["top risks", "mitigations"], 2, ["hand-wavy"]),
        ]
    elif funding_type == "grant":
        slides = [
            slide(1, "Project summary", ["one-liner", "scope"], 3, ["unclear scope"]),
            slide(2, "Eligibility", ["region/company eligibility"], 5, ["no eligibility match"]),
            slide(3, "Budget", ["cost breakdown", "requested amount"], 5, ["no cost breakdown"]),
            slide(4, "Timeline", ["milestones", "deliverables"], 4, ["no deliverables"]),
            slide(5, "Impact", ["regional/cultural impact", "jobs"], 4, ["no impact argument"]),
            slide(6, "Team", ["roles", "capacity"], 3, ["missing capacity proof"]),
        ]
    else:  # publisher
        slides = [
            slide(1, "Hook", ["one-liner", "genre", "USP"], 4, ["no hook"]),
            slide(2, "Game", ["core loop", "pillars"], 5, ["no core loop"]),
            slide(3, "Audience", ["target player", "comps"], 3, ["no comps"]),
            slide(4, "Production", ["team size", "timeline"], 4, ["no timeline"]),
            slide(5, "Budget & ask", ["budget", "ask", "recoup / rev share"], 5, ["no numbers"]),
            slide(6, "Traction", ["wishlist", "playtest", "demo"], 4, ["no traction"]),
            slide(7, "Build", ["build link", "vertical slice"], 4, ["no build link"]),
        ]

    target_specific: list[str] = []
    if target_slug:
        brief = get_submission_brief(target_slug)
        target_specific = (brief.get("hard_filters") or []) + (brief.get("warnings") or [])

    return {"funding_type": funding_type, "slides": slides, "target_specific": target_specific}


def review_pitch(
    deck_markdown: str,
    *,
    target_slug: str | None = None,
    funding_type: str | None = None,
) -> dict[str, Any]:
    rubric = get_pitch_rubric(target_slug=target_slug, funding_type=funding_type)
    slides = rubric["slides"]

    text = deck_markdown or ""
    lower = text.lower()

    # crude "slide" detection by headings
    headings = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("#")]
    slide_count = len(headings) if headings else 1
    words = len(re.findall(r"\w+", text))

    # build a map from heading title -> section text
    sections: dict[str, str] = {}
    current = "deck"
    buf: list[str] = []
    for ln in text.splitlines():
        if ln.strip().startswith("#"):
            sections[current] = "\n".join(buf).strip()
            buf = []
            current = re.sub(r"^#+\s*", "", ln).strip()
        else:
            buf.append(ln)
    sections[current] = "\n".join(buf).strip()

    def find_section_for(title: str) -> str:
        # exact or substring match on heading keys
        t = title.lower()
        for h, body in sections.items():
            if t == h.lower() or t in h.lower() or h.lower() in t:
                return body
        return ""

    coverage: dict[str, str] = {}
    hard_findings: list[dict[str, Any]] = []

    for s in slides:
        title = s["title"]
        body = find_section_for(title)
        if not body and title.lower() not in lower:
            coverage[title] = "missing"
            hard_findings.append({"severity": "major", "slide": title, "issue": "Missing required slide"})
            continue

        # thin: present but too short or misses must_contain keywords (best-effort)
        body_words = len(re.findall(r"\w+", body)) if body else 0
        must = [m.lower() for m in s.get("must_contain", [])]
        has_must = any(m in (body.lower() if body else lower) for m in must) if must else True
        if body_words and body_words < 20:
            coverage[title] = "thin"
            hard_findings.append({"severity": "minor", "slide": title, "issue": "Slide content is very short"})
        elif must and not has_must:
            coverage[title] = "thin"
            hard_findings.append({"severity": "minor", "slide": title, "issue": "Missing key elements for this slide"})
        else:
            coverage[title] = "present"

    # numeric checks
    number_patterns = {
        "budget": re.compile(r"\bbudget\b|\$\s*\d|\b€\s*\d", re.I),
        "ask": re.compile(r"\bask\b|\braising\b|\bseeking\b", re.I),
        "timeline": re.compile(r"\bQ[1-4]\b|\bmonth\b|\bweeks?\b|\b20\d{2}\b", re.I),
        "team": re.compile(r"\bteam\b|\bpeople\b|\bdevs?\b", re.I),
        "recoup": re.compile(r"\brecoup\b|\brev share\b|\bsplit\b", re.I),
    }
    for k, rx in number_patterns.items():
        if not rx.search(text):
            hard_findings.append({"severity": "major", "slide": None, "issue": f"Missing concrete {k} details"})

    # build link checks
    if not re.search(r"https?://", text):
        hard_findings.append({"severity": "major", "slide": None, "issue": "Missing link to a build / demo / trailer"})

    # target hard filters
    if target_slug:
        brief = get_submission_brief(target_slug)
        hard = " ".join(brief.get("hard_filters") or []).lower()
        if "no sandbox" in hard and "sandbox" in lower:
            hard_findings.append({"severity": "blocker", "slide": None, "issue": "Violates target hard filter: no sandbox"})

    if slide_count > 20:
        hard_findings.append({"severity": "minor", "slide": None, "issue": "Deck is longer than 20 slides"})

    return {
        "hard_findings": hard_findings,
        "coverage": coverage,
        "rubric": rubric,
        "deck_stats": {"slides": slide_count, "words": words},
    }


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

