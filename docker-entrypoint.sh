#!/bin/sh
set -e

DATA_DIR="${GAMEFUNDS_DATA_DIR:-/app/data}"
DB_PATH="${GAMEFUNDS_DB_PATH:-$DATA_DIR/gamefunds.db}"

# A bind-mounted data dir arrives owned by the host user (often root), so the
# unprivileged app user cannot write the DB, synced guides, or logs. Fix that
# here, while still root, then drop privileges for everything that follows.
if [ "$(id -u)" = "0" ]; then
  mkdir -p "$DATA_DIR"
  chown -R gamefunds:gamefunds "$DATA_DIR"
  exec gosu gamefunds "$0" "$@"
fi

if [ ! -f "$DB_PATH" ]; then
  echo "No database at $DB_PATH — running initial sync..."
  gamefunds sync --apply
fi

exec gamefunds serve --transport http --host 0.0.0.0 --port 8080
