import json
import secrets
from pathlib import Path

import httpx
import pytest

from fastmcp import Client
from gamefunds.auth import HTTP_TOKEN_REQUIRED_MSG, build_http_auth_verifier, require_http_token
from gamefunds.core import set_status
from gamefunds.db import upsert_entities
from gamefunds.http_serve import build_http_asgi_app
from gamefunds.middleware import SHARED_READONLY_DENIAL
from gamefunds.parser import parse_directory_markdown
from gamefunds.server import build_server
from tests.http_test_utils import http_test_client
from tests.test_tool_descriptions import EXPECTED_TOOLS

REPO_URL = "https://github.com/KolegaLiterat/gamefunds-mcp"


@pytest.fixture
def http_tokens(monkeypatch):
    primary = secrets.token_urlsafe(32)
    readonly = secrets.token_urlsafe(32)
    monkeypatch.setenv("GAMEFUNDS_TOKEN", primary)
    monkeypatch.setenv("GAMEFUNDS_TOKEN_READONLY", readonly)
    return {"primary": primary, "readonly": readonly}


@pytest.fixture
def catalog_db(tmp_path, monkeypatch):
    db = tmp_path / "http.db"
    monkeypatch.setenv("GAMEFUNDS_DB_PATH", str(db))
    monkeypatch.setenv("GAMEFUNDS_LOG_ARGS", "0")
    md = (Path(__file__).parent / "fixtures" / "directory_2026-07.md").read_text(encoding="utf-8")
    upsert_entities(parse_directory_markdown(md), db_path=db)
    return db


@pytest.fixture
def pipeline_secret(catalog_db):
    set_status(
        "anshar-publishing",
        "in_talks",
        note="CONFIDENTIAL: advance talks 140k",
    )
    return "anshar-publishing"


@pytest.fixture
def http_app(catalog_db, http_tokens):
    server = build_server(transport="http")
    return build_http_asgi_app(server)


def test_http_requires_token_to_start(monkeypatch):
    monkeypatch.delenv("GAMEFUNDS_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match=HTTP_TOKEN_REQUIRED_MSG):
        require_http_token()
    with pytest.raises(RuntimeError, match=HTTP_TOKEN_REQUIRED_MSG):
        build_server(transport="http")


@pytest.mark.anyio
async def test_stdio_pipeline_visible_with_secret(catalog_db, monkeypatch, pipeline_secret):
    monkeypatch.delenv("GAMEFUNDS_TOKEN", raising=False)
    monkeypatch.delenv("GAMEFUNDS_TOKEN_READONLY", raising=False)

    server = build_server(transport="stdio")
    async with Client(server) as client:
        ent = (await client.call_tool("get_entity", {"slug": pipeline_secret})).data
        assert ent["pipeline"] is not None
        lst = (await client.call_tool("list_pipeline", {})).data
        assert lst["by_status"].get("in_talks", 0) >= 1


@pytest.mark.anyio
async def test_stdio_without_tokens_lists_all_tools(catalog_db, monkeypatch):
    monkeypatch.delenv("GAMEFUNDS_TOKEN", raising=False)
    monkeypatch.delenv("GAMEFUNDS_TOKEN_READONLY", raising=False)

    server = build_server(transport="stdio")
    async with Client(server) as client:
        tools = await client.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS


@pytest.mark.anyio
async def test_wrong_token_returns_401(http_app):
    async with http_app.router.lifespan_context(http_app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=http_app),
            base_url="http://testserver",
        ) as client:
            response = await client.post(
                "/mcp",
                headers={"Authorization": "Bearer definitely-wrong"},
            )
    assert response.status_code == 401
    assert "definitely-wrong" not in response.text


@pytest.mark.anyio
async def test_readonly_token_denies_set_status_with_repo_link(http_app, http_tokens):
    async with http_test_client(http_app, http_tokens["readonly"]) as client:
        with pytest.raises(Exception, match="shared read-only endpoint"):
            await client.call_tool(
                "set_status",
                {"slug": "anshar-publishing", "status": "contacted"},
            )


@pytest.mark.anyio
async def test_readonly_token_denies_list_pipeline_with_repo_link(
    http_app, http_tokens, pipeline_secret
):
    async with http_test_client(http_app, http_tokens["readonly"]) as client:
        with pytest.raises(Exception, match=REPO_URL):
            await client.call_tool("list_pipeline", {})


@pytest.mark.anyio
async def test_readonly_token_get_entity_omits_pipeline(
    http_app, http_tokens, pipeline_secret
):
    async with http_test_client(http_app, http_tokens["readonly"]) as client:
        out = (await client.call_tool("get_entity", {"slug": pipeline_secret})).data
    assert "entity" in out
    assert "pipeline" not in out
    assert "CONFIDENTIAL" not in json.dumps(out)


@pytest.mark.anyio
async def test_full_token_list_pipeline_sees_entries(
    http_app, http_tokens, pipeline_secret
):
    async with http_test_client(http_app, http_tokens["primary"]) as client:
        out = (await client.call_tool("list_pipeline", {})).data
    assert out["by_status"].get("in_talks", 0) >= 1
    slugs = {e["slug"] for e in out["entries"]}
    assert pipeline_secret in slugs


@pytest.mark.anyio
async def test_full_token_get_entity_includes_pipeline(
    http_app, http_tokens, pipeline_secret
):
    async with http_test_client(http_app, http_tokens["primary"]) as client:
        out = (await client.call_tool("get_entity", {"slug": pipeline_secret})).data
    assert out["pipeline"] is not None
    assert "140k" in json.dumps(out["pipeline"])


@pytest.mark.anyio
async def test_readonly_token_allows_catalog_tools(http_app, http_tokens):
    async with http_test_client(http_app, http_tokens["readonly"]) as client:
        search = (await client.call_tool("search_funding", {"query": "Devolver", "limit": 3})).data
        assert search["total_matched"] >= 1

        filt = (
            await client.call_tool(
                "filter_funding",
                {"section": "A", "country": "Poland", "limit": 3},
            )
        ).data
        assert "total_matched" in filt

        match = (
            await client.call_tool(
                "match_project",
                {
                    "genre": "roguelike",
                    "budget_usd": 400_000,
                    "stage": "prototype",
                },
            )
        ).data
        assert "by_type" in match

        rubric = (await client.call_tool("get_pitch_rubric", {"funding_type": "publisher"})).data
        assert "slides" in rubric

        review = (
            await client.call_tool(
                "review_pitch",
                {"deck_markdown": "# Title\n\nBudget: $400k\n"},
            )
        ).data
        assert "hard_findings" in review


@pytest.mark.anyio
async def test_readonly_token_allows_search(http_app, http_tokens):
    async with http_test_client(http_app, http_tokens["readonly"]) as client:
        out = (await client.call_tool("search_funding", {"query": "Devolver", "limit": 3})).data
    assert out["total_matched"] >= 1


@pytest.mark.anyio
async def test_rate_limit_returns_429_on_burst(catalog_db, http_tokens, monkeypatch):
    monkeypatch.setenv("GAMEFUNDS_RATE_LIMIT", "60/minute")
    app = build_http_asgi_app(build_server(transport="http"))
    token = http_tokens["primary"]

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://testserver",
        ) as client:
            headers = {"Authorization": f"Bearer {token}"}
            last_status = None
            for _ in range(61):
                response = await client.post("/mcp", headers=headers)
                last_status = response.status_code
            assert last_status == 429
            assert response.headers.get("retry-after") is not None
            assert token not in response.text


@pytest.mark.anyio
async def test_token_not_logged_when_args_enabled(http_app, http_tokens, tmp_path, monkeypatch):
    log_path = tmp_path / "tool_calls.log"
    monkeypatch.setenv("GAMEFUNDS_LOG_ARGS", "1")

    from gamefunds.middleware import LoggingMiddleware

    class _Msg:
        name = "search_funding"
        arguments = {"query": http_tokens["primary"], "limit": 3}

    class _Ctx:
        message = _Msg()

    middleware = LoggingMiddleware(log_path=str(log_path))

    async def _next(_ctx):
        return {"ok": True}

    await middleware.on_call_tool(_Ctx(), _next)
    text = log_path.read_text(encoding="utf-8")
    payload = json.loads(text.strip())
    assert http_tokens["primary"] not in text
    assert payload["args"]["query"].startswith("<str:")


def test_shared_readonly_denial_mentions_repo():
    assert REPO_URL in SHARED_READONLY_DENIAL
    assert "shared read-only endpoint" in SHARED_READONLY_DENIAL


def test_build_http_auth_verifier_uses_compare_digest(monkeypatch):
    monkeypatch.setenv("GAMEFUNDS_TOKEN", "secret-token-value")
    verifier = build_http_auth_verifier()
    import asyncio

    async def _check():
        assert await verifier.verify_token("secret-token-value") is not None
        assert await verifier.verify_token("secret-token-wrong") is None

    asyncio.run(_check())
