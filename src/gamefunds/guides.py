from __future__ import annotations

from pathlib import Path

from .paths import guides_dir as _guides_dir

GUIDE_SPECS: list[dict[str, str]] = [
    {
        "uri": "gamefunds://guide/definitions",
        "upstream": "Definitions.md",
        "local": "Definitions.md",
    },
    {
        "uri": "gamefunds://guide/definitions/pl",
        "upstream": "DefinitionsPL.md",
        "local": "DefinitionsPL.md",
    },
    {
        "uri": "gamefunds://guide/funding-types",
        "upstream": "FundingTypes.md",
        "local": "FundingTypes.md",
    },
    {
        "uri": "gamefunds://guide/funding-types/pl",
        "upstream": "FundingTypesPL.md",
        "local": "FundingTypesPL.md",
    },
    {
        "uri": "gamefunds://guide/pitch-deck",
        "upstream": "PitchDeckTutorial.md",
        "local": "PitchDeckTutorial.md",
    },
    {
        "uri": "gamefunds://guide/pitch-deck/pl",
        "upstream": "PitchDeckTutorialPL.md",
        "local": "PitchDeckTutorialPL.md",
    },
]

UPSTREAM_GUIDE_FILES = [spec["upstream"] for spec in GUIDE_SPECS]


def guides_dir() -> Path:
    return _guides_dir()


def guide_sha_meta_key(upstream_name: str) -> str:
    return f"guide_sha_{upstream_name}"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def register_guides(server) -> None:
    """
    Register `gamefunds://guide/...` resources.

    Content is loaded from `data/guides/` after sync; until then a short placeholder is returned.
    """

    root = guides_dir()

    for spec in GUIDE_SPECS:
        local_path = root / spec["local"]
        uri = spec["uri"]
        fallback = f"{spec['upstream']} guide not synced yet."

        def _make_handler(path: Path, placeholder: str):
            def _handler() -> str:
                return _read(path) if path.exists() else placeholder

            return _handler

        server.resource(uri)(_make_handler(local_path, fallback))
