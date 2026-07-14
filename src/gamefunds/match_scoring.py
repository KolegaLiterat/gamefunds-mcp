from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from .rubric_parser import infer_funding_type

_GENRE_STOPWORDS = frozenset(
    {
        "with",
        "the",
        "and",
        "of",
        "for",
        "in",
        "on",
        "at",
        "to",
        "from",
        "by",
        "an",
        "or",
        "is",
        "it",
        "as",
        "be",
        "are",
        "was",
        "were",
        "that",
        "this",
        "but",
        "not",
        "you",
        "all",
        "any",
        "can",
        "has",
        "had",
        "have",
        "will",
        "your",
        "our",
        "their",
        "its",
        "into",
        "over",
        "out",
        "off",
        "via",
    }
)

_GENRE_DESCRIPTOR_STOPWORDS = frozenset(
    {
        "art",
        "game",
        "games",
        "sim",
        "video",
        "indie",
        "title",
        "titles",
        "style",
        "like",
        "based",
        "new",
        "fun",
        "play",
        "player",
        "world",
        "story",
        "mode",
        "content",
        "mature",
        "gaming",
        "studio",
        "studios",
        "developer",
        "developers",
        "publisher",
        "project",
        "steam",
    }
)

_CORPUS_FREQ_THRESHOLD = 0.20

_COUNTRY_WEIGHT: dict[str, int] = {
    "grant": 28,
    "publisher": 5,
    "vc_equity": 10,
    "project_investor": 5,
}

_GENRE_CAP: dict[str, int] = {
    "publisher": 30,
    "grant": 20,
    "vc_equity": 20,
    "project_investor": 20,
}

_EXCLUDE_PATTERNS = [
    re.compile(r"\bstrictly\s+no\s+([^.;—]+)", re.I),
    re.compile(r"\bno\s+([^.;—]+?)\s+games?\b", re.I),
    re.compile(r"\bnot\s+taking\s+([^.;—]+)", re.I),
]

_REQUIRE_PATTERNS = [
    re.compile(r"\bonly\s+([^.;—]+)", re.I),
    re.compile(r"\b([^.;—]+?)\s+only\b", re.I),
    re.compile(r"\bwe publish\s+([^.;]+)", re.I),
    re.compile(r"\bstrictly\s+(?!no\b)([^.;—]+)", re.I),
    re.compile(r"\b([^.;—]+?)\s+focus\b", re.I),
]

_HARD_FILTER_PATTERNS = [
    re.compile(r"^only\s", re.I),
    re.compile(r"^strictly\s+no\s", re.I),
    re.compile(r"^strictly\s+(?!no\b)", re.I),
    re.compile(r"\bno\s+sandbox\b", re.I),
    re.compile(r"\bwe publish\b", re.I),
    re.compile(r"\bfocus\b", re.I),
    re.compile(r"\bminimum\b", re.I),
    re.compile(r"\bcurrently signing\b", re.I),
    re.compile(r"\bat capacity\b", re.I),
    re.compile(r"\bnot taking\b", re.I),
]

_REQUIRE_TERM_STOPWORDS = frozenset({"games", "game", "suited", "switch", "publish", "label"})


@dataclass(frozen=True)
class PreparedGenreQuery:
    phrases: list[str]
    tokens: list[str]
    primary_tokens: list[str]
    significant_terms: list[str]
    noise_tokens: frozenset[str]


@dataclass(frozen=True)
class CatalogBudgetScale:
    floor_usd: int
    ceiling_usd: int
    reference_high_usd: int


def _row_int(row: Any, key: str) -> int | None:
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return None
    if value is None:
        return None
    return int(value)


def compute_catalog_budget_scale(rows: list[Any]) -> CatalogBudgetScale:
    """Derive catalog budget bounds from parsed amount_min/max_usd (outlier-resistant)."""
    max_vals = [
        value
        for r in rows
        if (value := _row_int(r, "amount_max_usd")) is not None and value > 0
    ]
    min_vals = [
        value
        for r in rows
        if (value := _row_int(r, "amount_min_usd")) is not None and value > 0
    ]

    floor_candidates = [v for v in min_vals if v >= 1_000]
    floor_usd = min(floor_candidates) if floor_candidates else 5_000

    if not max_vals:
        return CatalogBudgetScale(
            floor_usd=floor_usd,
            ceiling_usd=5_000_000,
            reference_high_usd=5_000_000,
        )

    sorted_max = sorted(max_vals)
    median = sorted_max[len(sorted_max) // 2]
    outlier_cap = max(median * 5, 5_000_000)
    sensible = [v for v in sorted_max if v <= outlier_cap]
    if not sensible:
        sensible = sorted_max[: max(1, len(sorted_max) // 2)]

    reference_high_usd = max(sensible)
    return CatalogBudgetScale(
        floor_usd=floor_usd,
        ceiling_usd=reference_high_usd,
        reference_high_usd=reference_high_usd,
    )


def format_budget_warning(budget_usd: int, signal: str, scale: CatalogBudgetScale) -> str:
    return (
        f"Budżet ${budget_usd:,} wykracza poza skalę tego katalogu. "
        f"Największe wpisy operują w okolicach ${scale.reference_high_usd:,}. "
        f"Wyniki uszeregowane bez wiarygodnego dopasowania budżetu."
    )


def assess_budget_scale(budget_usd: int, scale: CatalogBudgetScale) -> tuple[str, str | None]:
    if budget_usd < scale.floor_usd:
        return "below_range", format_budget_warning(budget_usd, "below_range", scale)
    if budget_usd > scale.ceiling_usd:
        return "above_range", format_budget_warning(budget_usd, "above_range", scale)
    return "in_range", None


def parse_genre_query(genre: str) -> tuple[list[str], list[str], list[str], list[str]]:
    """Return (phrases, tokens, primary_tokens, segments)."""
    segments = [s.strip() for s in genre.replace("-", " ").split(",") if s.strip()]
    if not segments:
        segments = [genre.strip()]

    phrases: list[str] = []
    tokens: list[str] = []

    for segment in segments:
        normalized = re.sub(r"\s+", " ", segment.lower()).strip()
        if not normalized:
            continue
        raw_tokens = [t for t in normalized.split() if len(t) >= 3 and t not in _GENRE_STOPWORDS]
        seg_tokens = [t for t in raw_tokens if t not in _GENRE_DESCRIPTOR_STOPWORDS]
        tokens.extend(seg_tokens)

        if len(raw_tokens) >= 2:
            phrases.append(normalized)
        for i in range(len(raw_tokens) - 1):
            pair = f"{raw_tokens[i]} {raw_tokens[i + 1]}"
            if pair.split()[0] in _GENRE_DESCRIPTOR_STOPWORDS or pair.split()[-1] in _GENRE_DESCRIPTOR_STOPWORDS:
                if not all(w in _GENRE_DESCRIPTOR_STOPWORDS for w in pair.split()):
                    phrases.append(pair)
            else:
                phrases.append(pair)
        for i in range(len(raw_tokens) - 2):
            phrases.append(f"{raw_tokens[i]} {raw_tokens[i + 1]} {raw_tokens[i + 2]}")

    phrases = sorted(set(phrases), key=len, reverse=True)
    tokens = list(dict.fromkeys(tokens))
    primary_tokens = list(
        dict.fromkeys(
            [
                t
                for t in tokens
                if t
                in {
                    tok
                    for seg in [segments[0]]
                    for tok in [
                        w
                        for w in re.sub(r"\s+", " ", seg.lower()).split()
                        if len(w) >= 3 and w not in _GENRE_STOPWORDS and w not in _GENRE_DESCRIPTOR_STOPWORDS
                    ]
                }
            ]
        )
    )
    return phrases, tokens, primary_tokens, segments


def corpus_token_doc_frequency(rows: list[Any]) -> dict[str, float]:
    n = len(rows) or 1
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        hay = entity_genre_haystack(row)
        for token in set(re.findall(r"\b[a-z0-9]{3,}\b", hay)):
            counts[token] += 1
    return {token: count / n for token, count in counts.items()}


def build_noise_vocab(rows: list[Any]) -> frozenset[str]:
    freq = corpus_token_doc_frequency(rows)
    high_freq = {token for token, share in freq.items() if share > _CORPUS_FREQ_THRESHOLD}
    return frozenset(_GENRE_DESCRIPTOR_STOPWORDS | _GENRE_STOPWORDS | high_freq)


def _meaningful_words(text: str, noise: frozenset[str]) -> list[str]:
    words = [
        w
        for w in re.sub(r"\s+", " ", text.lower()).split()
        if len(w) >= 3 and w not in _GENRE_STOPWORDS and w not in noise
    ]
    return list(dict.fromkeys(words))


def prepare_genre_query(genre: str, rows: list[Any]) -> PreparedGenreQuery:
    phrases, tokens, primary_tokens, segments = parse_genre_query(genre)
    noise = build_noise_vocab(rows)

    scoring_tokens = [t for t in tokens if t not in noise]
    scoring_phrases = [p for p in phrases if all(w not in noise for w in p.split())]

    significant_terms: list[str] = []
    seen: set[str] = set()

    def _add_term(term: str) -> None:
        if term and term not in seen:
            seen.add(term)
            significant_terms.append(term)

    for segment in segments:
        words = _meaningful_words(segment, noise)
        for word in words:
            _add_term(word)
        if len(words) >= 2:
            _add_term(" ".join(words))

    return PreparedGenreQuery(
        phrases=scoring_phrases,
        tokens=scoring_tokens,
        primary_tokens=[t for t in primary_tokens if t not in noise],
        significant_terms=significant_terms,
        noise_tokens=noise,
    )


def entity_exclusivity_text(row: dict[str, Any] | Any) -> str:
    parts = [
        row["notes"] if row["notes"] else "",
        row["eligibility"] if row["eligibility"] else "",
        row["backing"] if row["backing"] else "",
        row["funding_terms"] if row["funding_terms"] else "",
        row["target_scope"] if row["target_scope"] else "",
        row["class_tier"] if row["class_tier"] else "",
    ]
    return " ".join(parts).lower()


_SPECIALTY_LINE_PATTERNS = [
    re.compile(r"\bonly\b", re.I),
    re.compile(r"specialist", re.I),
    re.compile(r"\b\w[\w-]*\s+games\b", re.I),
    re.compile(r"\bstrictly\b", re.I),
    re.compile(r"\bfocus\b", re.I),
]


def entity_specialty_text(row: dict[str, Any] | Any) -> str:
    for field in ("backing", "notes", "funding_terms", "eligibility"):
        text = (row[field] if row[field] else "").strip()
        if text and any(p.search(text) for p in _SPECIALTY_LINE_PATTERNS):
            return text
    return (row["backing"] if row["backing"] else "") or ""


def entity_genre_haystack(row: dict[str, Any] | Any) -> str:
    parts = [
        row["notes"] if row["notes"] else "",
        row["notable_titles"] if row["notable_titles"] else "",
        row["eligibility"] if row["eligibility"] else "",
        row["backing"] if row["backing"] else "",
        row["funding_terms"] if row["funding_terms"] else "",
        row["target_scope"] if row["target_scope"] else "",
        row["class_tier"] if row["class_tier"] else "",
    ]
    return " ".join(parts).lower()


def corpus_text(rows: list[Any]) -> str:
    return " ".join(entity_genre_haystack(r) for r in rows).lower()


def _term_in_text(term: str, text: str) -> bool:
    if re.search(rf"\b{re.escape(term)}\b", text):
        return True
    return bool(re.search(rf"\b{re.escape(term)}[/]", text))


def _term_in_corpus(term: str, corpus: str) -> bool:
    if " " in term:
        return term in corpus
    return _term_in_text(term, corpus)


def format_genre_warning(unmatched: list[str]) -> str:
    terms = ", ".join(unmatched) if unmatched else "podane cechy"
    return (
        f"Katalog nie zawiera wpisów pasujących do: {terms}. "
        f"Wyniki uszeregowane bez uwzględnienia tych cech — zweryfikuj ręcznie."
    )


def assess_genre_coverage(
    significant_terms: list[str],
    corpus: str,
) -> tuple[str, dict[str, list[str]], str | None]:
    if not significant_terms:
        return "none", {"matched": [], "unmatched": []}, format_genre_warning([])

    matched = [term for term in significant_terms if _term_in_corpus(term, corpus)]
    unmatched = [term for term in significant_terms if term not in matched]

    if matched:
        signal = "good"
        warning = None
    else:
        signal = "none"
        warning = format_genre_warning(significant_terms)

    return signal, {"matched": matched, "unmatched": unmatched}, warning


def score_genre_match(
    phrases: list[str],
    tokens: list[str],
    hay: str,
    *,
    funding_type: str,
    specialty_text: str = "",
    primary_tokens: list[str] | None = None,
    noise_tokens: frozenset[str] | None = None,
) -> tuple[int, list[str], int]:
    cap = _GENRE_CAP.get(funding_type, 25)
    primary_tokens = primary_tokens or []
    noise = noise_tokens or frozenset()
    score = 0
    reasons: list[str] = []
    matched_phrases: list[str] = []
    matched_tokens: list[str] = []
    primary_genre_rank = 0

    for phrase in phrases:
        if phrase in hay:
            matched_phrases.append(phrase)

    for token in tokens:
        if token in noise:
            continue
        if _term_in_text(token, hay):
            matched_tokens.append(token)

    specialty = specialty_text.lower()
    specialty_hits = [t for t in matched_tokens if specialty and _term_in_text(t, specialty)]

    if matched_phrases:
        score += 25
        reasons.append(f"genre phrase: {', '.join(sorted(set(matched_phrases)))}")
    elif matched_tokens:
        score += min(cap, 10 + 8 * len(set(matched_tokens)))
        clean = [t for t in sorted(set(matched_tokens)) if t not in noise]
        if clean:
            reasons.append(f"genre keywords: {', '.join(clean)}")

    if specialty_hits and set(matched_tokens) & set(specialty_hits):
        score = cap
        if not any("genre specialty" in r for r in reasons):
            reasons.append(f"genre specialty: {', '.join(sorted(set(specialty_hits)))}")
        if any(t in specialty_hits for t in primary_tokens):
            score += 10
            primary_genre_rank = 1
            reasons.append("primary genre specialty match")
    elif specialty_hits:
        score += min(12, 6 * len(set(specialty_hits)))
        reasons.append(f"genre specialty: {', '.join(sorted(set(specialty_hits)))}")

    return min(score, cap + 10), reasons, primary_genre_rank


def _terms_from_clause(clause: str) -> list[str]:
    clause = clause.lower()
    clause = re.sub(r"[^\w\s/&-]", " ", clause)
    parts = re.split(r"[/&,]|\band\b|\bor\b", clause)
    terms: list[str] = []
    for part in parts:
        for word in part.split():
            word = word.strip("-")
            if len(word) >= 3 and word not in _GENRE_STOPWORDS and word not in _REQUIRE_TERM_STOPWORDS:
                terms.append(word)
    return terms


def _extract_exclusivity_rules(text: str) -> tuple[list[str], list[str]]:
    excludes: list[str] = []
    requires: list[str] = []
    for rx in _EXCLUDE_PATTERNS:
        for m in rx.finditer(text):
            excludes.extend(_terms_from_clause(m.group(1)))
    for rx in _REQUIRE_PATTERNS:
        for m in rx.finditer(text):
            clause = m.group(1).strip()
            if clause.lower().startswith("no "):
                continue
            requires.extend(_terms_from_clause(clause))
    return sorted(set(excludes)), sorted(set(requires))


def violates_genre_exclusivity(project_phrases: list[str], project_tokens: list[str], row: Any) -> bool:
    hay = entity_exclusivity_text(row)
    excludes, requires = _extract_exclusivity_rules(hay)
    project_blob = " ".join(project_phrases + project_tokens)

    if any(term in project_blob for term in excludes):
        return True

    if requires and not any(term in project_blob for term in requires):
        return True

    return False


def split_submission_parts(text: str, *, eligibility: str = "") -> list[str]:
    parts = [p.strip() for p in re.split(r"[.;]\s+|\n+", text) if p.strip()]
    if eligibility:
        parts.insert(0, eligibility.strip())
    return parts


def extract_hard_filters_from_text(text: str, *, eligibility: str = "") -> list[str]:
    hard_filters: list[str] = []
    for p in split_submission_parts(text, eligibility=eligibility):
        if any(rx.search(p) for rx in _HARD_FILTER_PATTERNS):
            hard_filters.append(p)
    return hard_filters


def country_match_score(
    *,
    funding_type: str,
    section: str,
    country_norm: str | None,
    ent_country: str | None,
    has_budget_data: bool,
) -> int:
    if not country_norm or not ent_country or ent_country != country_norm:
        return 0
    if section == "G":
        return 28 if has_budget_data else 15
    return _COUNTRY_WEIGHT.get(funding_type, 5)


def candidate_confidence(
    *,
    genre_signal: str,
    budget_signal: str = "in_range",
    budget_confirmed: bool,
    stage_fit: bool,
    genre_score: int,
    has_budget_data: bool,
    budget_score: int,
    country_score: int,
    funding_type: str = "publisher",
) -> str:
    if genre_signal == "none":
        return "low"
    if budget_signal != "in_range":
        return "low"
    if not has_budget_data and funding_type == "grant":
        return "low"
    if stage_fit and genre_score >= 25:
        return "high"
    if budget_confirmed and stage_fit and genre_score >= 15:
        return "high"
    if genre_score >= 15 or budget_confirmed or country_score > 0:
        return "medium"
    if not has_budget_data:
        return "low"
    return "medium"
