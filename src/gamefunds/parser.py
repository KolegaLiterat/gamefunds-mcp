from __future__ import annotations

from typing import Any


class ParseError(ValueError):
    def __init__(self, message: str, *, line_no: int | None = None, raw_line: str | None = None):
        super().__init__(message)
        self.line_no = line_no
        self.raw_line = raw_line


def parse_directory_markdown(markdown: str) -> list[dict[str, Any]]:
    """
    Parse GameFundingDirectory.md into normalized entity dicts matching the `entities` schema.

    Implementation is added in the parser step (highest risk).
    """
    raise NotImplementedError()


def slugify(name: str) -> str:
    """Create a stable slug from an entity name (lowercase, hyphens)."""
    raise NotImplementedError()

