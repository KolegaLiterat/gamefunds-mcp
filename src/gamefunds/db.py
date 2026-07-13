from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable


DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "gamefunds.db"


def connect(db_path: Path = DEFAULT_DB_PATH):
    """Return a sqlite3.Connection (created later)."""
    raise NotImplementedError()


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Create schema, FTS5, triggers. Idempotent."""
    raise NotImplementedError()


def upsert_entities(rows: Iterable[dict[str, Any]], db_path: Path = DEFAULT_DB_PATH) -> dict[str, int]:
    """
    Replace directory entities in a transaction (TRUNCATE + insert + rebuild FTS).
    Must NOT touch `pipeline` or `meta`.
    """
    raise NotImplementedError()


def ingest_cli(argv: list[str] | None = None) -> int:
    """CLI entrypoint: `python -m gamefunds.db ingest <path>`."""
    raise NotImplementedError()

