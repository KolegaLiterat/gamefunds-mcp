from __future__ import annotations

import os
import secrets
from typing import Any

from fastmcp.server.auth import AccessToken, TokenVerifier

HTTP_TOKEN_REQUIRED_MSG = "HTTP transport requires GAMEFUNDS_TOKEN. Generate one: gamefunds token"


class StaticTokenVerifier(TokenVerifier):
    """Map bearer tokens from environment variables to AccessToken scopes."""

    def __init__(self) -> None:
        super().__init__()
        self._configured: list[tuple[str, dict[str, Any]]] = []
        primary = os.getenv("GAMEFUNDS_TOKEN", "").strip()
        if primary:
            self._configured.append(
                (primary, {"client_id": "gamefunds", "scopes": ["read", "write"]}),
            )
        readonly = os.getenv("GAMEFUNDS_TOKEN_READONLY", "").strip()
        if readonly:
            self._configured.append(
                (
                    readonly,
                    {"client_id": "gamefunds-readonly", "scopes": ["read"]},
                ),
            )

    async def verify_token(self, token: str) -> AccessToken | None:
        for configured, claims in self._configured:
            if secrets.compare_digest(token, configured):
                scopes = list(claims["scopes"])
                return AccessToken(
                    token=token,
                    client_id=str(claims["client_id"]),
                    scopes=scopes,
                    claims=claims,
                )
        return None


def require_http_token() -> str:
    token = os.getenv("GAMEFUNDS_TOKEN", "").strip()
    if not token:
        raise RuntimeError(HTTP_TOKEN_REQUIRED_MSG)
    return token


def build_http_auth_verifier() -> StaticTokenVerifier:
    require_http_token()
    return StaticTokenVerifier()
