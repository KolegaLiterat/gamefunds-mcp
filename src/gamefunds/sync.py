from __future__ import annotations

from typing import Any


def check_updates() -> dict[str, Any]:
    """Check GitHub for a newer commit affecting GameFundingDirectory.md."""
    raise NotImplementedError()


def sync_directory(*, dry_run: bool = True, full_diff: bool = False) -> dict[str, Any]:
    """Fetch, parse, diff, and optionally apply the directory update."""
    raise NotImplementedError()

