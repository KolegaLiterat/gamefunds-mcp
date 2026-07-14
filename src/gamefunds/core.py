from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from .db import DEFAULT_DB_PATH, connect, ensure_db
from .paths import data_dir, default_db_path
from .match_scoring import (
    assess_budget_scale,
    assess_genre_coverage,
    candidate_confidence,
    compute_catalog_budget_scale,
    corpus_text,
    country_match_score,
    entity_genre_haystack,
    entity_specialty_text,
    extract_hard_filters_from_text,
    prepare_genre_query,
    parse_genre_query,
    score_genre_match,
    split_submission_parts,
    violates_genre_exclusivity,
)
from .fts_query import build_fts5_match_query, is_fts_query_error
from .rubric_parser import FUNDING_TYPES, infer_funding_type

from .rubrics import load_rubrics


def _db_path() -> Path:
    return default_db_path()


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
    """Full-text search over the funding directory (FTS5).

    Accepts arbitrary plain-text queries. Hyphens, slashes, ampersands, colons,
    and parentheses are safe — compound genre labels like ``turn-based``,
    ``free-to-play``, ``AAA/AA``, and ``hack & slash`` are normalized to spaced
    phrases before matching.

    Supported syntax (after sanitization):
    - Multiple terms are OR-ed (union of matches, bm25-ranked).
    - Double-quoted input phrases, e.g. ``"cozy games"``, search as a literal phrase.
    - Prefix search: a trailing asterisk on an alphanumeric token, e.g. ``pixel*``.

    Empty or punctuation-only queries return ``{total_matched: 0, results: []}``
    without raising an error.
    """
    ensure_db(_db_path())
    limit = _clamp_limit(limit)
    offset = max(offset, 0)

    fts_query = build_fts5_match_query(query)
    if fts_query is None:
        return {"total_matched": 0, "results": []}

    try:
        with connect(_db_path()) as conn:
            total = conn.execute(
                "SELECT COUNT(*) AS n FROM entities_fts WHERE entities_fts MATCH ?;",
                (fts_query,),
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
                (fts_query, limit, offset),
            ).fetchall()
    except sqlite3.OperationalError as exc:
        if is_fts_query_error(exc):
            return {"total_matched": 0, "results": []}
        raise

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
    ensure_db(_db_path())
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
    ensure_db(_db_path())
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
    limit_per_type: int = 3,
) -> dict[str, Any]:
    """Score directory entities and return top matches grouped by funding path.

    Scores are comparable only within each funding_type group (publisher, grant,
    vc_equity, project_investor). Returns up to ``limit_per_type`` candidates per
    group (default 3, maximum 10).

    ``hard_filtered`` counts entities removed by budget-tier, grant-country, or genre
    exclusivity rules. ``below_cutoff`` counts scored entities not returned because
    of ``limit_per_type``.

    ``budget_usd`` must be positive. The catalog has meaningful budget resolution
    roughly between parsed ``amount_min_usd`` / ``amount_max_usd`` bounds (typically
    ~$5k–$5M). Outside that scale ``budget_signal`` is ``above_range`` or
    ``below_range`` and all candidates get ``confidence: low``.
    """
    if int(budget_usd) <= 0:
        raise ValueError("budget_usd must be a positive integer (USD).")

    ensure_db(_db_path())

    def project_budget_tier(b: int) -> int:
        if b < 200_000:
            return 1
        if b <= 2_000_000:
            return 2
        return 3

    proj_tier = project_budget_tier(int(budget_usd))
    stage = stage.strip().lower()
    country_norm = country.strip() if country else None

    prefer_sections: set[str] = set()
    if stage in {"concept", "prototype"}:
        prefer_sections = {"E", "F", "G"}
    elif stage in {"vertical_slice", "alpha", "beta"}:
        prefer_sections = {"A", "B", "C"}

    with connect(_db_path()) as conn:
        rows = conn.execute("SELECT * FROM entities;").fetchall()

    budget_scale = compute_catalog_budget_scale(rows)
    budget_signal, budget_warning = assess_budget_scale(int(budget_usd), budget_scale)

    prepared = prepare_genre_query(genre, rows)
    genre_phrases = prepared.phrases
    genre_tokens = prepared.tokens
    primary_genre_tokens = prepared.primary_tokens
    exclusivity_phrases, exclusivity_tokens, _, _ = parse_genre_query(genre)
    corpus = corpus_text(rows)
    genre_signal, genre_terms, genre_warning = assess_genre_coverage(
        prepared.significant_terms,
        corpus,
    )
    corpus_has_genre = genre_signal == "good"
    hard_filtered = 0
    scored: list[dict[str, Any]] = []

    for r in rows:
        sec = r["section"]
        ft = infer_funding_type(sec)
        ent_country = r["country"]
        ent_tier = r["budget_tier"]
        amount_min = r["amount_min_usd"]
        amount_max = r["amount_max_usd"]

        if violates_genre_exclusivity(exclusivity_phrases, exclusivity_tokens, r):
            hard_filtered += 1
            continue

        if ent_tier is None and amount_max is not None:
            ent_tier = budget_tier_from_amount_max(int(amount_max))
        elif ent_tier is None and amount_min is not None:
            ent_tier = budget_tier_from_amount_max(int(amount_min))

        if ent_tier is not None and abs(int(ent_tier) - proj_tier) >= 2:
            hard_filtered += 1
            continue

        if sec == "G" and country_norm and ent_country and ent_country != country_norm:
            hard_filtered += 1
            continue

        breakdown: dict[str, int | None] = {
            "budget": None,
            "country": 0,
            "stage": 0,
            "comm": 0,
            "genre": 0,
            "penalty": 0,
        }
        reasons: list[str] = []
        has_budget_data = ent_tier is not None or amount_min is not None or amount_max is not None
        budget_confirmed = False

        if ent_tier is not None:
            d = abs(int(ent_tier) - proj_tier)
            if d == 0:
                breakdown["budget"] = 25
                budget_confirmed = True
                reasons.append(f"budget match: tier {proj_tier}")
            elif d == 1:
                breakdown["budget"] = 8
                reasons.append(f"budget near-match: tier {ent_tier} vs {proj_tier}")
        elif amount_min is not None or amount_max is not None:
            if amount_max is not None and int(budget_usd) <= int(amount_max):
                if amount_min is not None and int(budget_usd) >= int(amount_min):
                    breakdown["budget"] = 25
                    budget_confirmed = True
                    reasons.append(
                        f"within parsed budget range (${amount_min:,}–${amount_max:,})"
                    )
                else:
                    breakdown["budget"] = 20
                    budget_confirmed = True
                    reasons.append(f"within parsed budget ceiling (${amount_max:,})")
            elif amount_min is not None and int(budget_usd) >= int(amount_min):
                breakdown["budget"] = 10
                reasons.append(f"above parsed budget floor (${amount_min:,})")
            else:
                breakdown["budget"] = 0
                breakdown["penalty"] -= 10
                reasons.append("budget range may not fit project ask")
        else:
            breakdown["budget"] = None
            reasons.append("budget data unavailable")

        country_pts = country_match_score(
            funding_type=ft,
            section=sec,
            country_norm=country_norm,
            ent_country=ent_country,
            has_budget_data=has_budget_data,
        )
        if country_pts:
            breakdown["country"] = country_pts
            if sec == "G":
                reasons.append(f"{country_norm} grant")
            else:
                reasons.append(f"country match: {country_norm}")

        stage_fit = False
        if prefer_sections:
            if sec in prefer_sections:
                breakdown["stage"] = 14
                stage_fit = True
                reasons.append(f"stage fit: {stage} → section {sec}")
            elif stage in {"concept", "prototype"} and sec in {"A", "B"}:
                breakdown["penalty"] -= 5
                reasons.append("stage mismatch for early project")

        if r["comm_rating"]:
            breakdown["comm"] = int(r["comm_rating"]) * 4
            reasons.append(f"comm rating: {r['comm_rating']}★")

        if r["has_warning"]:
            breakdown["penalty"] -= 15
            reasons.append("reputation warning")

        genre_hay = entity_genre_haystack(r)
        specialty_text = entity_specialty_text(r)
        if corpus_has_genre:
            genre_pts, genre_reasons, primary_genre_rank = score_genre_match(
                genre_phrases,
                genre_tokens,
                genre_hay,
                funding_type=ft,
                specialty_text=specialty_text,
                primary_tokens=primary_genre_tokens,
                noise_tokens=prepared.noise_tokens,
            )
        else:
            genre_pts, genre_reasons, primary_genre_rank = 0, [], 0
        if genre_pts:
            breakdown["genre"] = genre_pts
            reasons.extend(genre_reasons)

        if proj_tier <= 2 and sec == "A" and str(ent_tier) == "3" and genre_pts == 0:
            breakdown["penalty"] -= 25
            reasons.append("too high-budget for project (no genre fit)")

        score = _score_from_breakdown(breakdown)

        scored.append(
            {
                "slug": r["slug"],
                "name": r["name"],
                "country": ent_country,
                "section": sec,
                "funding_type": ft,
                "budget_tier": ent_tier,
                "amount_min_usd": amount_min,
                "amount_max_usd": amount_max,
                "comm_rating": r["comm_rating"],
                "has_warning": bool(r["has_warning"]),
                "headline": (r["notes"] or "")[:80] + ("…" if (r["notes"] and len(r["notes"]) > 80) else ""),
                "score": int(score),
                "breakdown": breakdown,
                "confidence": "medium",
                "reasons": reasons,
                "_budget_confirmed": budget_confirmed,
                "_stage_fit": stage_fit,
                "_has_budget_data": has_budget_data,
                "_primary_genre_rank": primary_genre_rank,
            }
        )

    for item in scored:
        b = item["breakdown"]
        item.pop("_primary_genre_rank", None)
        item["confidence"] = candidate_confidence(
            genre_signal=genre_signal,
            budget_signal=budget_signal,
            budget_confirmed=item.pop("_budget_confirmed"),
            stage_fit=item.pop("_stage_fit"),
            genre_score=int(b.get("genre") or 0),
            has_budget_data=item.pop("_has_budget_data"),
            budget_score=int(b.get("budget") or 0),
            country_score=int(b.get("country") or 0),
            funding_type=item["funding_type"],
        )

    cap = min(10, max(1, int(limit_per_type)))
    buckets: dict[str, list[dict[str, Any]]] = {ft: [] for ft in FUNDING_TYPES}
    for item in scored:
        buckets[item["funding_type"]].append(item)

    by_type: dict[str, list[dict[str, Any]]] = {}
    group_notes: dict[str, str] = {}
    below_cutoff = 0
    for ft in FUNDING_TYPES:
        ranked = sorted(
            buckets[ft],
            key=lambda x: (x["score"], x.get("_primary_genre_rank", 0), x["comm_rating"] or 0),
            reverse=True,
        )
        by_type[ft] = ranked[:cap]
        below_cutoff += max(0, len(ranked) - len(by_type[ft]))
        if not by_type[ft]:
            group_notes[ft] = _match_group_empty_reason(
                ft,
                country=country_norm,
                stage=stage,
            )
        elif len(by_type[ft]) < cap:
            group_notes[ft] = (
                f"Showing all {len(by_type[ft])} matching {ft.replace('_', ' ')} "
                f"entries for this query (limit_per_type={cap})."
            )

    result: dict[str, Any] = {
        "by_type": by_type,
        "hard_filtered": hard_filtered,
        "below_cutoff": below_cutoff,
        "genre_signal": genre_signal,
        "genre_terms": genre_terms,
        "budget_signal": budget_signal,
        "note": (
            "These are separate funding paths, not competing options. "
            "Scores are comparable only within each group."
        ),
    }
    if genre_warning:
        result["genre_warning"] = genre_warning
    if budget_warning:
        result["budget_warning"] = budget_warning
    if group_notes:
        result["group_notes"] = group_notes
    return result


def _score_from_breakdown(breakdown: dict[str, int | None]) -> int:
    total = sum(v for v in breakdown.values() if isinstance(v, int))
    return max(0, total)


def _match_group_empty_reason(funding_type: str, *, country: str | None, stage: str) -> str:
    labels = {
        "publisher": "publishers",
        "grant": "grants",
        "vc_equity": "VC/equity investors",
        "project_investor": "project investors",
    }
    label = labels.get(funding_type, funding_type)
    parts = [f"No matching {label} in the directory after budget/stage filters."]
    if funding_type == "grant" and country:
        parts.append(f"Country filter for grants: {country}.")
    if stage:
        parts.append(f"Stage: {stage}.")
    return " ".join(parts)


def get_submission_brief(slug: str) -> dict[str, Any]:
    data = get_entity(slug)
    e = data["entity"]

    pitch = (e.get("pitch") or "").strip()
    contact = (e.get("contact") or "").strip()
    notes = (e.get("notes") or "").strip()
    eligibility = (e.get("eligibility") or "").strip()
    backing = (e.get("backing") or "").strip()
    funding_terms = (e.get("funding_terms") or "").strip()
    target_scope = (e.get("target_scope") or "").strip()
    terms_raw = (e.get("terms_raw") or "").strip()
    submit_links: list[dict[str, str]] = []
    if e.get("submit_links_json"):
        try:
            submit_links = json.loads(e["submit_links_json"])
        except json.JSONDecodeError:
            submit_links = []
    links: list[dict[str, str]] = []
    if e.get("links_json"):
        try:
            links = json.loads(e["links_json"])
        except json.JSONDecodeError:
            links = []

    channel = "unknown"
    target = None

    def _apply_channel_from_link(link: dict[str, str]) -> None:
        nonlocal channel, target
        url = (link.get("url") or "").strip()
        label = (link.get("label") or "").lower()
        if not url:
            return
        if "@" in url:
            channel = "email"
            target = url
        elif "form" in label or "apply" in label or "portal" in label or "application" in label:
            channel = "form"
            target = url
        elif channel == "unknown":
            channel = "link"
            target = url

    for link in submit_links:
        _apply_channel_from_link(link)
        if channel in {"form", "email"}:
            break

    def _apply_channel(value: str, *, prefer_form: bool = False) -> None:
        nonlocal channel, target
        if not value:
            return
        lower = value.lower()
        if "@" in value:
            channel = "email"
            target = value
        elif prefer_form or "form" in lower or "portal" in lower or "application" in lower:
            channel = "form"
            target = value
        elif channel == "unknown" and ("http://" in lower or "https://" in lower or lower.startswith("[") or "site" in lower):
            channel = "link"
            target = value

    if channel == "unknown":
        _apply_channel(pitch, prefer_form=True)
    if channel == "unknown":
        _apply_channel(contact)
    if channel == "unknown" and links:
        for link in links:
            url = (link.get("url") or "").strip()
            label = (link.get("label") or "").strip()
            if url:
                channel = "link"
                target = url
                if "form" in label.lower() or "apply" in label.lower() or "portal" in label.lower():
                    channel = "form"
                break
    if channel == "unknown" and e.get("contact_email"):
        channel = "email"
        target = e["contact_email"]

    text = notes
    parts = split_submission_parts(text, eligibility=eligibility)
    hard_filters = extract_hard_filters_from_text(text, eligibility=eligibility)
    hard_set = set(hard_filters)

    stated_criteria: list[str] = []
    warnings: list[str] = []

    for p in parts:
        if "⚠️" in p or "warning" in p.lower() or "due diligence" in p.lower():
            warnings.append(p.replace("⚠️", "").strip())
            continue
        if p in hard_set:
            continue
        stated_criteria.append(p)

    portfolio = []
    if e.get("notable_titles"):
        portfolio = [t.strip() for t in str(e["notable_titles"]).split(",") if t.strip()]

    return {
        "slug": e["slug"],
        "name": e["name"],
        "channel": channel,
        "target": target,
        "eligibility": eligibility or None,
        "backing": backing or None,
        "funding_terms": funding_terms or None,
        "target_scope": target_scope or None,
        "terms_raw": terms_raw or None,
        "submit_links": submit_links or None,
        "stated_criteria": stated_criteria,
        "hard_filters": hard_filters,
        "warnings": warnings,
        "portfolio": portfolio,
        "comm_rating": e.get("comm_rating"),
    }


def get_pitch_rubric(*, target_slug: str | None = None, funding_type: str | None = None) -> dict[str, Any]:
    bundle = load_rubrics(db_path=_db_path())
    base = bundle.data
    tailoring = base.get("tailoring") or {}

    if not funding_type and target_slug:
        ent = get_entity(target_slug)["entity"]
        funding_type = infer_funding_type(ent.get("section"))
    funding_type = funding_type or "publisher"
    if funding_type not in tailoring:
        raise ValueError(
            f"Invalid funding_type: {funding_type}. "
            f"Expected one of: {', '.join(sorted(tailoring))}"
        )

    fit = tailoring[funding_type]
    target_specific: list[str] = []
    if target_slug:
        brief = get_submission_brief(target_slug)
        target_specific = (brief.get("hard_filters") or []) + (brief.get("warnings") or [])

    out: dict[str, Any] = {
        "funding_type": funding_type,
        "slide_range": base.get("slide_range", [10, 20]),
        "slides": base.get("slides", []),
        "emphasize": fit.get("emphasize", []),
        "deemphasize": fit.get("deemphasize", []),
        "common_mistakes": base.get("common_mistakes", []),
        "design_rules": base.get("design_rules", []),
        "target_specific": target_specific,
        "rubric_stale": bundle.stale,
        "rubric_synced_at": bundle.synced_at,
    }
    return out


_SEVERITY_ORDER = {"blocker": 0, "major": 1, "minor": 2}


def _sort_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(findings, key=lambda f: _SEVERITY_ORDER.get(f.get("severity"), 99))


_DEEMPHASIZE_PATTERNS: dict[str, list[tuple[str, re.Pattern[str]]]] = {
    "publisher": [
        ("exit strategy", re.compile(r"\bexit\b", re.I)),
        ("market tam", re.compile(r"\btam\b|total addressable market", re.I)),
        ("company vision", re.compile(r"\bfive[- ]year\b|\bstudio vision\b|\blong[- ]term vision\b", re.I)),
    ],
    "project_investor": [
        ("long-term studio vision", re.compile(r"\blong[- ]term studio\b|\bfive[- ]year plan\b", re.I)),
        ("sequels", re.compile(r"\bsequel\b", re.I)),
        ("art philosophy", re.compile(r"\bart philosophy\b|\baesthetic philosophy\b", re.I)),
    ],
    "vc_equity": [
        ("deep mechanics", re.compile(r"\bcore loop\b|\bmechanics\b", re.I)),
        ("level design", re.compile(r"\blevel design\b", re.I)),
        ("art pipelines", re.compile(r"\bart pipeline\b", re.I)),
    ],
    "grant": [
        ("revenue projections", re.compile(r"\brevenue projection\b|\best\.?\s*revenue\b|\bgross revenue\b", re.I)),
        ("roi", re.compile(r"\broi\b|return on investment", re.I)),
        ("hype language", re.compile(r"\brevolutionary\b|\bgroundbreaking\b|\bdisruptive\b", re.I)),
    ],
}


def _deemphasize_findings(funding_type: str, text: str, deemphasize: list[str]) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    patterns = _DEEMPHASIZE_PATTERNS.get(funding_type, [])
    lower = text.lower()
    for phrase in deemphasize:
        phrase_lower = phrase.lower()
        matched = False
        for label, rx in patterns:
            if label in phrase_lower or phrase_lower in label:
                if rx.search(text):
                    matched = True
                    findings.append(
                        {
                            "severity": "minor",
                            "slide": None,
                            "issue": f"De-emphasized for {funding_type}: {phrase}",
                        }
                    )
                break
        if not matched and len(phrase_lower) > 6 and phrase_lower in lower:
            findings.append(
                {
                    "severity": "minor",
                    "slide": None,
                    "issue": f"De-emphasized for {funding_type}: {phrase}",
                }
            )
    return findings


def _normalize_slide_title(title: str) -> str:
    t = title.lower()
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _slide_titles_match(rubric_title: str, deck_title: str) -> bool:
    r = _normalize_slide_title(rubric_title)
    d = _normalize_slide_title(deck_title)
    if not r or not d:
        return False
    if r == d or r in d or d in r:
        return True
    r_tokens = set(r.split())
    d_tokens = set(d.split())
    if len(r_tokens & d_tokens) >= min(2, len(r_tokens)):
        return True
    if ("hook" in r or "title" in r) and ("hook" in d or "title" in d):
        return True
    if "budget" in r and "ask" in r and "budget" in d and "ask" in d:
        return True
    if "market" in r and "compar" in r and ("market" in d or "compar" in d):
        return True
    if "usp" in r and "usp" in d:
        return True
    if "look" in r and "feel" in r and "look" in d:
        return True
    if "production" in r and "plan" in r and "production" in d:
        return True
    return False


def _parse_deck_sections(text: str) -> tuple[dict[str, str], list[str]]:
    sections: dict[str, str] = {}
    headings: list[str] = []
    lines = text.splitlines()
    i = 0
    first_h1 = True
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped.startswith("#"):
            i += 1
            continue
        level = len(stripped) - len(stripped.lstrip("#"))
        title = re.sub(r"^#+\s*", "", stripped).strip()
        i += 1
        buf: list[str] = []
        while i < len(lines):
            if lines[i].strip().startswith("#"):
                break
            buf.append(lines[i])
            i += 1
        body = "\n".join(buf).strip()
        headings.append(title)
        sections[title] = body
        if level == 1 and first_h1:
            sections["Title / Hook"] = body
            first_h1 = False
    return sections, headings


def _find_deck_section(rubric_title: str, sections: dict[str, str]) -> str:
    if rubric_title in sections:
        return sections[rubric_title]
    for heading, body in sections.items():
        if _slide_titles_match(rubric_title, heading):
            return body
    return ""


def _slide_thin_issue(rubric_title: str, body: str) -> str | None:
    if not body.strip():
        return None
    tl = rubric_title.lower()
    if "market" in tl and "compar" in tl:
        has_number = bool(re.search(r"\d", body))
        items = [p.strip() for p in re.split(r"[,;\n|·•]+", body) if p.strip()]
        if not has_number or len(items) < 3:
            return "Missing key elements for this slide"
        return None
    if "traction" in tl:
        if not re.search(r"\d", body):
            return "Missing key elements for this slide"
        return None
    if tl.startswith("team") or tl == "team":
        bl = body.lower()
        team_signals = (
            "shipped",
            "copies",
            "released",
            " programmer",
            " producer",
            " designer",
            "lead ",
            " titles",
            "mod",
            "jam",
        )
        if not any(s in bl for s in team_signals):
            return "Missing key elements for this slide"
        return None
    if "hook" in tl or "title" in tl:
        bl = body.lower()
        if "fans of" not in bl and not re.search(r"\bfor fans\b", bl):
            return "Missing key elements for this slide"
        return None
    if "budget" in tl and "ask" in tl:
        if not re.search(r"\bask\b|\bbudget\b|\$\s*\d|\b€\s*\d|\d+\s*k\b", body, re.I):
            return "Missing key elements for this slide"
        return None
    return None


def review_pitch(
    deck_markdown: str,
    *,
    target_slug: str | None = None,
    funding_type: str | None = None,
    include_rubric: bool = False,
) -> dict[str, Any]:
    rubric = get_pitch_rubric(target_slug=target_slug, funding_type=funding_type)
    slides = rubric["slides"]
    slide_range = rubric.get("slide_range") or [10, 20]

    text = deck_markdown or ""
    lower = text.lower()

    sections, headings = _parse_deck_sections(text)
    slide_count = len(headings) if headings else (1 if text.strip() else 0)
    words = len(re.findall(r"\w+", text))

    coverage: dict[str, str] = {}
    hard_findings: list[dict[str, Any]] = []

    for s in slides:
        title = s["title"]
        body = _find_deck_section(title, sections)
        if not body:
            coverage[title] = "missing"
            hard_findings.append({"severity": "major", "slide": title, "issue": "Missing required slide"})
            continue

        thin_issue = _slide_thin_issue(title, body)
        if thin_issue:
            coverage[title] = "thin"
            hard_findings.append({"severity": "minor", "slide": title, "issue": thin_issue})
        else:
            coverage[title] = "present"

    min_slides, max_slides = slide_range[0], slide_range[1]
    if slide_count < min_slides:
        hard_findings.append(
            {
                "severity": "major",
                "slide": None,
                "issue": f"Deck has fewer than {min_slides} slides (tutorial expects {min_slides}–{max_slides})",
            }
        )
    if slide_count > max_slides:
        hard_findings.append(
            {
                "severity": "minor",
                "slide": None,
                "issue": f"Deck is longer than {max_slides} slides (tutorial expects {min_slides}–{max_slides})",
            }
        )

    if not re.search(r"\bask\b|\bbudget\b|\$\s*\d|\b€\s*\d", text, re.I):
        hard_findings.append({"severity": "major", "slide": None, "issue": "Missing concrete budget / ask details"})

    if not re.search(r"https?://", text):
        hard_findings.append({"severity": "major", "slide": None, "issue": "Missing link to a build / demo / trailer"})

    hard_findings.extend(
        _deemphasize_findings(rubric["funding_type"], text, rubric.get("deemphasize") or [])
    )

    if target_slug:
        brief = get_submission_brief(target_slug)
        hard = " ".join(brief.get("hard_filters") or []).lower()
        if "no sandbox" in hard and "sandbox" in lower:
            hard_findings.append({"severity": "blocker", "slide": None, "issue": "Violates target hard filter: no sandbox"})

    out: dict[str, Any] = {
        "hard_findings": _sort_findings(hard_findings),
        "coverage": coverage,
        "deck_stats": {"slides": slide_count, "words": words},
        "rubric_stale": rubric.get("rubric_stale", False),
        "rubric_synced_at": rubric.get("rubric_synced_at"),
    }
    if include_rubric:
        out["rubric"] = rubric
    return out


def set_status(
    slug: str,
    status: str,
    *,
    project: str | None = None,
    next_followup: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    ensure_db(_db_path())
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
    ensure_db(_db_path())
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
    ensure_db(_db_path())

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

    from .guides import GUIDE_SPECS, guides_dir

    topic_norm = topic.strip().lower()

    help_topics = {
        "sections": "sections.md",
        "scoring": "scoring.md",
        "statuses": "statuses.md",
        "tiers": "tiers.md",
        "workflows": "workflows.md",
        "troubleshooting": "troubleshooting.md",
    }
    guide_topics = {
        "funding-types": "FundingTypes.md",
        "definitions": "Definitions.md",
        "pitch-deck": "PitchDeckTutorial.md",
    }

    if topic_norm == "guides":
        return _guides_index()

    if topic_norm in help_topics:
        return (data_dir() / "help" / help_topics[topic_norm]).read_text(encoding="utf-8")

    if topic_norm in guide_topics:
        path = guides_dir() / guide_topics[topic_norm]
        if path.exists():
            return path.read_text(encoding="utf-8")
        uri = next(s["uri"] for s in GUIDE_SPECS if s["local"] == guide_topics[topic_norm])
        return (
            f"Guide `{topic_norm}` is not synced locally yet.\n\n"
            f"Read resource `{uri}` or run `sync_directory(dry_run=False)`.\n\n"
            + _topic_catalog(help_topics, guide_topics)
        )

    return _topic_catalog(help_topics, guide_topics, unknown=topic_norm or None)


def _topic_catalog(
    help_topics: dict[str, str],
    guide_topics: dict[str, str],
    *,
    unknown: str | None = None,
) -> str:
    lines = ["## Available `gamefunds_help` topics", ""]
    if unknown is not None:
        lines.append(f'Unknown topic: "{unknown}". Choose one of the following.')
        lines.append("")
    lines.extend(
        [
            "### Operational help",
            "",
            *[f"- `{name}`" for name in help_topics],
            "",
            "### Concept guides (same as `gamefunds://guide/*`)",
            "",
            "- `guides` — index of guides and when to read them",
            *[f"- `{name}`" for name in guide_topics],
            "",
            "Questions about publishing vs project investment vs equity, recoup, waterfall, vertical slice, "
            "or pitch deck structure — call `funding-types`, `definitions`, or `pitch-deck`. "
            "Do not answer from memory.",
        ]
    )
    return "\n".join(lines)


def _guides_index() -> str:
    return """## GameFunds guide resources (`gamefunds://guide/*`)

Six long-form guides (EN + PL). Read these for **concepts** — not the directory listing.

| Topic | `gamefunds_help` | Resource (EN) | Resource (PL) | What's inside |
|---|---|---|---|---|
| Funding types | `funding-types` | `gamefunds://guide/funding-types` | `gamefunds://guide/funding-types/pl` | Publishing deal vs **project investment** (revenue share in *one game*, no equity) vs **equity** (company shares). Which path to pick. |
| Definitions | `definitions` | `gamefunds://guide/definitions` | `gamefunds://guide/definitions/pl` | Recoup, waterfall, dilution, vertical slice, milestone, net revenue, and other deal vocabulary. |
| Pitch deck | `pitch-deck` | `gamefunds://guide/pitch-deck` | `gamefunds://guide/pitch-deck/pl` | Canonical slide structure, what each slide must contain, common mistakes. |

**When to use:** any question about *what a deal type means*, *whether you give up company equity*, *how recoup works*, or *how to structure a deck* — fetch the guide here or via `read_resource`, then answer from that text.

Operational help (sections A–G, scoring weights, pipeline statuses) stays in `gamefunds_help("sections")`, `"scoring"`, etc.
"""

