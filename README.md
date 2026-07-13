# GameFunds MCP

MCP server for browsing and managing a local copy of the GameFunds funding directory, plus a personal outreach pipeline.

## Goals

- Expose the GameFunds directory via MCP tools (search/filter/get full entity).
- Keep your personal fundraising pipeline in the same DB, but **never touched by sync**.
- Keep business logic in `core.py` (no `fastmcp` imports) so stdio today and HTTP + auth later is a transport swap.

## Status

Scaffolded project. Implementation is incremental and covered by tests.

## Dev setup

- Create venv and install deps:

```bash
cd gamefunds-mcp
python3 -m venv .venv
.venv/bin/python -m pip install -U pip
.venv/bin/python -m pip install -e ".[dev]"
```

- Run tests:

```bash
.venv/bin/python -m pytest
```

## CLI

- `gamefunds check` — check upstream repo SHA (cheap)
- `gamefunds sync` — dry-run sync (default)
- `gamefunds sync --apply` — apply sync to local DB
- `gamefunds stats` — basic DB stats

## Cursor MCP

This repo includes `[.cursor/mcp.json](.cursor/mcp.json)` configured to run the server via stdio:

- Command: `python -m gamefunds.server`

## Troubleshooting

- If `sync_directory` returns `parser_error`, the upstream markdown format drifted — update `src/gamefunds/parser.py` using the reported line number and `raw_row`.

