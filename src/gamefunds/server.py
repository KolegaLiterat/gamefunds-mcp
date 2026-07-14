from __future__ import annotations

"""
FastMCP server entrypoint (stdio today; HTTP later).

This module must remain a thin transport layer delegating to `gamefunds.core`.
"""

from fastmcp import FastMCP

import os
from pathlib import Path

from . import core
from .db import DEFAULT_DB_PATH, ensure_db
from .guides import register_guides
from .middleware import LoggingMiddleware, TokenGuardMiddleware
from .sync import check_updates as _check_updates, sync_directory as _sync_directory


def build_server() -> FastMCP:
    ensure_db(Path(os.getenv("GAMEFUNDS_DB_PATH", str(DEFAULT_DB_PATH))))

    server = FastMCP("GameFunds")

    max_tokens = int(os.getenv("GAMEFUNDS_MAX_TOOL_TOKENS", "2000"))
    server.add_middleware(TokenGuardMiddleware(max_tokens=max_tokens))
    server.add_middleware(LoggingMiddleware(log_path="data/tool_calls.log"))

    register_guides(server)

    @server.tool()
    def search_funding(query: str, limit: int = 15, offset: int = 0) -> dict:
        """Full-text po katalogu 225+ źródeł finansowania gier (nazwa, kraj, tytuły gier, notatki).
        Używaj gdy znasz nazwę lub szukasz po słowie kluczowym ("roguelike", "Sweden", "Devolver",
        "turn-based", "free-to-play", "AAA/AA"). Myślniki, ukośniki i nawiasy są bezpieczne.
        NIE używaj do dopasowania projektu do funduszy — do tego jest `match_project`.
        Zwraca: `{total_matched: int, results: [EntityBrief]}`. Brief nie zawiera pełnych
        notatek ani linków — po nie zawołaj `get_entity(slug)`."""
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
        """Filtrowanie po polach strukturalnych. Wszystkie argumenty opcjonalne.
        `section`: A-G (zawołaj `gamefunds_help("sections")` jeśli nie wiesz co znaczą).
        `budget_tier`: 1=$ (<200k), 2=$%$ (200k-2M), 3=$%$$ (>2M).
        `min_comm`: 1-3, jak dobrze odpowiadają na maile. `has_email`: tylko z mailem, bez formularzy.
        Zwraca: `{total_matched, results: [EntityBrief], filters_applied}`.
        Jeśli `total_matched` > 30, zawęź filtry zamiast stronicować."""
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
        """Pełny rekord: wszystkie linki, pełne notatki, kontakt, ścieżka zgłoszenia,
        ostrzeżenia reputacyjne, `terms_raw` (surowy tekst warunków finansowych z katalogu),
        plus Twój stan pipeline dla tego podmiotu jeśli istnieje.
        Wołaj dla 2-3 finalistów, nie dla całej listy kandydatów.
        Zwraca: `Entity` (pełny) + `pipeline: PipelineState | null`."""
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
        """Dopasowuje projekt do źródeł finansowania. Deterministyczny scoring, NIE LLM.
        `stage`: concept | prototype | vertical_slice | alpha | beta.
        `limit_per_type`: ile kandydatów na grupę (domyślnie 3, max 10).
        Zwraca kandydatów pogrupowanych po ścieżce finansowania — rekomendację
        i uszeregowanie zrób sam na podstawie `reasons` i `confidence`, nie traktuj `score` jako wyroczni.
        Zwraca: `{by_type: {publisher|grant|vc_equity|project_investor: [{...EntityBrief, score, confidence, breakdown, reasons}]},
        hard_filtered, below_cutoff, genre_signal, genre_terms, genre_warning?, budget_signal, budget_warning?, note}`.
        `budget_usd` musi być dodatni. Katalog ma sensowną rozdzielczość budżetu w skali ~$5k–$5M (z danych amount_min/max).
        Poza tą skalą `budget_signal` to `above_range` lub `below_range` (warning + wszyscy confidence=low).
        `budget_signal`: in_range | above_range | below_range.
        `genre_signal`: good (≥1 termin gatunku trafił w katalog) | none (zero trafień → warning, wszyscy confidence=low).
        `genre_terms`: {matched, unmatched} — nietrafione terminy to informacja, nie kara.
        `confidence`: low | medium | high per kandydat. `breakdown`: rozbicie punktów (genre, budget, country, stage, comm).
        Granty (sekcja G) są filtrowane twardo po kraju — grant z innego kraju zwykle nie ma sensu."""
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
        """Wszystko o tym, JAK zgłosić się do konkretnego podmiotu: kanał (mail vs formularz),
        adres/link, `submit_links` (sparsowane linki zgłoszeniowe), ich deklarowane kryteria wyciągnięte z notatek ("Only $2M+ games",
        "No sandbox games"), tier budżetowy, responsywność, ostrzeżenia, tytuły z portfolio
        (do dopasowania tonu). Wołaj ZANIM zaczniesz pisać pitcha albo maila.
        Zwraca: `{slug, name, channel, target, stated_criteria: [str], hard_filters: [str], warnings: [str],
        portfolio: [str], comm_rating, terms_raw, submit_links}`."""
        return core.get_submission_brief(slug)

    @server.tool()
    def get_pitch_rubric(target_slug: str | None = None, funding_type: str | None = None) -> dict:
        """Kanoniczna struktura pitch decka z PitchDeckTutorial.md: slajdy, co ma być na każdym,
        wagi. Jeśli podasz `target_slug`, rubryka jest wzbogacona o kryteria tego podmiotu.
        `funding_type`: publisher | vc_equity | grant | project_investor — RÓŻNE rubryki, grant chce czego innego niż VC.
        To jest rubryka do pracy, nie tekst do wklejenia. Deck piszesz Ty, nie ten tool.
        Zwraca: `{funding_type, slides: [{n, title, must_contain: [str], weight, common_mistakes: [str]}], target_specific: [str]}`."""
        return core.get_pitch_rubric(target_slug=target_slug, funding_type=funding_type)

    @server.tool()
    def review_pitch(
        deck_markdown: str,
        target_slug: str | None = None,
        funding_type: str | None = None,
        include_rubric: bool = False,
    ) -> dict:
        """Twarde, deterministyczne sprawdzenie decka. NIE ocenia jakości — od tego jesteś Ty.
        Sprawdza: brakujące wymagane slajdy, brak konkretnych liczb (budżet, ask, recoup,
        timeline, wielkość zespołu), brak linku do buildu/vertical slice, długość decka,
        naruszenia twardych filtrów targetu (np. sandbox game do Team17).
        `include_rubric`: jeśli True, dołącza pełną rubrykę (`rubric`) do odpowiedzi — użyj gdy potrzebujesz
        struktury slajdów w tym samym wywołaniu co review.
        Zwraca: `{hard_findings: [{severity, slide, issue}], coverage: {slide_name: present|missing|thin},
        deck_stats: {slides, words}, rubric?}`.
        Po dostaniu wyniku: zrób ocenę jakościową sam, na podstawie `rubric` i `coverage`.
        Format wejścia: markdown/tekst. Serwer nie parsuje PDF ani PPTX — wyekstrahuj tekst przed wywołaniem."""
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
        (`passed` = Ty odpuściłeś, `rejected` = oni odmówili).
        Notatki są DOPISYWANE z datą, nie nadpisywane. Zwraca: `PipelineState`."""
        return core.set_status(slug, status, project=project, next_followup=next_followup, note=note)

    @server.tool()
    def add_note(slug: str, note: str) -> dict:
        """Dopisuje datowaną notatkę do wpisu pipeline bez zmiany statusu.
        Używaj po rozmowie, mailu lub spotkaniu, gdy nie zmieniasz statusu — do zmiany statusu jest `set_status`.
        Notatki są dopisywane z datą, nie nadpisywane. Zwraca: `{slug, note_count}`."""
        return core.add_note(slug, note)

    @server.tool()
    def list_pipeline(status: str | None = None, stale_days: int | None = None) -> dict:
        """Twój stan zgłoszeń. `stale_days=30` → wpisy w contacted/in_talks bez ruchu >30 dni
        lub z przeterminowanym follow-upem. Wołaj to na start sesji o fundraisingu.
        Zwraca: `{by_status: {status: count}, entries: [{slug, name, status, last_contact, next_followup, days_stale, last_note}]}`."""
        return core.list_pipeline(status=status, stale_days=stale_days)

    @server.tool()
    def check_updates() -> dict:
        """Tanie sprawdzenie czy repo źródłowe się zmieniło (tylko SHA commita, bez pobierania).
        Zwraca: `{has_update, current_sha, last_synced_sha, last_synced_at, commit_message, commit_date}`."""
        return _check_updates()

    @server.tool()
    def sync_directory(dry_run: bool = True, full_diff: bool = False) -> dict:
        """Pobiera i przeparsowuje katalog z GitHuba. Twoje dane pipeline NIE są ruszane.
        Domyślnie `dry_run=True` — pokazuje co by się zmieniło. Zapis wymaga jawnego `dry_run=False`.
        `full_diff=False` zwraca liczniki + 10 pierwszych zmian (diff może mieć setki wierszy).
        Jeśli parser padnie, NIC nie jest zapisywane i dostajesz błąd z numerem linii.
        Zwraca: `{has_update, added: int, removed: int, changed: int, sample: [...], applied: bool, parser_error?: str}`."""
        return _sync_directory(dry_run=dry_run, full_diff=full_diff)

    @server.tool()
    def gamefunds_help(topic: str) -> str:
        """Głęboka dokumentacja, wołaj gdy potrzebujesz szczegółów spoza opisów tools.

        Pytania o POJĘCIA (publishing vs project investment vs equity, recoup, dilution, waterfall,
        vertical slice, struktura pitch decka) — NIE odpowiadaj z pamięci. Treść jest w resources
        gamefunds://guide/*. Wołaj `gamefunds_help("guides")` po spis, albo bezpośrednio:
        `funding-types`, `definitions`, `pitch-deck` (treść jak w resource).

        `topic`: sections (co znaczy A-G) | scoring (wagi w match_project) | statuses |
        tiers (progi budżetowe i gwiazdki) | workflows (typowe ścieżki: od zera do shortlisty,
        przygotowanie pitcha, przegląd pipeline'u) | troubleshooting (parser padł, co dalej) |
        guides (spis poradników) | funding-types | definitions | pitch-deck.
        Nieznany lub pusty topic zwraca listę dostępnych tematów — nigdy nie rzuca wyjątku.
        Zwraca: markdown."""
        return core.gamefunds_help(topic)

    return server


def main() -> None:
    """Run the MCP server over stdio."""
    server = build_server()
    server.run()


if __name__ == "__main__":
    main()
