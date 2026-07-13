from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .parser import parse_directory_markdown


DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "gamefunds.db"

ENTITY_COLUMNS = [
    "slug",
    "section",
    "section_name",
    "name",
    "country",
    "links_json",
    "pitch",
    "contact",
    "contact_email",
    "class_tier",
    "budget_tier",
    "comm_rating",
    "lifetime_rev_usd",
    "notable_titles",
    "notes",
    "has_warning",
    "raw_row",
]


def connect(db_path: Path = DEFAULT_DB_PATH):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def init_db(db_path: Path = DEFAULT_DB_PATH) -> None:
    """Create schema, FTS5, triggers. Idempotent."""
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS entities (
              slug TEXT PRIMARY KEY,
              section TEXT,
              section_name TEXT,
              name TEXT NOT NULL,
              country TEXT,
              links_json TEXT,
              pitch TEXT,
              contact TEXT,
              contact_email TEXT,
              class_tier TEXT,
              budget_tier INTEGER,
              comm_rating INTEGER,
              lifetime_rev_usd INTEGER,
              notable_titles TEXT,
              notes TEXT,
              has_warning BOOLEAN,
              raw_row TEXT
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(
              name, country, notable_titles, notes,
              content='entities', content_rowid='rowid'
            );

            CREATE TRIGGER IF NOT EXISTS entities_ai AFTER INSERT ON entities BEGIN
              INSERT INTO entities_fts(rowid, name, country, notable_titles, notes)
              VALUES (new.rowid, new.name, new.country, new.notable_titles, new.notes);
            END;

            CREATE TRIGGER IF NOT EXISTS entities_ad AFTER DELETE ON entities BEGIN
              INSERT INTO entities_fts(entities_fts, rowid, name, country, notable_titles, notes)
              VALUES('delete', old.rowid, old.name, old.country, old.notable_titles, old.notes);
            END;

            CREATE TRIGGER IF NOT EXISTS entities_au AFTER UPDATE ON entities BEGIN
              INSERT INTO entities_fts(entities_fts, rowid, name, country, notable_titles, notes)
              VALUES('delete', old.rowid, old.name, old.country, old.notable_titles, old.notes);
              INSERT INTO entities_fts(rowid, name, country, notable_titles, notes)
              VALUES (new.rowid, new.name, new.country, new.notable_titles, new.notes);
            END;

            CREATE TABLE IF NOT EXISTS pipeline (
              slug TEXT PRIMARY KEY,
              status TEXT DEFAULT 'not_contacted',
              project TEXT,
              last_contact_date TEXT,
              next_followup TEXT,
              notes TEXT,
              updated_at TEXT
            );

            CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
            """
        )


def upsert_entities(rows: Iterable[dict[str, Any]], db_path: Path = DEFAULT_DB_PATH) -> dict[str, int]:
    """
    Replace directory entities in a transaction (TRUNCATE + insert + rebuild FTS).
    Must NOT touch `pipeline` or `meta`.
    """
    init_db(db_path)
    rows = list(rows)
    counts: dict[str, int] = {}

    with connect(db_path) as conn:
        cur = conn.cursor()
        cur.execute("BEGIN;")
        cur.execute("DELETE FROM entities;")

        placeholders = ", ".join(["?"] * len(ENTITY_COLUMNS))
        cols_sql = ", ".join(ENTITY_COLUMNS)
        sql = f"INSERT INTO entities ({cols_sql}) VALUES ({placeholders});"

        for r in rows:
            vals = [r.get(c) for c in ENTITY_COLUMNS]
            cur.execute(sql, vals)
            sec = r.get("section") or "?"
            counts[sec] = counts.get(sec, 0) + 1

        # rebuild FTS from content table
        cur.execute("INSERT INTO entities_fts(entities_fts) VALUES('rebuild');")
        cur.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?);",
            ("entity_count", str(len(rows))),
        )
        cur.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?);",
            ("last_ingest_at", datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()

    return counts


def ingest_cli(argv: list[str] | None = None) -> int:
    """CLI entrypoint: `python -m gamefunds.db ingest <path>`."""
    parser = argparse.ArgumentParser(prog="python -m gamefunds.db")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ingest = sub.add_parser("ingest", help="Parse markdown and ingest into SQLite")
    ingest.add_argument("markdown_path", type=Path)
    ingest.add_argument("--db", dest="db_path", type=Path, default=DEFAULT_DB_PATH)

    args = parser.parse_args(argv)

    if args.cmd == "ingest":
        md = args.markdown_path.read_text(encoding="utf-8")
        rows = parse_directory_markdown(md)
        counts = upsert_entities(rows, db_path=args.db_path)
        total = sum(counts.values())
        print(f"Ingested {total} entities into {args.db_path}")
        for sec in sorted(counts.keys()):
            print(f"{sec}: {counts[sec]}")
        return 0

    raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit(ingest_cli())

