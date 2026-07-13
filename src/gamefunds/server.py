from __future__ import annotations

"""
FastMCP server entrypoint (stdio today; HTTP later).

This module must remain a thin transport layer delegating to `gamefunds.core`.
"""

from fastmcp import FastMCP

import os

from . import core
from .guides import register_guides
from .middleware import LoggingMiddleware, TokenGuardMiddleware
from .sync import check_updates as _check_updates, sync_directory as _sync_directory


def build_server() -> FastMCP:
    server = FastMCP("GameFunds")

    max_tokens = int(os.getenv("GAMEFUNDS_MAX_TOOL_TOKENS", "2000"))
    server.add_middleware(TokenGuardMiddleware(max_tokens=max_tokens))
    server.add_middleware(LoggingMiddleware(log_path="data/tool_calls.log"))

    register_guides(server)

    @server.tool()
    def search_funding(query: str, limit: int = 15, offset: int = 0) -> dict:
        return core.search_funding(query, limit=limit, offset=offset)

    @server.tool()
    def filter_funding(
        section: str | None = None,
        country: str | None = None,
        budget_tier: int | None = None,
        min_comm: int | None = None,
        has_email: bool | None = None,
        exclude_warnings: bool = False,
        limit: int = 15,
        offset: int = 0,
    ) -> dict:
        return core.filter_funding(
            section=section,
            country=country,
            budget_tier=budget_tier,
            min_comm=min_comm,
            has_email=has_email,
            exclude_warnings=exclude_warnings,
            limit=limit,
            offset=offset,
        )

    @server.tool()
    def get_entity(slug: str) -> dict:
        return core.get_entity(slug)

    @server.tool()
    def match_project(
        genre: str,
        budget_usd: int,
        stage: str,
        country: str | None = None,
        platform: str | None = None,
    ) -> dict:
        return core.match_project(genre, budget_usd, stage, country=country, platform=platform)

    @server.tool()
    def get_submission_brief(slug: str) -> dict:
        return core.get_submission_brief(slug)

    @server.tool()
    def get_pitch_rubric(target_slug: str | None = None, funding_type: str | None = None) -> dict:
        return core.get_pitch_rubric(target_slug=target_slug, funding_type=funding_type)

    @server.tool()
    def review_pitch(deck_markdown: str, target_slug: str | None = None, funding_type: str | None = None) -> dict:
        return core.review_pitch(deck_markdown, target_slug=target_slug, funding_type=funding_type)

    @server.tool()
    def set_status(
        slug: str,
        status: str,
        project: str | None = None,
        next_followup: str | None = None,
        note: str | None = None,
    ) -> dict:
        return core.set_status(slug, status, project=project, next_followup=next_followup, note=note)

    @server.tool()
    def add_note(slug: str, note: str) -> dict:
        return core.add_note(slug, note)

    @server.tool()
    def list_pipeline(status: str | None = None, stale_days: int | None = None) -> dict:
        return core.list_pipeline(status=status, stale_days=stale_days)

    @server.tool()
    def check_updates() -> dict:
        return _check_updates()

    @server.tool()
    def sync_directory(dry_run: bool = True, full_diff: bool = False) -> dict:
        return _sync_directory(dry_run=dry_run, full_diff=full_diff)

    @server.tool()
    def gamefunds_help(topic: str) -> str:
        return core.gamefunds_help(topic)

    return server


def main() -> None:
    """Run the MCP server over stdio."""
    server = build_server()
    server.run()


if __name__ == "__main__":
    main()

