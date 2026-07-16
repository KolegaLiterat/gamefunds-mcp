from pathlib import Path

import httpx

from gamefunds import sync as sync_mod
from gamefunds.db import connect, init_db, upsert_entities
from gamefunds.guides import UPSTREAM_GUIDE_FILES, guide_sha_meta_key
from gamefunds.parser import parse_directory_markdown
from gamefunds.rubrics import sync_rubrics_from_tutorial


def _blob_sha(text: str) -> str:
    return sync_mod._git_blob_sha(text.encode("utf-8"))


DEFAULT_GUIDE_CONTENTS = {
    "Definitions.md": "# Definitions EN\n",
    "DefinitionsPL.md": "# Definitions PL\n",
    "FundingTypes.md": "# Funding EN\n",
    "FundingTypesPL.md": "# Funding PL\n",
    "PitchDeckTutorial.md": "# Pitch EN\n",
    "PitchDeckTutorialPL.md": "# Pitch PL\n",
}


def _install_github_mocks(
    monkeypatch,
    *,
    directory_md: str,
    guide_contents: dict[str, str] | None = None,
    api_call_counter: list[str] | None = None,
):
    guide_contents = {**DEFAULT_GUIDE_CONTENTS, **(guide_contents or {})}
    api_urls = api_call_counter if api_call_counter is not None else []

    class FakeCommitResp:
        status_code = 200
        headers: dict[str, str] = {}

        def raise_for_status(self):
            return None

        def json(self):
            return [
                {
                    "sha": "abc",
                    "commit": {
                        "message": "msg",
                        "committer": {"date": "2026-01-01T00:00:00Z"},
                    },
                }
            ]

    class FakeTextResp:
        def __init__(self, text: str):
            self._text = text
            self.status_code = 200
            self.headers: dict[str, str] = {}
            self.content = text.encode("utf-8")

        def raise_for_status(self):
            return None

        @property
        def text(self):
            return self._text

    def fake_get(self, url, params=None, headers=None):
        url = str(url)
        if "api.github.com" in url:
            api_urls.append(url)
        if url.endswith("/commits") or url.rstrip("/").endswith("/commits"):
            return FakeCommitResp()
        if "/contents/" in url:
            raise AssertionError(f"Contents API must not be used (rate limit): {url}")
        if url.endswith("/GameFundingDirectory.md"):
            return FakeTextResp(directory_md)
        for name, content in guide_contents.items():
            if url.endswith(f"/{name}"):
                return FakeTextResp(content)
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(httpx.Client, "get", fake_get, raising=True)


def test_check_updates_shape(monkeypatch, tmp_path: Path):
    db = tmp_path / "t.db"
    monkeypatch.setattr(sync_mod, "DEFAULT_DB_PATH", db)
    _install_github_mocks(monkeypatch, directory_md="# noop")

    out = sync_mod.check_updates()
    assert set(out.keys()) >= {
        "has_update",
        "current_sha",
        "last_synced_sha",
        "last_synced_at",
        "commit_message",
        "commit_date",
        "directory_has_update",
        "guides_have_update",
        "changed_guides",
        "guides",
    }
    assert out["guides_have_update"] is True
    assert set(out["guides"]) == set(UPSTREAM_GUIDE_FILES)
    assert all("_content" not in g for g in out["guides"].values())


def test_sync_directory_dry_run_diff(monkeypatch, tmp_path: Path):
    db = tmp_path / "t.db"
    guides = tmp_path / "guides"
    monkeypatch.setattr(sync_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(sync_mod, "guides_dir", lambda: guides)
    init_db(db)

    base = Path(__file__).parent / "fixtures" / "directory_small_base.md"
    modified = Path(__file__).parent / "fixtures" / "directory_small_modified.md"

    base_rows = parse_directory_markdown(base.read_text(encoding="utf-8"))
    upsert_entities(base_rows, db_path=db)

    _install_github_mocks(
        monkeypatch,
        directory_md=modified.read_text(encoding="utf-8"),
        guide_contents={"Definitions.md": "# Definitions\n"},
    )

    out = sync_mod.sync_directory(dry_run=True, full_diff=False)
    assert out["applied"] is False
    assert out["added"] == 1
    assert out["removed"] == 0
    assert out["changed"] >= 1
    assert out["guides_added"] == 6
    assert out["guides_updated"] == 0
    assert out["guides_applied"] is False
    assert not (guides / "Definitions.md").exists()

    with connect(db) as conn:
        r = conn.execute(
            "SELECT country, budget_tier, comm_rating FROM entities WHERE slug='alpha-pub'"
        ).fetchone()
    assert r["country"] == "Poland"


def test_sync_directory_applies_guides(monkeypatch, tmp_path: Path):
    db = tmp_path / "t.db"
    guides = tmp_path / "guides"
    monkeypatch.setattr(sync_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(sync_mod, "guides_dir", lambda: guides)
    init_db(db)

    base = Path(__file__).parent / "fixtures" / "directory_small_base.md"
    guide_fixtures = dict(DEFAULT_GUIDE_CONTENTS)

    _install_github_mocks(
        monkeypatch,
        directory_md=base.read_text(encoding="utf-8"),
        guide_contents=guide_fixtures,
    )

    out = sync_mod.sync_directory(dry_run=False, full_diff=False)
    assert out["applied"] is True
    assert out["guides_added"] == 6
    assert out["guides_applied"] is True
    assert (guides / "DefinitionsPL.md").read_text(encoding="utf-8") == "# Definitions PL\n"

    with connect(db) as conn:
        stored = conn.execute(
            "SELECT value FROM meta WHERE key=?",
            (guide_sha_meta_key("DefinitionsPL.md"),),
        ).fetchone()
    assert stored["value"] == _blob_sha("# Definitions PL\n")


def test_sync_rebuilds_rubrics_when_stale(monkeypatch, tmp_path: Path):
    db = tmp_path / "t.db"
    guides = tmp_path / "guides"
    rubrics = tmp_path / "rubrics.json"
    monkeypatch.setattr(sync_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(sync_mod, "guides_dir", lambda: guides)
    monkeypatch.setattr(sync_mod, "RUBRICS_PATH", rubrics)
    monkeypatch.setattr("gamefunds.rubrics.RUBRICS_PATH", rubrics)
    init_db(db)

    base = Path(__file__).parent / "fixtures" / "directory_small_base.md"
    tutorial = Path(__file__).parent / "fixtures" / "PitchDeckTutorial.md"
    base_rows = parse_directory_markdown(base.read_text(encoding="utf-8"))
    upsert_entities(base_rows, db_path=db)

    guide_shas = {name: _blob_sha(f"# {name}\n") for name in DEFAULT_GUIDE_CONTENTS}
    guide_shas["PitchDeckTutorial.md"] = _blob_sha(tutorial.read_text(encoding="utf-8"))

    guides.mkdir()
    for name in DEFAULT_GUIDE_CONTENTS:
        content = tutorial.read_text(encoding="utf-8") if name == "PitchDeckTutorial.md" else f"# {name}\n"
        (guides / name).write_text(content, encoding="utf-8")

    with connect(db) as conn:
        for name, sha in guide_shas.items():
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?);",
                (guide_sha_meta_key(name), sha),
            )
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('rubric_stale', 'true');")
        conn.commit()

    sync_rubrics_from_tutorial(
        tutorial.read_text(encoding="utf-8"),
        source_sha=guide_shas["PitchDeckTutorial.md"],
        db_path=db,
    )
    with connect(db) as conn:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('rubric_stale', 'true');")
        conn.commit()

    guide_contents = {
        name: tutorial.read_text(encoding="utf-8") if name == "PitchDeckTutorial.md" else f"# {name}\n"
        for name in DEFAULT_GUIDE_CONTENTS
    }
    _install_github_mocks(
        monkeypatch,
        directory_md=base.read_text(encoding="utf-8"),
        guide_contents=guide_contents,
    )

    out = sync_mod.sync_directory(dry_run=False, full_diff=False)
    assert out["rubric_applied"] is True
    assert out.get("rubric_parse_error") in (None, "")

    with connect(db) as conn:
        stale = conn.execute("SELECT value FROM meta WHERE key='rubric_stale';").fetchone()
    assert stale["value"] == "false"


def test_rate_limit_403_returns_readable_payload(monkeypatch, tmp_path: Path):
    db = tmp_path / "t.db"
    monkeypatch.setattr(sync_mod, "DEFAULT_DB_PATH", db)
    init_db(db)

    class RateLimitedResp:
        status_code = 403
        headers = {"X-RateLimit-Remaining": "0"}

        def raise_for_status(self):
            raise httpx.HTTPStatusError("403", request=None, response=self)

        def json(self):
            return {"message": "API rate limit exceeded"}

    def fake_get(self, url, params=None, headers=None):
        url = str(url)
        if "api.github.com" in url:
            return RateLimitedResp()
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(httpx.Client, "get", fake_get, raising=True)

    check = sync_mod.check_updates()
    assert check["rate_limited"] is True
    assert "GITHUB_TOKEN" in check["hint"]
    assert "60/h" in check["hint"]

    sync_out = sync_mod.sync_directory(dry_run=True)
    assert sync_out["rate_limited"] is True
    assert sync_out["applied"] is False
    assert "GITHUB_TOKEN" in sync_out["hint"]
    assert sync_out["added"] == 0


def test_sync_uses_at_most_one_api_call(monkeypatch, tmp_path: Path):
    db = tmp_path / "t.db"
    guides = tmp_path / "guides"
    monkeypatch.setattr(sync_mod, "DEFAULT_DB_PATH", db)
    monkeypatch.setattr(sync_mod, "guides_dir", lambda: guides)
    init_db(db)

    base = Path(__file__).parent / "fixtures" / "directory_small_base.md"
    api_urls: list[str] = []
    _install_github_mocks(
        monkeypatch,
        directory_md=base.read_text(encoding="utf-8"),
        api_call_counter=api_urls,
    )

    out = sync_mod.sync_directory(dry_run=False, full_diff=False)
    assert out["applied"] is True
    assert out.get("rate_limited") is False
    assert len(api_urls) <= 1
    assert all("api.github.com" in u for u in api_urls)
    assert all("/contents/" not in u for u in api_urls)


def test_git_blob_sha_matches_known_vector():
    # Empty blob SHA used by git
    assert sync_mod._git_blob_sha(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
