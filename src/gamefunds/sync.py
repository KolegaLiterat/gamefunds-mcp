from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import httpx

from .db import DEFAULT_DB_PATH, connect, init_db, upsert_entities
from .parser import ParseError, parse_directory_markdown

def check_updates() -> dict[str, Any]:
    """Check GitHub for a newer commit affecting GameFundingDirectory.md."""
    init_db(DEFAULT_DB_PATH)
    with connect(DEFAULT_DB_PATH) as conn:
        row = conn.execute("SELECT value FROM meta WHERE key='last_sync_sha'").fetchone()
        last_sha = row["value"] if row else None
        row = conn.execute("SELECT value FROM meta WHERE key='last_sync_at'").fetchone()
        last_at = row["value"] if row else None

    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    tok = os.getenv("GITHUB_TOKEN")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"

    url = "https://api.github.com/repos/GameDevGrzesiek/GameFunds/commits"
    params = {"path": "GameFundingDirectory.md", "per_page": 1}
    with httpx.Client(timeout=20) as client:
        r = client.get(url, params=params, headers=headers)
        r.raise_for_status()
        data = r.json()

    if not data:
        raise RuntimeError("GitHub API returned no commits for GameFundingDirectory.md")
    latest = data[0]
    sha = latest.get("sha")
    commit = latest.get("commit") or {}
    msg = (commit.get("message") or "").splitlines()[0] if commit.get("message") else None
    date = (commit.get("committer") or {}).get("date")

    return {
        "has_update": (sha is not None and sha != last_sha),
        "current_sha": sha,
        "last_synced_sha": last_sha,
        "last_synced_at": last_at,
        "commit_message": msg,
        "commit_date": date,
    }


def sync_directory(*, dry_run: bool = True, full_diff: bool = False) -> dict[str, Any]:
    """Fetch, parse, diff, and optionally apply the directory update."""
    init_db(DEFAULT_DB_PATH)

    info = check_updates()
    sha = info.get("current_sha")

    raw_url = "https://raw.githubusercontent.com/GameDevGrzesiek/GameFunds/main/GameFundingDirectory.md"
    with httpx.Client(timeout=30) as client:
        md_resp = client.get(raw_url)
        md_resp.raise_for_status()
        md = md_resp.text

    try:
        parsed = parse_directory_markdown(md)
    except ParseError as e:
        return {
            "has_update": info.get("has_update", True),
            "added": 0,
            "removed": 0,
            "changed": 0,
            "sample": [],
            "applied": False,
            "parser_error": f"format repo się zmienił, parser wymaga aktualizacji (line {e.line_no}): {e} :: {e.raw_line}",
        }

    with connect(DEFAULT_DB_PATH) as conn:
        existing_rows = conn.execute("SELECT * FROM entities").fetchall()
        existing = {r["slug"]: dict(r) for r in existing_rows}

    new_by_slug = {r["slug"]: r for r in parsed}

    added_slugs = sorted(set(new_by_slug) - set(existing))
    removed_slugs = sorted(set(existing) - set(new_by_slug))

    changed: list[dict[str, Any]] = []
    for slug in sorted(set(existing) & set(new_by_slug)):
        old = existing[slug]
        new = new_by_slug[slug]
        for k, v in new.items():
            if k in {"raw_row"}:
                continue
            if old.get(k) != v:
                changed.append({"slug": slug, "field": k, "old": old.get(k), "new": v})

    sample: list[dict[str, Any]] = []
    for s in added_slugs[:10]:
        sample.append({"kind": "added", "slug": s, "name": new_by_slug[s].get("name")})
    for s in removed_slugs[:10]:
        sample.append({"kind": "removed", "slug": s, "name": existing[s].get("name")})
    for c in changed[:10]:
        sample.append({"kind": "changed", **c})

    applied = False
    if not dry_run:
        upsert_entities(parsed, db_path=DEFAULT_DB_PATH)
        with connect(DEFAULT_DB_PATH) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?);",
                ("last_sync_sha", sha or ""),
            )
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?);",
                ("last_sync_at", datetime.now(timezone.utc).isoformat()),
            )
            conn.commit()
        applied = True

    if full_diff:
        diff_payload: Any = {
            "added": [{"slug": s, "name": new_by_slug[s].get("name")} for s in added_slugs],
            "removed": [{"slug": s, "name": existing[s].get("name")} for s in removed_slugs],
            "changed": changed,
        }
        sample = diff_payload

    return {
        "has_update": info.get("has_update", True),
        "added": len(added_slugs),
        "removed": len(removed_slugs),
        "changed": len(changed),
        "sample": sample,
        "applied": applied,
        "parser_error": None,
    }

