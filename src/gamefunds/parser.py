from __future__ import annotations

import json
import re
from typing import Any


class ParseError(ValueError):
    def __init__(self, message: str, *, line_no: int | None = None, raw_line: str | None = None):
        super().__init__(message)
        self.line_no = line_no
        self.raw_line = raw_line


_EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*(.*?)\*\*")


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
    # remove inline code/backticks
    text = text.replace("`", "")
    # keep link labels, drop URLs
    text = _MD_LINK_RE.sub(r"\1", text)
    # collapse spaces
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
    if stars == 0:
        return None
    if stars not in (1, 2, 3):
        return None
    return stars


def parse_budget_tier(text: str) -> int | None:
    # Normalize variants like "`$ $$`" or "`$$ $$$`"
    if "$$$" in text:
        return 3
    if "$$" in text:
        return 2
    if "$" in text:
        return 1
    return None


def parse_class_tier(text: str) -> str | None:
    t = strip_md(text)
    for cand in ("AAA", "AA", "Indie"):
        if re.search(rf"\b{re.escape(cand)}\b", t):
            return cand
    return None


def parse_lifetime_rev_usd(text: str) -> int | None:
    t = strip_md(text)
    if t in {"—", "-", ""} or "[?]" in t:
        return None
    # Expect formats like "$1.0B", "$603.0M", "$397.1K"
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
    # naive split works for this file (no escaped pipes inside cells)
    parts = [p.strip() for p in line.strip("|").split("|")]
    return parts


def parse_directory_markdown(markdown: str) -> list[dict[str, Any]]:
    """
    Parse GameFundingDirectory.md into normalized entity dicts matching the `entities` schema.

    Implementation is added in the parser step (highest risk).
    """
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

        # detect a table header row
        if stripped.startswith("|") and "|" in stripped and i + 1 < len(lines) and re.match(r"^\|\s*-{3,}", lines[i + 1].lstrip()):
            if not current_section:
                # Ignore non-directory tables (e.g., Legend) that appear before section A-G.
                i += 2
                while i < len(lines) and lines[i].lstrip().startswith("|"):
                    i += 1
                continue

            headers = _split_md_row(stripped, line_no=line_no)
            divider = lines[i + 1]
            if not divider.lstrip().startswith("|"):
                raise ParseError("Malformed table divider row", line_no=line_no + 1, raw_line=divider)

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
                    raise ParseError("Unexpected table divider in body", line_no=row_no, raw_line=row)

                cells = _split_md_row(row.lstrip(), line_no=row_no)
                if len(cells) != len(headers):
                    raise ParseError(
                        f"Row has {len(cells)} cells but header has {len(headers)}",
                        line_no=row_no,
                        raw_line=row,
                    )

                row_map = dict(zip(headers, cells, strict=True))
                ent = _row_to_entity(
                    section=current_section,
                    section_name=current_section_name or current_section,
                    headers=headers,
                    row_map=row_map,
                    raw_row=row,
                    line_no=row_no,
                )
                entities.append(ent)
                i += 1

            continue

        i += 1

    if not entities:
        raise ParseError("No entities parsed (no tables found?)")
    return entities


def _row_to_entity(
    *,
    section: str,
    section_name: str,
    headers: list[str],
    row_map: dict[str, str],
    raw_row: str,
    line_no: int,
) -> dict[str, Any]:
    def get(key: str) -> str:
        if key not in row_map:
            raise ParseError(f"Missing required column: {key}", line_no=line_no, raw_line=raw_row)
        return row_map[key]

    has_warning = "⚠️" in raw_row

    # Section G (grants) uses a different schema
    if headers and headers[0] == "Program":
        name = strip_md(get("Program"))
        country = strip_md(get("Region")) or None
        links = parse_links(get("Links"))
        links_json = json.dumps(links, ensure_ascii=False) if links else None
        amount = strip_md(get("Amount")) or None
        eligibility = strip_md(get("Eligibility / Focus")) or None
        pitch = eligibility or None
        contact = None

        notes_parts: list[str] = []
        if amount:
            notes_parts.append(f"Amount: {amount}")
        n = strip_md(get("Notes")) or None
        if n:
            notes_parts.append(n)
        notes = " | ".join(notes_parts) if notes_parts else None

        slug = slugify(name)
        return {
            "slug": slug,
            "section": section,
            "section_name": section_name,
            "name": name,
            "country": country,
            "links_json": links_json,
            "pitch": pitch,
            "contact": contact,
            "contact_email": extract_email(pitch or "", ""),
            "class_tier": None,
            "budget_tier": None,
            "comm_rating": None,
            "lifetime_rev_usd": None,
            "notable_titles": None,
            "notes": notes,
            "has_warning": bool(has_warning),
            "raw_row": raw_row,
        }

    # base schema fields
    name: str
    country: str | None
    links_json: str | None
    pitch: str | None
    contact: str | None
    class_tier: str | None = None
    budget_tier: int | None = None
    comm_rating: int | None = None
    lifetime_rev_usd: int | None = None
    notable_titles: str | None = None
    notes: str | None = None

    if headers[:3] == ["Name", "Country", "Links"] and "Notes" in headers:
        name = strip_md(get("Name"))
        country = strip_md(get("Country"))
        if country in {"—", "-", ""} or "[?]" in country:
            country = None

        links = parse_links(get("Links"))
        links_json = json.dumps(links, ensure_ascii=False) if links else None

        notes = strip_md(get("Notes")) or None

        # common columns for A/B
        if "Pitch / Submit" in headers:
            pitch = strip_md(get("Pitch / Submit")) or None
        elif "How to Approach" in headers:
            pitch = strip_md(get("How to Approach")) or None
        else:
            pitch = None

        if "Contact" in headers:
            contact = strip_md(get("Contact")) or None
            if contact in {"—", "-", ""}:
                contact = None
        elif "How to Apply" in headers:
            contact = strip_md(get("How to Apply")) or None
        else:
            contact = None

        if "Class / Budget / Comm." in headers:
            cbc = get("Class / Budget / Comm.")
            class_tier = parse_class_tier(cbc)
            budget_tier = parse_budget_tier(cbc)
            comm_rating = parse_comm_rating(cbc)

        if "Lifetime Rev" in headers:
            lifetime_rev_usd = parse_lifetime_rev_usd(get("Lifetime Rev"))

        # variants
        if "Notable Titles" in headers:
            notable_titles = strip_md(get("Notable Titles")) or None
        elif "Notable / Program" in headers:
            notable_titles = strip_md(get("Notable / Program")) or None
        elif "Portfolio" in headers:
            notable_titles = strip_md(get("Portfolio")) or None
        elif "Portfolio Highlights" in headers:
            notable_titles = strip_md(get("Portfolio Highlights")) or None

    else:
        raise ParseError(f"Unknown table schema headers: {headers!r}", line_no=line_no, raw_line=raw_row)

    contact_email = extract_email(pitch or "", contact or "")
    if pitch and ("(form" in pitch.lower() or "form only" in pitch.lower()) and contact_email:
        # keep email if present even if form mentioned; otherwise ok
        pass

    slug = slugify(name)

    if pitch in {"—", "-", ""}:
        pitch = None
    if notes in {"—", "-", ""}:
        notes = None

    return {
        "slug": slug,
        "section": section,
        "section_name": section_name,
        "name": name,
        "country": country,
        "links_json": links_json,
        "pitch": pitch,
        "contact": contact,
        "contact_email": contact_email,
        "class_tier": class_tier,
        "budget_tier": budget_tier,
        "comm_rating": comm_rating,
        "lifetime_rev_usd": lifetime_rev_usd,
        "notable_titles": notable_titles,
        "notes": notes,
        "has_warning": bool(has_warning),
        "raw_row": raw_row,
    }

