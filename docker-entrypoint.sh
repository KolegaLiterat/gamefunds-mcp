#!/bin/sh
set -e

DATA_DIR="${GAMEFUNDS_DATA_DIR:-/app/data}"
DB_PATH="${GAMEFUNDS_DB_PATH:-$DATA_DIR/gamefunds.db}"

if [ ! -f "$DB_PATH" ]; then
  echo "No database at $DB_PATH — running initial sync..."
  gamefunds sync --apply
fi

exec gamefunds serve --transport http --host 0.0.0.0 --port 8080
