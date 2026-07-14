from __future__ import annotations

import re
from typing import Any


class RubricParseError(Exception):
    def __init__(self, message: str, *, section: str | None = None):
        super().__init__(message)
        self.section = section


TAILORING_HEADINGS: dict[str, str] = {
    "Tailoring: Publisher Pitch": "publisher",
    "Tailoring: Project Investor Pitch": "project_investor",
    "Tailoring: Equity / VC Pitch": "vc_equity",
    "Tailoring: Grant Application": "grant",
}

SECTION_TO_FUNDING_TYPE: dict[str, str] = {
    "A": "publisher",
    "B": "publisher",
    "C": "publisher",
    "D": "publisher",
    "E": "vc_equity",
    "F": "project_investor",
    "G": "grant",
}

FUNDING_TYPES: tuple[str, ...] = ("publisher", "grant", "vc_equity", "project_investor")


def infer_funding_type(section: str | None) -> str:
    if not section:
        return "publisher"
    return SECTION_TO_FUNDING_TYPE.get(section.upper(), "publisher")


def _split_h2_sections(md: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current = ""
    buf: list[str] = []
    for line in md.splitlines():
        if line.startswith("## "):
            if current:
                sections[current] = "\n".join(buf).strip()
            current = line[3:].strip()
            buf = []
        else:
            buf.append(line)
    if current:
        sections[current] = "\n".join(buf).strip()
    return sections


def _parse_slide_range(text: str) -> list[int]:
    m = re.search(r"(\d+)\s*[–-]\s*(\d+)\s+slides", text, re.I)
    if not m:
        raise RubricParseError("Missing slide range (expected '10–20 slides')", section="The Universal Deck Structure")
    return [int(m.group(1)), int(m.group(2))]


def _parse_universal_table(text: str) -> tuple[list[dict[str, Any]], str | None]:
    slide_range = _parse_slide_range(text)
    slides: list[dict[str, Any]] = []
    appendix: str | None = None

    for line in text.splitlines():
        if line.lower().startswith("appendix"):
            appendix = line.strip()
            continue
        m = re.match(r"^\|\s*(\d+)\s*\|\s*\*\*(.+?)\*\*\s*\|\s*(.+?)\s*\|$", line)
        if not m:
            continue
        slides.append(
            {
                "n": int(m.group(1)),
                "title": m.group(2).strip(),
                "one_job": m.group(3).strip(),
                "notes": None,
                "must_contain": [],
            }
        )

    if len(slides) != 11:
        raise RubricParseError(f"Expected 11 universal slides, found {len(slides)}", section="The Universal Deck Structure")

    return slides, appendix


_SLIDE_NOTE_RE = re.compile(
    r"\*\*(\d+)\s*[—–-]\s*([^.*]+)\.\*\*\s*(.+?)(?=\n\n\*\*\d+\s*[—–-]|\n\n##|\Z)",
    re.DOTALL,
)


def _extract_must_contain(notes: str) -> list[str]:
    hints: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        phrase = re.sub(r"\s+", " ", raw).strip(" .—-").lower()
        if len(phrase) < 4 or phrase in seen:
            return
        seen.add(phrase)
        hints.append(phrase)

    for m in re.finditer(r'["“]([^"”\[]+)["”]', notes):
        add(m.group(1))
    for m in re.finditer(r"\*\*([^*]+)\*\*", notes):
        add(m.group(1))
    for m in re.finditer(r"(?:^|\n)-\s+(.+)", notes):
        add(m.group(1).split("—")[0].split(" - ")[0])

    for hint in (
        "elevator pitch",
        "fans of",
        "wishlist",
        "traction",
        "comparables",
        "comps",
        "median",
        "shipped",
        "budget",
        "ask",
        "burn rate",
        "usp",
    ):
        if hint in notes.lower():
            add(hint)

    return hints


def _parse_slide_notes(text: str, slides: list[dict[str, Any]]) -> None:
    by_n = {s["n"]: s for s in slides}
    matches = list(_SLIDE_NOTE_RE.finditer(text))
    if not matches:
        raise RubricParseError("No slide-by-slide notes found", section="Slide-by-Slide Notes")

    for m in matches:
        n = int(m.group(1))
        title = m.group(2).strip()
        notes = m.group(3).strip()
        slide = by_n.get(n)
        if not slide:
            raise RubricParseError(f"Note for unknown slide {n}", section="Slide-by-Slide Notes")
        slide["notes"] = notes
        slide["must_contain"] = _extract_must_contain(notes)


def _parse_bullets(text: str) -> list[str]:
    items: list[str] = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        item = line[2:].strip()
        item = re.sub(r"^\*\*([^*]+)\*\*", r"\1", item)
        items.append(item)
    return items


def _parse_deemphasize_line(text: str) -> list[str]:
    if "**De-emphasize:**" not in text:
        return []
    post = text.split("**De-emphasize:**", 1)[1].strip()
    first_para = post.split("\n\n")[0].strip().splitlines()[0].strip()
    for sep in (" — ", " – ", " - "):
        if sep in first_para:
            first_para = first_para.split(sep, 1)[0].strip()
            break
    first_para = first_para.rstrip(".")
    return [p.strip() for p in first_para.split(",") if p.strip()]


def _parse_tailoring(text: str) -> dict[str, list[str]]:
    emphasize_block = text
    if "**De-emphasize:**" in text:
        emphasize_block = text.split("**De-emphasize:**", 1)[0]

    emphasize = [item for item in _parse_bullets(emphasize_block) if item]
    deemphasize = _parse_deemphasize_line(text)
    return {"emphasize": emphasize, "deemphasize": deemphasize}


def _parse_tailoring_sections(sections: dict[str, str]) -> dict[str, dict[str, list[str]]]:
    tailoring: dict[str, dict[str, list[str]]] = {}
    for heading, key in TAILORING_HEADINGS.items():
        body = sections.get(heading)
        if not body:
            raise RubricParseError(f"Missing tailoring section: {heading}")
        parsed = _parse_tailoring(body)
        if not parsed["emphasize"]:
            raise RubricParseError(f"No emphasize bullets in {heading}")
        if not parsed["deemphasize"]:
            raise RubricParseError(f"Missing de-emphasize line in {heading}")
        tailoring[key] = parsed
    return tailoring


def parse_pitch_tutorial(md: str) -> dict[str, Any]:
    """Parse PitchDeckTutorial.md into a serializable rubric payload."""
    sections = _split_h2_sections(md)

    universal = sections.get("The Universal Deck Structure")
    if not universal:
        raise RubricParseError("Missing section: The Universal Deck Structure")

    slide_range = _parse_slide_range(universal)
    slides, appendix = _parse_universal_table(universal)

    notes_sec = sections.get("Slide-by-Slide Notes")
    if not notes_sec:
        raise RubricParseError("Missing section: Slide-by-Slide Notes")
    _parse_slide_notes(notes_sec, slides)

    tailoring = _parse_tailoring_sections(sections)

    mistakes_sec = sections.get("Common Mistakes")
    if not mistakes_sec:
        raise RubricParseError("Missing section: Common Mistakes")
    common_mistakes = _parse_bullets(mistakes_sec)
    if not common_mistakes:
        raise RubricParseError("No common mistakes bullets found")

    design_sec = sections.get("Design & Delivery Rules")
    if not design_sec:
        raise RubricParseError("Missing section: Design & Delivery Rules")
    design_rules = _parse_bullets(design_sec)
    if not design_rules:
        raise RubricParseError("No design rules bullets found")

    return {
        "slide_range": slide_range,
        "appendix": appendix,
        "slides": slides,
        "tailoring": tailoring,
        "common_mistakes": common_mistakes,
        "design_rules": design_rules,
    }
