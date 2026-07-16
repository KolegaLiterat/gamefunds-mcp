from __future__ import annotations

"""
FastMCP server entrypoint (stdio today; HTTP later).

This module must remain a thin transport layer delegating to `gamefunds.core`.
"""

from fastmcp import FastMCP

import os
from typing import Literal

from . import core
from .auth import build_http_auth_verifier, require_http_token
from .db import ensure_db
from .paths import default_db_path
from .guides import register_guides
from .http_serve import run_http_server
from .instructions import SERVER_INSTRUCTIONS
from .middleware import LoggingMiddleware, ScopeMiddleware, TokenGuardMiddleware
from .sync import check_updates as _check_updates, sync_directory as _sync_directory

Transport = Literal["stdio", "http"]


def build_server(*, transport: Transport = "stdio") -> FastMCP:
    ensure_db(default_db_path())

    auth = build_http_auth_verifier() if transport == "http" else None
    server = FastMCP("GameFunds", auth=auth, instructions=SERVER_INSTRUCTIONS)

    max_tokens = int(os.getenv("GAMEFUNDS_MAX_TOOL_TOKENS", "2000"))
    server.add_middleware(ScopeMiddleware(enforce=transport == "http"))
    server.add_middleware(TokenGuardMiddleware(max_tokens=max_tokens))
    server.add_middleware(LoggingMiddleware())

    register_guides(server)

    @server.tool()
    def search_funding(query: str, limit: int = 15, offset: int = 0) -> dict:
        """Full-text search across 225+ game funding sources (name, country, game titles, notes).
        Use when you know a name or want a keyword ("roguelike", "Sweden", "Devolver",
        "turn-based", "free-to-play", "AAA/AA"). Hyphens, slashes, and parentheses are safe.
        Do NOT use this to match a project to funders — use `match_project` for that.
        Returns: `{total_matched: int, results: [EntityBrief]}`. Briefs omit full notes and links —
        call `get_entity(slug)` for those."""
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
        """Filter by structured fields. All arguments are optional.
        `section`: A-G (call `gamefunds_help("sections")` if you need section meanings).
        `budget_tier`: 1=$ (<200k), 2=$%$ (200k-2M), 3=$%$$ (>2M).
        `min_comm`: 1-3, how responsive they are to email. `has_email`: email contact only, no web forms.
        Returns: `{total_matched, results: [EntityBrief], filters_applied}`.
        If `total_matched` > 30, narrow filters instead of paging."""
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
        """Full record: all links, full notes, contact, submission path, reputation warnings,
        `terms_raw` (raw financial terms text from the catalog), plus your pipeline state for
        this entity if it exists. Call for 2-3 finalists only, not the whole candidate list.
        Returns: full `Entity` + `pipeline: PipelineState | null`."""
        return core.get_entity(slug)

    @server.tool()
    def match_project(
        genre: str,
        budget_usd: int,
        stage: str,
        country: str | None = None,
        platform: str | None = None,
        limit_per_type: int = 3,
    ) -> dict:
        """Match a project to funding sources. Deterministic scoring, NOT an LLM.
        `stage`: concept | prototype | vertical_slice | alpha | beta.
        `limit_per_type`: candidates per group (default 3, max 10).
        Returns candidates grouped by funding path — you rank and recommend from `reasons` and
        `confidence`; do not treat `score` as an oracle.
        Returns: `{by_type: {publisher|grant|vc_equity|project_investor: [{...EntityBrief, score, confidence, breakdown, reasons}]},
        hard_filtered, below_cutoff, genre_signal, genre_terms, genre_warning?, budget_signal, budget_warning?, note}`.
        `budget_usd` must be positive. The catalog has meaningful budget resolution around ~$5k-$5M
        (from amount_min/max data). Outside that scale, `budget_signal` is `above_range` or
        `below_range` (warning + all candidates confidence=low).
        `budget_signal`: in_range | above_range | below_range.
        `genre_signal`: good (>=1 genre term hit the catalog) | none (zero hits -> warning, all confidence=low).
        `genre_terms`: {matched, unmatched} — unmatched terms are informational, not a penalty.
        `confidence`: low | medium | high per candidate. `breakdown`: points (genre, budget, country, stage, comm).
        Grants (section G) are hard-filtered by country — a grant from another country usually does not apply."""
        return core.match_project(
            genre,
            budget_usd,
            stage,
            country=country,
            platform=platform,
            limit_per_type=limit_per_type,
        )

    @server.tool()
    def get_submission_brief(slug: str) -> dict:
        """Everything about HOW to submit to a specific entity: channel (email vs web form),
        address/link, `submit_links` (parsed submission URLs), stated criteria extracted from notes
        ("Only $2M+ games", "No sandbox games"), budget tier, responsiveness, warnings, portfolio
        titles (for tone matching). Call BEFORE writing a pitch or email.
        Returns: `{slug, name, channel, target, stated_criteria: [str], hard_filters: [str], warnings: [str],
        portfolio: [str], comm_rating, terms_raw, submit_links}`."""
        return core.get_submission_brief(slug)

    @server.tool()
    def get_pitch_rubric(target_slug: str | None = None, funding_type: str | None = None) -> dict:
        """Canonical pitch deck structure from PitchDeckTutorial.md: slides, what each must contain,
        weights. If you pass `target_slug`, the rubric adds that entity's criteria.
        `funding_type`: publisher | vc_equity | grant | project_investor — DIFFERENT rubrics; grants
        want different content than VCs. This is a working rubric, not text to paste. You write the deck.
        Returns: `{funding_type, slides: [{n, title, must_contain: [str], weight, common_mistakes: [str]}], target_specific: [str]}`."""
        return core.get_pitch_rubric(target_slug=target_slug, funding_type=funding_type)

    @server.tool()
    def review_pitch(
        deck_markdown: str,
        target_slug: str | None = None,
        funding_type: str | None = None,
        include_rubric: bool = False,
    ) -> dict:
        """Hard, deterministic deck check. Does NOT judge quality — that is your job.
        Checks: missing required slides, missing concrete numbers (budget, ask, recoup, timeline,
        team size), missing build/vertical-slice link, deck length, target hard-filter violations
        (e.g. sandbox game to Team17).
        `include_rubric`: if True, attaches the full rubric (`rubric`) — use when you need slide
        structure in the same call as the review.
        Returns: `{hard_findings: [{severity, slide, issue}], coverage: {slide_name: present|missing|thin},
        deck_stats: {slides, words}, rubric?}`.
        After the result: do qualitative assessment yourself from `rubric` and `coverage`.
        Input format: markdown/text. The server does not parse PDF or PPTX — extract text first."""
        return core.review_pitch(
            deck_markdown,
            target_slug=target_slug,
            funding_type=funding_type,
            include_rubric=include_rubric,
        )

    @server.tool()
    def set_status(
        slug: str,
        status: str,
        project: str | None = None,
        next_followup: str | None = None,
        note: str | None = None,
    ) -> dict:
        """`status`: not_contacted | contacted | in_talks | rejected | signed | passed
        (`passed` = you dropped out, `rejected` = they declined).
        Notes are APPENDED with a date, never overwritten. Returns: `PipelineState`."""
        return core.set_status(slug, status, project=project, next_followup=next_followup, note=note)

    @server.tool()
    def add_note(slug: str, note: str) -> dict:
        """Append a dated note to a pipeline entry without changing status.
        Use after a call, email, or meeting when status stays the same — use `set_status` to change status.
        Notes are appended with a date, never overwritten. Returns: `{slug, note_count}`."""
        return core.add_note(slug, note)

    @server.tool()
    def list_pipeline(status: str | None = None, stale_days: int | None = None) -> dict:
        """Your submission tracker. `stale_days=30` -> contacted/in_talks entries idle >30 days
        or with an overdue follow-up. Call at the start of a fundraising session.
        Returns: `{by_status: {status: count}, entries: [{slug, name, status, last_contact, next_followup, days_stale, last_note}]}`."""
        return core.list_pipeline(status=status, stale_days=stale_days)

    @server.tool()
    def check_updates() -> dict:
        """Cheap check whether the upstream repo changed (commit SHA only, no download).
        Returns: `{has_update, current_sha, last_synced_sha, last_synced_at, commit_message, commit_date}`."""
        return _check_updates()

    @server.tool()
    def sync_directory(dry_run: bool = True, full_diff: bool = False) -> dict:
        """Fetch and parse the catalog from GitHub. Your pipeline data is NOT touched.
        Default `dry_run=True` — shows what would change. Writing requires explicit `dry_run=False`.
        `full_diff=False` returns counts + first 10 changes (diff can be hundreds of rows).
        If the parser fails, NOTHING is saved and you get an error with a line number.
        If GitHub API rate limit is hit: `{rate_limited: true, hint: "...", applied: false}` — tell the
        user to set `GITHUB_TOKEN` (no scopes needed) and retry.
        Returns: `{has_update, added: int, removed: int, changed: int, sample: [...], applied: bool, parser_error?: str, rate_limited?: bool, hint?: str}`."""
        return _sync_directory(dry_run=dry_run, full_diff=full_diff)

    @server.tool()
    def gamefunds_help(topic: str) -> str:
        """Deep documentation when you need detail beyond tool descriptions.

        Questions about CONCEPTS (publishing vs project investment vs equity, recoup, dilution, waterfall,
        vertical slice, pitch deck structure) — do NOT answer from memory. Content lives in
        gamefunds://guide/* resources. Call `gamefunds_help("guides")` for the index, or directly:
        `funding-types`, `definitions`, `pitch-deck` (same content as the resource).

        `topic`: sections (A-G meanings) | scoring (match_project weights) | statuses |
        tiers (budget tiers and stars) | workflows (typical paths: zero to shortlist, pitch prep,
        pipeline review) | troubleshooting (parser failed, what next) |
        guides (guide index) | funding-types | definitions | pitch-deck.
        Unknown or empty topic returns the available topic list — never throws.
        Returns: markdown."""
        return core.gamefunds_help(topic)

    return server


def run_stdio_server() -> None:
    """Run the MCP server over stdio (no auth)."""
    server = build_server(transport="stdio")
    server.run()


def run_http_server_cli(*, host: str, port: int) -> None:
    """Run the MCP server over HTTP with token auth."""
    require_http_token()
    server = build_server(transport="http")
    run_http_server(server, host=host, port=port)


def main() -> None:
    run_stdio_server()


if __name__ == "__main__":
    main()
