from __future__ import annotations

from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def register_guides(server) -> None:
    """
    Register `gamefunds://guide/...` resources.

    These are loaded from `data/guides/` if present (synced later), otherwise a small placeholder.
    """

    guides_dir = _root() / "data" / "guides"

    def _maybe(file: str, fallback: str) -> str:
        p = guides_dir / file
        return _read(p) if p.exists() else fallback

    @server.resource("gamefunds://guide/definitions")
    def definitions() -> str:
        return _maybe("definitions.md", "Definitions guide not synced yet.")

    @server.resource("gamefunds://guide/funding-types")
    def funding_types() -> str:
        return _maybe("funding-types.md", "Funding types guide not synced yet.")

    @server.resource("gamefunds://guide/pitch-deck")
    def pitch_deck() -> str:
        return _maybe("pitch-deck.md", "Pitch deck guide not synced yet.")

