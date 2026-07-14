from pathlib import Path

import httpx

from gamefunds import sync as sync_mod
from gamefunds.db import connect, init_db, upsert_entities
from gamefunds.guides import UPSTREAM_GUIDE_FILES, guide_sha_meta_key, guides_dir
from gamefunds.parser import parse_directory_markdown
from gamefunds.rubrics import sync_rubrics_from_tutorial

GUIDE_SHAS = {
    "Definitions.md": "sha-def",
    "DefinitionsPL.md": "sha-def-pl",
    "FundingTypes.md": "sha-ft",
    "FundingTypesPL.md": "sha-ft-pl",
    "PitchDeckTutorial.md": "sha-pitch",
    "PitchDeckTutorialPL.md": "sha-pitch-pl",
}


def _install_github_mocks(monkeypatch, *, directory_md: str, guide_contents: dict[str, str] | None = None):
    guide_contents = guide_contents or {}

    class FakeCommitResp:
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

    class FakeContentsResp:
        def __init__(self, sha: str):
            self._sha = sha

        def raise_for_status(self):
            return None

        def json(self):
            return {"sha": self._sha}

    class FakeTextResp:
        def __init__(self, text: str):
            self._text = text

        def raise_for_status(self):
            return None

        @property
        def text(self):
            return self._text

    def fake_get(self, url, params=None, headers=None):
        url = str(url)
        if url.endswith("/commits"):
            return FakeCommitResp()
        if "/contents/" in url:
            name = url.rsplit("/", 1)[-1]
            return FakeContentsResp(GUIDE_SHAS[name])
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
    guide_fixtures = {
        "Definitions.md": "# Definitions EN\n",
        "DefinitionsPL.md": "# Definitions PL\n",
        "FundingTypes.md": "# Funding EN\n",
        "FundingTypesPL.md": "# Funding PL\n",
        "PitchDeckTutorial.md": "# Pitch EN\n",
        "PitchDeckTutorialPL.md": "# Pitch PL\n",
    }

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
    assert stored["value"] == GUIDE_SHAS["DefinitionsPL.md"]


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

    guides.mkdir()
    for name in GUIDE_SHAS:
        content = tutorial.read_text(encoding="utf-8") if name == "PitchDeckTutorial.md" else f"# {name}\n"
        (guides / name).write_text(content, encoding="utf-8")

    with connect(db) as conn:
        for name, sha in GUIDE_SHAS.items():
            conn.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?);",
                (guide_sha_meta_key(name), sha),
            )
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('rubric_stale', 'true');")
        conn.commit()

    sync_rubrics_from_tutorial(tutorial.read_text(encoding="utf-8"), source_sha=GUIDE_SHAS["PitchDeckTutorial.md"], db_path=db)
    with connect(db) as conn:
        conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('rubric_stale', 'true');")
        conn.commit()

    _install_github_mocks(monkeypatch, directory_md=base.read_text(encoding="utf-8"))

    out = sync_mod.sync_directory(dry_run=False, full_diff=False)
    assert out["rubric_applied"] is True
    assert out.get("rubric_parse_error") in (None, "")

    with connect(db) as conn:
        stale = conn.execute("SELECT value FROM meta WHERE key='rubric_stale';").fetchone()
    assert stale["value"] == "false"
