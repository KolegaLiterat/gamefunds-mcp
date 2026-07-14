from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .db import DEFAULT_DB_PATH, connect, ensure_db
from .paths import rubrics_path
from .rubric_parser import RubricParseError, parse_pitch_tutorial

logger = logging.getLogger(__name__)

RUBRICS_PATH = rubrics_path()


@dataclass
class RubricBundle:
    data: dict[str, Any]
    stale: bool
    synced_at: str | None
    parse_error: str | None = None


def _meta_get(conn, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _meta_set(conn, key: str, value: str) -> None:
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?);", (key, value))


def load_rubrics(*, db_path: Path = DEFAULT_DB_PATH) -> RubricBundle:
    if not RUBRICS_PATH.exists():
        raise FileNotFoundError(f"Missing rubrics file: {RUBRICS_PATH}")

    data = json.loads(RUBRICS_PATH.read_text(encoding="utf-8"))
    stale = False
    synced_at = data.get("synced_at")
    parse_error = None

    try:
        ensure_db(db_path)
        with connect(db_path) as conn:
            stale_val = _meta_get(conn, "rubric_stale")
            stale = stale_val == "true"
            synced_at = _meta_get(conn, "rubric_synced_at") or synced_at
            parse_error = _meta_get(conn, "rubric_parse_error")
    except Exception:
        pass

    return RubricBundle(data=data, stale=stale, synced_at=synced_at, parse_error=parse_error)


def sync_rubrics_from_tutorial(
    tutorial_text: str,
    *,
    source_sha: str | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> tuple[bool, str | None]:
    """
    Parse tutorial and write rubrics.json.

    On parser failure, keep the previous rubrics.json and mark meta as stale.
    """
    ensure_db(db_path)
    try:
        parsed = parse_pitch_tutorial(tutorial_text)
    except RubricParseError as e:
        msg = f"{e}" + (f" (section: {e.section})" if e.section else "")
        logger.error("rubric parser failed: %s", msg)
        with connect(db_path) as conn:
            _meta_set(conn, "rubric_stale", "true")
            _meta_set(conn, "rubric_parse_error", msg)
            conn.commit()
        return False, msg

    now = datetime.now(timezone.utc).isoformat()
    payload = {
        "synced_at": now,
        "source_file": "PitchDeckTutorial.md",
        "source_sha": source_sha,
        **parsed,
    }

    RUBRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RUBRICS_PATH.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    with connect(db_path) as conn:
        _meta_set(conn, "rubric_stale", "false")
        _meta_set(conn, "rubric_synced_at", now)
        _meta_set(conn, "rubric_parse_error", "")
        if source_sha:
            _meta_set(conn, "rubric_source_sha", source_sha)
        conn.commit()

    return True, None
