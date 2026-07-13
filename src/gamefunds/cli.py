from __future__ import annotations

import argparse
import json
from pathlib import Path

from .db import connect, init_db
from .sync import check_updates, sync_directory


def cmd_check() -> int:
    out = check_updates()
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


def cmd_sync(*, apply: bool, full_diff: bool) -> int:
    out = sync_directory(dry_run=not apply, full_diff=full_diff)
    print(json.dumps(out, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_stats() -> int:
    from .db import DEFAULT_DB_PATH

    init_db(DEFAULT_DB_PATH)
    with connect(DEFAULT_DB_PATH) as conn:
        entity_count = conn.execute(
            "SELECT value FROM meta WHERE key='entity_count';"
        ).fetchone()
        by_status = conn.execute(
            "SELECT status, COUNT(*) AS n FROM pipeline GROUP BY status ORDER BY status;"
        ).fetchall()

    print(f"DB: {DEFAULT_DB_PATH}")
    if entity_count:
        print(f"entities: {entity_count['value']}")
    else:
        print("entities: (none ingested yet)")
    if by_status:
        print("pipeline:")
        for r in by_status:
            print(f"  {r['status']}: {r['n']}")
    else:
        print("pipeline: (empty)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="gamefunds")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="Check upstream repo for updates")

    sync_p = sub.add_parser("sync", help="Sync directory from GitHub (dry-run by default)")
    sync_p.add_argument("--apply", action="store_true", help="Apply changes to local DB")
    sync_p.add_argument("--full-diff", action="store_true", help="Return full diff payload (large)")

    sub.add_parser("stats", help="Show DB stats")

    args = p.parse_args(argv)
    if args.cmd == "check":
        return cmd_check()
    if args.cmd == "sync":
        return cmd_sync(apply=bool(args.apply), full_diff=bool(args.full_diff))
    if args.cmd == "stats":
        return cmd_stats()
    raise SystemExit(2)


if __name__ == "__main__":
    raise SystemExit(main())

