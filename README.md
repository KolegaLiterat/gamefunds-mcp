# GameFunds MCP

MCP server for browsing and managing a local copy of the GameFunds funding directory, plus a personal outreach pipeline.

## Goals

- Expose the GameFunds directory via MCP tools (search/filter/get full entity).
- Keep your personal fundraising pipeline in the same DB, but **never touched by sync**.
- Keep business logic in `core.py` (no `fastmcp` imports) so stdio today and HTTP + auth later is a transport swap.

## Status

Scaffolded project. Implementation is incremental and covered by tests.

