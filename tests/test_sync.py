from pathlib import Path

import httpx

from gamefunds import sync as sync_mod
from gamefunds.db import connect, init_db, upsert_entities
from gamefunds.parser import parse_directory_markdown


def test_check_updates_shape(monkeypatch, tmp_path: Path):
    # isolate DB
    db = tmp_path / "t.db"
    monkeypatch.setattr(sync_mod, "DEFAULT_DB_PATH", db)

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return [
                {"sha": "abc", "commit": {"message": "msg", "committer": {"date": "2026-01-01T00:00:00Z"}}}
            ]

    def fake_get(self, url, params=None, headers=None):
        return FakeResp()

    monkeypatch.setattr(httpx.Client, "get", fake_get, raising=True)

    out = sync_mod.check_updates()
    assert set(out.keys()) >= {
        "has_update",
        "current_sha",
        "last_synced_sha",
        "last_synced_at",
        "commit_message",
        "commit_date",
    }


def test_sync_directory_dry_run_diff(monkeypatch, tmp_path: Path):
    db = tmp_path / "t.db"
    monkeypatch.setattr(sync_mod, "DEFAULT_DB_PATH", db)
    init_db(db)

    base = Path(__file__).parent / "fixtures" / "directory_small_base.md"
    modified = Path(__file__).parent / "fixtures" / "directory_small_modified.md"

    base_rows = parse_directory_markdown(base.read_text(encoding="utf-8"))
    upsert_entities(base_rows, db_path=db)

    class FakeInfo:
        def __init__(self):
            self.data = {
                "has_update": True,
                "current_sha": "sha1",
                "last_synced_sha": None,
                "last_synced_at": None,
                "commit_message": "msg",
                "commit_date": "2026-01-01T00:00:00Z",
            }

        def __call__(self):
            return dict(self.data)

    monkeypatch.setattr(sync_mod, "check_updates", FakeInfo(), raising=True)

    class FakeTextResp:
        def raise_for_status(self):
            return None

        @property
        def text(self):
            return modified.read_text(encoding="utf-8")

    def fake_get(self, url, params=None, headers=None):
        return FakeTextResp()

    monkeypatch.setattr(httpx.Client, "get", fake_get, raising=True)

    out = sync_mod.sync_directory(dry_run=True, full_diff=False)
    assert out["applied"] is False
    assert out["added"] == 1
    assert out["removed"] == 0
    assert out["changed"] >= 1

    # Ensure DB still has original country for alpha-pub
    with connect(db) as conn:
        r = conn.execute("SELECT country, budget_tier, comm_rating FROM entities WHERE slug='alpha-pub'").fetchone()
    assert r["country"] == "Poland"

