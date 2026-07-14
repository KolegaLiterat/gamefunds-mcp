from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from .db import DEFAULT_DB_PATH, connect, ensure_db, upsert_entities
from .guides import GUIDE_SPECS, UPSTREAM_GUIDE_FILES, guide_sha_meta_key, guides_dir
from .parser import ParseError, parse_directory_markdown
from .rubrics import RUBRICS_PATH, sync_rubrics_from_tutorial

PITCH_TUTORIAL = "PitchDeckTutorial.md"

REPO = "GameDevGrzesiek/GameFunds"
BRANCH = "main"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
API_BASE = f"https://api.github.com/repos/{REPO}"


def _github_headers() -> dict[str, str]:
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    tok = os.getenv("GITHUB_TOKEN")
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
    return headers


def _meta_get(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _meta_set(conn, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?);", (key, value))


def _fetch_directory_commit(client: httpx.Client) -> dict[str, Any]:
    url = f"{API_BASE}/commits"
    params = {"path": "GameFundingDirectory.md", "per_page": 1}
    r = client.get(url, params=params, headers=_github_headers())
    r.raise_for_status()
    data = r.json()
    if not data:
        raise RuntimeError("GitHub API returned no commits for GameFundingDirectory.md")
    latest = data[0]
    commit = latest.get("commit") or {}
    msg = (commit.get("message") or "").splitlines()[0] if commit.get("message") else None
    date = (commit.get("committer") or {}).get("date")
    return {
        "current_sha": latest.get("sha"),
        "commit_message": msg,
        "commit_date": date,
    }


def _fetch_guide_blob_sha(client: httpx.Client, upstream_name: str) -> str:
    url = f"{API_BASE}/contents/{upstream_name}"
    r = client.get(url, headers=_github_headers())
    r.raise_for_status()
    data = r.json()
    sha = data.get("sha")
    if not sha:
        raise RuntimeError(f"GitHub API returned no blob sha for {upstream_name}")
    return sha


def _load_guide_status(client: httpx.Client, conn) -> dict[str, dict[str, Any]]:
    guides: dict[str, dict[str, Any]] = {}
    for upstream in UPSTREAM_GUIDE_FILES:
        current_sha = _fetch_guide_blob_sha(client, upstream)
        last_sha = _meta_get(conn, guide_sha_meta_key(upstream))
        guides[upstream] = {
            "has_update": current_sha != last_sha,
            "current_sha": current_sha,
            "last_synced_sha": last_sha,
        }
    return guides


def check_updates() -> dict[str, Any]:
    """Check GitHub for newer commits affecting the directory or guide files."""
    ensure_db(DEFAULT_DB_PATH)
    with connect(DEFAULT_DB_PATH) as conn:
        last_sha = _meta_get(conn, "last_sync_sha")
        last_at = _meta_get(conn, "last_sync_at")

    with httpx.Client(timeout=20) as client:
        directory = _fetch_directory_commit(client)
        with connect(DEFAULT_DB_PATH) as conn:
            guides = _load_guide_status(client, conn)

    directory_has_update = directory["current_sha"] is not None and directory["current_sha"] != last_sha
    guides_have_update = any(g["has_update"] for g in guides.values())
    changed_guides = sorted(name for name, g in guides.items() if g["has_update"])

    return {
        "has_update": directory_has_update or guides_have_update,
        "current_sha": directory["current_sha"],
        "last_synced_sha": last_sha,
        "last_synced_at": last_at,
        "commit_message": directory["commit_message"],
        "commit_date": directory["commit_date"],
        "directory_has_update": directory_has_update,
        "guides_have_update": guides_have_update,
        "changed_guides": changed_guides,
        "guides": guides,
    }


def _fetch_raw(client: httpx.Client, upstream_name: str) -> str:
    r = client.get(f"{RAW_BASE}/{upstream_name}")
    r.raise_for_status()
    return r.text


def _should_rebuild_rubrics(
    *,
    tutorial_updated: bool,
) -> bool:
    if tutorial_updated:
        return True
    if not RUBRICS_PATH.exists():
        return True
    with connect(DEFAULT_DB_PATH) as conn:
        if _meta_get(conn, "rubric_stale") == "true":
            return True
    return False


def _sync_rubrics_if_needed(
    *,
    dry_run: bool,
    guide_status: dict[str, dict[str, Any]],
    pitch_tutorial_text: str | None,
    pitch_tutorial_sha: str | None,
    tutorial_updated: bool,
    guides_root: Path,
) -> tuple[bool, str | None]:
    if dry_run or not _should_rebuild_rubrics(tutorial_updated=tutorial_updated):
        return False, None

    text = pitch_tutorial_text
    sha = pitch_tutorial_sha
    if text is None:
        local_path = guides_root / PITCH_TUTORIAL
        if not local_path.exists():
            return False, f"{PITCH_TUTORIAL} missing locally; cannot rebuild rubrics"
        text = local_path.read_text(encoding="utf-8")
        sha = guide_status.get(PITCH_TUTORIAL, {}).get("current_sha")

    return sync_rubrics_from_tutorial(text, source_sha=sha, db_path=DEFAULT_DB_PATH)


def _sync_guides(
    client: httpx.Client,
    *,
    dry_run: bool,
    guide_status: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    target = guides_dir()
    added: list[str] = []
    updated: list[str] = []
    unchanged: list[str] = []

    pitch_tutorial_text: str | None = None
    pitch_tutorial_sha: str | None = None
    pitch_tutorial_updated = False

    for spec in GUIDE_SPECS:
        upstream = spec["upstream"]
        local_name = spec["local"]
        local_path = target / local_name
        status = guide_status[upstream]
        exists = local_path.exists()

        if not status["has_update"] and exists:
            unchanged.append(upstream)
            continue

        if dry_run:
            if exists:
                updated.append(upstream)
            else:
                added.append(upstream)
            continue

        content = _fetch_raw(client, upstream)
        target.mkdir(parents=True, exist_ok=True)
        local_path.write_text(content, encoding="utf-8")
        if upstream == "PitchDeckTutorial.md":
            pitch_tutorial_text = content
            pitch_tutorial_sha = status.get("current_sha")
            pitch_tutorial_updated = True
        if exists:
            updated.append(upstream)
        else:
            added.append(upstream)

    applied = False
    if not dry_run and (added or updated):
        with connect(DEFAULT_DB_PATH) as conn:
            for upstream in added + updated:
                _meta_set(conn, guide_sha_meta_key(upstream), guide_status[upstream]["current_sha"] or "")
            conn.commit()
        applied = True

    rubric_applied = False
    rubric_parse_error: str | None = None
    if not dry_run:
        rubric_applied, rubric_parse_error = _sync_rubrics_if_needed(
            dry_run=dry_run,
            guide_status=guide_status,
            pitch_tutorial_text=pitch_tutorial_text,
            pitch_tutorial_sha=pitch_tutorial_sha,
            tutorial_updated=pitch_tutorial_updated,
            guides_root=target,
        )

    return {
        "guides_added": len(added),
        "guides_updated": len(updated),
        "guides_unchanged": len(unchanged),
        "guide_changes": sorted(added + updated),
        "guides_applied": applied,
        "rubric_applied": rubric_applied,
        "rubric_parse_error": rubric_parse_error,
    }


def sync_directory(*, dry_run: bool = True, full_diff: bool = False) -> dict[str, Any]:
    """Fetch, parse, diff, and optionally apply directory and guide updates."""
    ensure_db(DEFAULT_DB_PATH)

    info = check_updates()
    sha = info.get("current_sha")
    guide_status = info.get("guides") or {}

    parser_error: str | None = None
    parsed: list[dict[str, Any]] | None = None

    with httpx.Client(timeout=30) as client:
        md_resp = client.get(f"{RAW_BASE}/GameFundingDirectory.md")
        md_resp.raise_for_status()
        md = md_resp.text

        try:
            parsed = parse_directory_markdown(md)
        except ParseError as e:
            parser_error = (
                f"format repo się zmienił, parser wymaga aktualizacji (line {e.line_no}): {e} :: {e.raw_line}"
            )

        guide_result = _sync_guides(client, dry_run=dry_run, guide_status=guide_status)

    added_slugs: list[str] = []
    removed_slugs: list[str] = []
    changed: list[dict[str, Any]] = []
    sample: list[dict[str, Any]] = []
    existing: dict[str, dict[str, Any]] = {}

    if parsed is not None:
        with connect(DEFAULT_DB_PATH) as conn:
            existing_rows = conn.execute("SELECT * FROM entities").fetchall()
            existing = {r["slug"]: dict(r) for r in existing_rows}

        new_by_slug = {r["slug"]: r for r in parsed}
        added_slugs = sorted(set(new_by_slug) - set(existing))
        removed_slugs = sorted(set(existing) - set(new_by_slug))

        for slug in sorted(set(existing) & set(new_by_slug)):
            old = existing[slug]
            new = new_by_slug[slug]
            for k, v in new.items():
                if k in {"raw_row"}:
                    continue
                if old.get(k) != v:
                    changed.append({"slug": slug, "field": k, "old": old.get(k), "new": v})

        for s in added_slugs[:10]:
            sample.append({"kind": "added", "slug": s, "name": new_by_slug[s].get("name")})
        for s in removed_slugs[:10]:
            sample.append({"kind": "removed", "slug": s, "name": existing[s].get("name")})
        for c in changed[:10]:
            sample.append({"kind": "changed", **c})

    applied = False
    if not dry_run and parsed is not None:
        upsert_entities(parsed, db_path=DEFAULT_DB_PATH)
        with connect(DEFAULT_DB_PATH) as conn:
            _meta_set(conn, "last_sync_sha", sha or "")
            _meta_set(conn, "last_sync_at", datetime.now(timezone.utc).isoformat())
            conn.commit()
        applied = True

    if full_diff and parsed is not None:
        new_by_slug = {r["slug"]: r for r in parsed}
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
        "parser_error": parser_error,
        **guide_result,
    }
