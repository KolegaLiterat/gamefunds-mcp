"""Ensure model-facing text is English-only."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest
from fastmcp import Client

from gamefunds.instructions import SERVER_INSTRUCTIONS
from gamefunds.server import build_server

POLISH_DIACRITICS = re.compile(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]")

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "gamefunds"
HELP_DIR = ROOT / "data" / "help"

# Modules whose string literals may reach the model via tool results or errors.
MODEL_FACING_MODULES = (
    "core.py",
    "match_scoring.py",
    "middleware.py",
    "sync.py",
    "server.py",
    "instructions.py",
    "guides.py",
)


def _iter_string_literals(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.value


@pytest.mark.anyio
async def test_tool_descriptions_contain_no_polish_diacritics():
    server = build_server()
    async with Client(server) as client:
        tools = await client.list_tools()
    for tool in tools:
        text = tool.description or ""
        assert not POLISH_DIACRITICS.search(text), f"{tool.name} description has Polish diacritics"


def test_model_facing_src_strings_contain_no_polish_diacritics():
    offenders: list[str] = []
    for name in MODEL_FACING_MODULES:
        path = SRC / name
        for literal in _iter_string_literals(path):
            if POLISH_DIACRITICS.search(literal):
                snippet = literal[:80].replace("\n", " ")
                offenders.append(f"{name}: {snippet!r}")
    assert not offenders, "Polish diacritics in model-facing strings:\n" + "\n".join(offenders)


def test_help_markdown_contains_no_polish_diacritics():
    offenders: list[str] = []
    for path in sorted(HELP_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if POLISH_DIACRITICS.search(text):
            offenders.append(path.name)
    assert not offenders, f"Polish diacritics in help files: {offenders}"


def test_server_instructions_set_and_mention_english():
    server = build_server()
    assert server.instructions
    assert "English" in server.instructions
    assert server.instructions == SERVER_INSTRUCTIONS


@pytest.mark.anyio
async def test_instructions_reach_initialize_result():
    server = build_server()
    async with Client(server) as client:
        init = await client.initialize()
    assert init.instructions
    assert "English" in init.instructions
    assert init.instructions == SERVER_INSTRUCTIONS
