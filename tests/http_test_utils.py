from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport
from starlette.applications import Starlette


@asynccontextmanager
async def http_test_client(
    app: Starlette,
    token: str,
) -> AsyncIterator[Client]:
    async with app.router.lifespan_context(app):
        def factory(**kwargs):
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers=kwargs.get("headers"),
                timeout=kwargs.get("timeout"),
            )

        transport = StreamableHttpTransport(
            "http://testserver/mcp",
            headers={"Authorization": f"Bearer {token}"},
            httpx_client_factory=factory,
        )
        client = Client(transport)
        async with client:
            yield client
