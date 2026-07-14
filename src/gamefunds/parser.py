from __future__ import annotations

import json
import re
from typing import Any, Callable

# Approximate FX for parsing only — documented in gamefunds_help("tiers").
GBP_TO_USD = 1.27
EUR_TO_USD = 1.09

HEADER_SCHEMAS: dict[tuple[str, ...], str] = {
    (
        "Name",
        "Country",
        "Links",
        "Pitch / Submit",
        "Contact",
        "Class / Budget / Comm.",
        "Lifetime Rev",
        "Notable Titles",
        "Notes",
    ): "publisher_ab",
    (
        "Name",
        "Country",
        "Links",
        "Pitch / Submit",
        "Target",
        "Notable Titles",
        "Notes",
    ): "publisher_b_compact",
    (
        "Name",
        "Country",
        "Links",
        "Pitch / Submit",
        "Backing / Angle",
        "Notable Titles",
        "Notes",
    ): "publisher_c",
    (
        "Name",
        "Country",
        "Links",
        "How to Approach",
        "Funding Type",
        "Target",
        "Notable / Program",
        "Notes",
    ): "platform_d",
    (
        "Name",
        "Country",
        "Links",
        "Stage / Check",
        "Contact",
        "Portfolio Highlights",
        "Notes",
    ): "vc_equity",
    (
        "Name",
        "Country",
        "Links",
        "Funding & Terms",
        "How to Apply",
        "Portfolio",
        "Notes",
    ): "project_fund",
    (
        "Program",
        "Region",
        "Links",
        "Amount",
        "Eligibility / Focus",
        "Notes",
    ): "grant_g",
}


class ParseError(ValueError):
    def __init__(
        self,
        message: str,
        *,
        line_no: int | None = None,
        raw_line: str | None = None,
        section: str | None = None,
    ):
        super().__init__(message)
        self.line_no = line_no
        self.raw_line = raw_line
        self.section = section


_EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*(.*?)\*\*")
_EMPTY = {"—", "-", ""}


def slugify(name: str) -> str:
    name = strip_md(name)
    name = name.lower()
    name = name.replace("&", " and ")
    name = re.sub(r"[^\w\s-]", "", name, flags=re.UNICODE)
    name = re.sub(r"[\s_]+", "-", name).strip("-")
    name = re.sub(r"-{2,}", "-", name)
    if not name:
        raise ParseError("Could not slugify empty name")
    return name


def strip_md(text: str) -> str:
    text = text.strip()
    text = _BOLD_RE.sub(r"\1", text)
    text = text.replace("`", "")
    text = _MD_LINK_RE.sub(r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def parse_links(cell: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for label, url in _MD_LINK_RE.findall(cell):
        links.append({"label": strip_md(label), "url": url.strip()})
    return links


def parse_comm_rating(text: str) -> int | None:
    if "?" in text:
        return None
    stars = text.count("★")
    if stars in (1, 2, 3):
        return stars
    return None


def parse_budget_tier_symbols(text: str) -> int | None:
    if "$$$" in text:
        return 3
    if "$$" in text:
        return 2
    if "$" in text:
        return 1
    return None


def budget_tier_from_amount_max(amount_max_usd: int | None) -> int | None:
    if amount_max_usd is None:
        return None
    if amount_max_usd < 200_000:
        return 1
    if amount_max_usd <= 2_000_000:
        return 2
    return 3


def _money_to_usd(num: float, suffix: str | None, currency: str | None) -> int:
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get((suffix or "").upper(), 1)
    cur = (currency or "$").strip()
    rate = 1.0
    if cur in {"£", "GBP"}:
        rate = GBP_TO_USD
    elif cur in {"€", "EUR"}:
        rate = EUR_TO_USD
    return int(num * mult * rate)


def text_has_money_signal(text: str) -> bool:
    return bool(re.search(r"[$£€]|\d+(?:\.\d+)?\s*[KMBkmb]\b", text, re.I))


def parse_amount_fields(*texts: str) -> tuple[str | None, int | None, int | None]:
    parts = [t for t in texts if t and strip_md(t) not in _EMPTY]
    if not parts:
        return None, None, None
    combined = " · ".join(parts)
    raw = strip_md(combined)
    values: list[int] = []

    range_re = re.compile(
        r"(?P<c1>[$£€])?\s*(?P<n1>\d+(?:\.\d+)?)\s*(?P<s1>[KMBkmb])?"
        r"\s*(?:[–\-—]|~|to)\s*"
        r"(?P<c2>[$£€])?\s*(?P<n2>\d+(?:\.\d+)?)\s*(?P<s2>[KMBkmb])?",
        re.I,
    )
    consumed_spans: list[tuple[int, int]] = []
    for m in range_re.finditer(combined):
        consumed_spans.append(m.span())
        c1 = m.group("c1") or m.group("c2") or "$"
        c2 = m.group("c2") or c1
        values.append(_money_to_usd(float(m.group("n1")), m.group("s1"), c1))
        values.append(_money_to_usd(float(m.group("n2")), m.group("s2"), c2))

    upto_re = re.compile(
        r"up to\s+(?P<c>[$£€])?\s*(?P<n>\d+(?:\.\d+)?)\s*(?P<s>[KMBkmb])?",
        re.I,
    )
    for m in upto_re.finditer(combined):
        if any(a <= m.start() < b for a, b in consumed_spans):
            continue
        values.append(_money_to_usd(float(m.group("n")), m.group("s"), m.group("c")))

    if not values:
        single_re = re.compile(r"(?P<c>[$£€])\s*(?P<n>\d+(?:\.\d+)?)\s*(?P<s>[KMBkmb])?", re.I)
        for m in single_re.finditer(combined):
            values.append(_money_to_usd(float(m.group("n")), m.group("s"), m.group("c")))

    if not values:
        return raw if text_has_money_signal(combined) else None, None, None
    return raw, min(values), max(values)


def parse_monetary_fields(*texts: str) -> tuple[str | None, int | None, int | None]:
    parts = [t for t in texts if t and strip_md(t) not in _EMPTY]
    if not parts:
        return None, None, None
    combined = " · ".join(parts)
    if not text_has_money_signal(combined):
        return None, None, None
    return parse_amount_fields(*texts)


def resolve_budget_tier(
    *,
    amount_max_usd: int | None,
    symbol_text: str | None = None,
) -> int | None:
    if amount_max_usd is not None:
        return budget_tier_from_amount_max(amount_max_usd)
    if symbol_text:
        return parse_budget_tier_symbols(symbol_text)
    return None


def parse_lifetime_rev_usd(text: str) -> int | None:
    t = strip_md(text)
    if t in _EMPTY or "[?]" in t:
        return None
    m = re.search(r"\$([0-9]+(?:\.[0-9]+)?)\s*([BMK])\b", t)
    if not m:
        return None
    num = float(m.group(1))
    mult = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[m.group(2)]
    return int(num * mult)


def extract_email(*cells: str) -> str | None:
    for c in cells:
        m = _EMAIL_RE.search(c)
        if m:
            return m.group(0)
    return None


def _split_md_row(line: str, *, line_no: int) -> list[str]:
    line = line.strip()
    if not (line.startswith("|") and line.endswith("|")):
        raise ParseError("Not a markdown table row", line_no=line_no, raw_line=line)
    return [p.strip() for p in line.strip("|").split("|")]


def _normalize_headers(headers: list[str]) -> tuple[str, ...]:
    return tuple(h.strip() for h in headers)


def _resolve_schema(headers: list[str], *, section: str, line_no: int, raw_row: str) -> str:
    key = _normalize_headers(headers)
    schema = HEADER_SCHEMAS.get(key)
    if not schema:
        raise ParseError(
            f"Unknown table schema in section {section}: {list(headers)}",
            line_no=line_no,
            raw_line=raw_row,
            section=section,
        )
    return schema


def _cell(row_map: dict[str, str], key: str) -> str:
    return row_map.get(key, "")


def _opt_text(value: str) -> str | None:
    t = strip_md(value)
    return None if t in _EMPTY else t


def _submit_links_json(*cells: str) -> str | None:
    seen: set[str] = set()
    links: list[dict[str, str]] = []
    for cell in cells:
        if not cell:
            continue
        for link in parse_links(cell):
            url = (link.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            links.append({"label": link.get("label") or url, "url": url})
    return json.dumps(links, ensure_ascii=False) if links else None


def _terms_raw(cell: str) -> str | None:
    """Verbatim terms/budget column text; never dropped when non-monetary."""
    return _opt_text(cell)


def _base_entity(
    *,
    section: str,
    section_name: str,
    name: str,
    country: str | None,
    links_json: str | None,
    pitch: str | None,
    contact: str | None,
    raw_row: str,
    **extra: Any,
) -> dict[str, Any]:
    contact_email = extract_email(pitch or "", contact or "", raw_row)
    return {
        "slug": slugify(name),
        "section": section,
        "section_name": section_name,
        "name": name,
        "country": country,
        "links_json": links_json,
        "pitch": pitch,
        "contact": contact,
        "contact_email": contact_email,
        "class_tier": extra.get("class_tier"),
        "budget_tier": extra.get("budget_tier"),
        "comm_rating": extra.get("comm_rating"),
        "lifetime_rev_usd": extra.get("lifetime_rev_usd"),
        "notable_titles": extra.get("notable_titles"),
        "notes": extra.get("notes"),
        "eligibility": extra.get("eligibility"),
        "backing": extra.get("backing"),
        "funding_terms": extra.get("funding_terms"),
        "target_scope": extra.get("target_scope"),
        "terms_raw": extra.get("terms_raw"),
        "submit_links_json": extra.get("submit_links_json"),
        "amount_raw": extra.get("amount_raw"),
        "amount_min_usd": extra.get("amount_min_usd"),
        "amount_max_usd": extra.get("amount_max_usd"),
        "has_warning": bool("⚠️" in raw_row),
        "raw_row": raw_row,
    }


def _parse_publisher_ab(row_map: dict[str, str], *, section: str, section_name: str, raw_row: str) -> dict[str, Any]:
    cbc = _cell(row_map, "Class / Budget / Comm.")
    pitch_cell = _cell(row_map, "Pitch / Submit")
    contact_cell = _cell(row_map, "Contact")
    amount_raw, amount_min, amount_max = parse_monetary_fields(cbc)
    return _base_entity(
        section=section,
        section_name=section_name,
        name=strip_md(_cell(row_map, "Name")),
        country=_opt_text(_cell(row_map, "Country")),
        links_json=json.dumps(parse_links(_cell(row_map, "Links")), ensure_ascii=False) or None,
        pitch=_opt_text(pitch_cell),
        contact=_opt_text(contact_cell),
        raw_row=raw_row,
        class_tier=parse_class_tier(cbc),
        budget_tier=resolve_budget_tier(amount_max_usd=amount_max, symbol_text=cbc),
        comm_rating=parse_comm_rating(cbc),
        lifetime_rev_usd=parse_lifetime_rev_usd(_cell(row_map, "Lifetime Rev")),
        notable_titles=_opt_text(_cell(row_map, "Notable Titles")),
        notes=_opt_text(_cell(row_map, "Notes")),
        terms_raw=_terms_raw(cbc),
        submit_links_json=_submit_links_json(pitch_cell, contact_cell),
        amount_raw=amount_raw,
        amount_min_usd=amount_min,
        amount_max_usd=amount_max,
    )


def _parse_publisher_b_compact(row_map: dict[str, str], *, section: str, section_name: str, raw_row: str) -> dict[str, Any]:
    target = _cell(row_map, "Target")
    pitch_cell = _cell(row_map, "Pitch / Submit")
    amount_raw, amount_min, amount_max = parse_monetary_fields(target, pitch_cell, _cell(row_map, "Notes"))
    return _base_entity(
        section=section,
        section_name=section_name,
        name=strip_md(_cell(row_map, "Name")),
        country=_opt_text(_cell(row_map, "Country")),
        links_json=json.dumps(parse_links(_cell(row_map, "Links")), ensure_ascii=False) or None,
        pitch=_opt_text(pitch_cell),
        contact=None,
        raw_row=raw_row,
        class_tier=parse_class_tier(target),
        budget_tier=resolve_budget_tier(amount_max_usd=amount_max, symbol_text=target),
        notable_titles=_opt_text(_cell(row_map, "Notable Titles")),
        notes=_opt_text(_cell(row_map, "Notes")),
        terms_raw=_terms_raw(target),
        submit_links_json=_submit_links_json(pitch_cell),
        amount_raw=amount_raw,
        amount_min_usd=amount_min,
        amount_max_usd=amount_max,
    )


def _parse_publisher_c(row_map: dict[str, str], *, section: str, section_name: str, raw_row: str) -> dict[str, Any]:
    backing_cell = _cell(row_map, "Backing / Angle")
    pitch_cell = _cell(row_map, "Pitch / Submit")
    backing = _opt_text(backing_cell)
    notes_cell = _cell(row_map, "Notes")
    amount_raw, amount_min, amount_max = parse_monetary_fields(backing_cell, notes_cell)
    return _base_entity(
        section=section,
        section_name=section_name,
        name=strip_md(_cell(row_map, "Name")),
        country=_opt_text(_cell(row_map, "Country")),
        links_json=json.dumps(parse_links(_cell(row_map, "Links")), ensure_ascii=False) or None,
        pitch=_opt_text(pitch_cell),
        contact=None,
        raw_row=raw_row,
        notable_titles=_opt_text(_cell(row_map, "Notable Titles")),
        notes=_opt_text(notes_cell),
        backing=backing,
        terms_raw=_terms_raw(backing_cell),
        submit_links_json=_submit_links_json(pitch_cell),
        amount_raw=amount_raw,
        amount_min_usd=amount_min,
        amount_max_usd=amount_max,
        budget_tier=resolve_budget_tier(amount_max_usd=amount_max, symbol_text=backing_cell),
    )


def _parse_platform_d(row_map: dict[str, str], *, section: str, section_name: str, raw_row: str) -> dict[str, Any]:
    funding_cell = _cell(row_map, "Funding Type")
    approach_cell = _cell(row_map, "How to Approach")
    target_cell = _cell(row_map, "Target")
    funding_terms = _opt_text(funding_cell)
    target_scope = _opt_text(target_cell)
    amount_raw, amount_min, amount_max = parse_monetary_fields(funding_cell, target_cell, _cell(row_map, "Notes"))
    return _base_entity(
        section=section,
        section_name=section_name,
        name=strip_md(_cell(row_map, "Name")),
        country=_opt_text(_cell(row_map, "Country")),
        links_json=json.dumps(parse_links(_cell(row_map, "Links")), ensure_ascii=False) or None,
        pitch=_opt_text(approach_cell),
        contact=None,
        raw_row=raw_row,
        notable_titles=_opt_text(_cell(row_map, "Notable / Program")),
        notes=_opt_text(_cell(row_map, "Notes")),
        funding_terms=funding_terms,
        target_scope=target_scope,
        terms_raw=_terms_raw(funding_cell),
        submit_links_json=_submit_links_json(approach_cell),
        amount_raw=amount_raw,
        amount_min_usd=amount_min,
        amount_max_usd=amount_max,
        budget_tier=resolve_budget_tier(amount_max_usd=amount_max, symbol_text=target_cell),
    )


def _parse_vc_equity(row_map: dict[str, str], *, section: str, section_name: str, raw_row: str) -> dict[str, Any]:
    stage = _cell(row_map, "Stage / Check")
    contact_cell = _cell(row_map, "Contact")
    contact = _opt_text(contact_cell)
    amount_raw, amount_min, amount_max = parse_monetary_fields(stage, _cell(row_map, "Notes"))
    return _base_entity(
        section=section,
        section_name=section_name,
        name=strip_md(_cell(row_map, "Name")),
        country=_opt_text(_cell(row_map, "Country")),
        links_json=json.dumps(parse_links(_cell(row_map, "Links")), ensure_ascii=False) or None,
        pitch=contact,
        contact=contact,
        raw_row=raw_row,
        notable_titles=_opt_text(_cell(row_map, "Portfolio Highlights")),
        notes=_opt_text(_cell(row_map, "Notes")),
        terms_raw=_terms_raw(stage),
        submit_links_json=_submit_links_json(contact_cell),
        amount_raw=amount_raw,
        amount_min_usd=amount_min,
        amount_max_usd=amount_max,
        budget_tier=resolve_budget_tier(amount_max_usd=amount_max, symbol_text=stage),
    )


def _parse_project_fund(row_map: dict[str, str], *, section: str, section_name: str, raw_row: str) -> dict[str, Any]:
    terms = _cell(row_map, "Funding & Terms")
    apply_cell = _cell(row_map, "How to Apply")
    amount_raw, amount_min, amount_max = parse_monetary_fields(terms, _cell(row_map, "Notes"))
    return _base_entity(
        section=section,
        section_name=section_name,
        name=strip_md(_cell(row_map, "Name")),
        country=_opt_text(_cell(row_map, "Country")),
        links_json=json.dumps(parse_links(_cell(row_map, "Links")), ensure_ascii=False) or None,
        pitch=_opt_text(apply_cell),
        contact=_opt_text(apply_cell),
        raw_row=raw_row,
        notable_titles=_opt_text(_cell(row_map, "Portfolio")),
        notes=_opt_text(_cell(row_map, "Notes")),
        terms_raw=_terms_raw(terms),
        submit_links_json=_submit_links_json(apply_cell),
        amount_raw=amount_raw,
        amount_min_usd=amount_min,
        amount_max_usd=amount_max,
        budget_tier=resolve_budget_tier(amount_max_usd=amount_max, symbol_text=terms),
    )


def _parse_grant_g(row_map: dict[str, str], *, section: str, section_name: str, raw_row: str) -> dict[str, Any]:
    amount_cell = _cell(row_map, "Amount")
    eligibility = _opt_text(_cell(row_map, "Eligibility / Focus"))
    links_cell = _cell(row_map, "Links")
    links = parse_links(links_cell)
    plain_links = _opt_text(links_cell)
    amount_raw, amount_min, amount_max = parse_monetary_fields(amount_cell)
    if links:
        pitch = " · ".join(f"[{l['label']}]({l['url']})" for l in links)
    else:
        pitch = plain_links
    return _base_entity(
        section=section,
        section_name=section_name,
        name=strip_md(_cell(row_map, "Program")),
        country=_opt_text(_cell(row_map, "Region")),
        links_json=json.dumps(links, ensure_ascii=False) if links else None,
        pitch=pitch,
        contact=None,
        raw_row=raw_row,
        notes=_opt_text(_cell(row_map, "Notes")),
        eligibility=eligibility,
        terms_raw=_terms_raw(amount_cell),
        submit_links_json=_submit_links_json(links_cell),
        amount_raw=amount_raw,
        amount_min_usd=amount_min,
        amount_max_usd=amount_max,
        budget_tier=resolve_budget_tier(amount_max_usd=amount_max),
    )


def parse_class_tier(text: str) -> str | None:
    t = strip_md(text)
    for cand in ("AAA", "AA", "Indie"):
        if re.search(rf"\b{re.escape(cand)}\b", t):
            return cand
    return None


_SCHEMA_HANDLERS: dict[str, Callable[..., dict[str, Any]]] = {
    "publisher_ab": _parse_publisher_ab,
    "publisher_b_compact": _parse_publisher_b_compact,
    "publisher_c": _parse_publisher_c,
    "platform_d": _parse_platform_d,
    "vc_equity": _parse_vc_equity,
    "project_fund": _parse_project_fund,
    "grant_g": _parse_grant_g,
}


def parse_directory_markdown(markdown: str) -> list[dict[str, Any]]:
    lines = markdown.splitlines()
    entities: list[dict[str, Any]] = []
    current_section: str | None = None
    current_section_name: str | None = None

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        line_no = i + 1

        m = re.match(r"^##\s+([A-G])\.\s+(.*)$", line)
        if m:
            current_section = m.group(1)
            current_section_name = strip_md(m.group(2))
            i += 1
            continue

        if stripped.startswith("|") and "|" in stripped and i + 1 < len(lines) and re.match(
            r"^\|\s*-{3,}", lines[i + 1].lstrip()
        ):
            if not current_section:
                i += 2
                while i < len(lines) and lines[i].lstrip().startswith("|"):
                    i += 1
                continue

            headers = _split_md_row(stripped, line_no=line_no)
            schema_id = _resolve_schema(headers, section=current_section, line_no=line_no, raw_row=stripped)
            handler = _SCHEMA_HANDLERS[schema_id]

            i += 2
            while i < len(lines):
                row = lines[i]
                row_no = i + 1
                if not row.lstrip().startswith("|"):
                    if row.strip() == "":
                        i += 1
                        continue
                    break
                if re.match(r"^\|\s*-{3,}", row.lstrip()):
                    raise ParseError(
                        "Unexpected table divider in body",
                        line_no=row_no,
                        raw_row=row,
                        section=current_section,
                    )

                cells = _split_md_row(row.lstrip(), line_no=row_no)
                if len(cells) != len(headers):
                    raise ParseError(
                        f"Row has {len(cells)} cells but header has {len(headers)}",
                        line_no=row_no,
                        raw_row=row,
                        section=current_section,
                    )

                row_map = dict(zip(headers, cells, strict=True))
                entities.append(
                    handler(
                        row_map,
                        section=current_section,
                        section_name=current_section_name or current_section,
                        raw_row=row,
                    )
                )
                i += 1
            continue

        i += 1

    if not entities:
        raise ParseError("No entities parsed (no tables found?)")
    return entities
