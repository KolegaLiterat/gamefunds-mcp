from __future__ import annotations

import re

_QUOTED_PHRASE = re.compile(r'"((?:[^"]|"")*)"')
_TOKEN = re.compile(r"[A-Za-z0-9]+\*|[A-Za-z0-9]+(?:[-/&][A-Za-z0-9]+)*")
_PREFIX_TOKEN = re.compile(r"^[A-Za-z0-9]+\*$")


def parse_search_terms(query: str) -> list[str]:
    """Extract searchable terms from a raw user query string."""
    terms: list[str] = []
    last = 0

    for match in _QUOTED_PHRASE.finditer(query):
        if match.start() > last:
            terms.extend(_tokens_from_chunk(query[last : match.start()]))
        phrase = match.group(1).replace('""', '"').strip()
        if phrase:
            terms.append(phrase)
        last = match.end()

    if last < len(query):
        terms.extend(_tokens_from_chunk(query[last:]))

    return _dedupe_terms(terms)


def _tokens_from_chunk(chunk: str) -> list[str]:
    return [token for token in (_normalize_token(m.group(0)) for m in _TOKEN.finditer(chunk)) if token]


def _normalize_token(token: str) -> str | None:
    if _PREFIX_TOKEN.match(token):
        return token
    normalized = re.sub(r"[-/&]+", " ", token)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized or None


def _dedupe_terms(terms: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        key = term.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(term)
    return out


def _fts_term(term: str) -> str:
    if _PREFIX_TOKEN.match(term):
        return term
    escaped = term.replace('"', '""')
    return f'"{escaped}"'


def is_fts_query_error(exc: BaseException) -> bool:
    """True for FTS5 syntax/query errors that should return empty search results."""
    if not isinstance(exc, Exception):
        return False
    msg = str(exc).lower()
    return (
        "fts5: syntax error" in msg
        or "no such column" in msg
        or "malformed match expression" in msg
        or "unknown special query" in msg
    )


def build_fts5_match_query(query: str) -> str | None:
    """
    Build a safe FTS5 MATCH expression from arbitrary user input.

    Terms are combined with OR so multi-part queries return the union of matches,
    ranked by bm25(entities_fts) in search_funding.
    """
    terms = parse_search_terms(query)
    if not terms:
        return None
    return " OR ".join(_fts_term(term) for term in terms)
