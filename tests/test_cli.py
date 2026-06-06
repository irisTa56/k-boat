"""Dispatch + JSON-shape + ordering-invariant tests for the CLI (TASK-032).

Network and Reminders.app are monkeypatched at the ``cli`` module boundary; the
seen-store and ``sites.toml`` are real tmp files (``state_dir`` fixture) so the
ordering invariants — snapshot-before-config, remind-then-record, snapshot the
exact healed URLs — are asserted against actual persisted state.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from feed_filter import cli
from feed_filter.canonical import CanonicalUrl, canonical_url
from feed_filter.config import db_path, sites_path
from feed_filter.discover import DiscoveryCandidate, DiscoveryRejection, DiscoveryResult
from feed_filter.feeds import Entry, EntryKind
from feed_filter.fetch import FetchError
from feed_filter.pipeline import GatherResult
from feed_filter.reminders import ReminderError
from feed_filter.seen import count, is_seen, open_db
from feed_filter.sites import SiteConfig, add_site, load_sites


def _no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out the real httpx client; monkeypatched core fns ignore it."""
    monkeypatch.setattr(cli, "build_client", lambda: contextlib.nullcontext(None))


def _entry(url: str, title: str | None = None, kind: EntryKind = "feed") -> Entry:
    return Entry(canonical_url(url), title, None, None, kind)


def _out(capsys: pytest.CaptureFixture[str]) -> Any:
    return json.loads(capsys.readouterr().out)


# --- discover -------------------------------------------------------------


def test_discover_emits_candidates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_client(monkeypatch)
    candidate = DiscoveryCandidate(
        feed_url="https://e.com/f.xml",
        feed_type="feed",
        index_url="",
        article_url_pattern="",
        sample_urls=("https://e.com/a",),
        entry_count=3,
    )
    monkeypatch.setattr(cli, "discover", lambda url, *, client: DiscoveryResult((candidate,), None))

    assert cli.main(["discover", "https://e.com"]) == 0
    out = _out(capsys)
    assert out["candidates"][0]["feed_url"] == "https://e.com/f.xml"
    assert out["candidates"][0]["sample_urls"] == ["https://e.com/a"]
    assert out["rejection"] is None


def test_discover_passes_rejection_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_client(monkeypatch)
    rejection = DiscoveryRejection("no_article_clusters", "point at the listing page")
    monkeypatch.setattr(cli, "discover", lambda url, *, client: DiscoveryResult((), rejection))

    assert cli.main(["discover", "https://e.com"]) == 0
    out = _out(capsys)
    assert out["candidates"] == []
    assert out["rejection"] == {
        "reason": "no_article_clusters",
        "message": "point at the listing page",
    }


def test_discover_transport_error_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_client(monkeypatch)

    def boom(url: str, *, client: object) -> DiscoveryResult:
        raise FetchError(url)

    monkeypatch.setattr(cli, "discover", boom)
    assert cli.main(["discover", "https://e.com"]) == 1
    assert "error:" in capsys.readouterr().err


# --- add-site -------------------------------------------------------------


def test_add_site_snapshots_before_writing_config(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_client(monkeypatch)
    entries = [_entry("https://e.com/posts/1"), _entry("https://e.com/posts/2")]
    monkeypatch.setattr(cli, "fetch_entries", lambda site, *, client: entries)

    events: list[str] = []
    real_snapshot = cli.snapshot
    real_add_site = cli.add_site

    def spy_snapshot(conn: sqlite3.Connection, site_id: str, urls: list[CanonicalUrl]) -> None:
        # REQ-002: the config file must not exist yet when the snapshot commits.
        assert not sites_path().exists()
        events.append("snapshot")
        real_snapshot(conn, site_id, urls)

    def spy_add_site(path: Path, site: SiteConfig) -> None:
        events.append("add_site")
        real_add_site(path, site)

    monkeypatch.setattr(cli, "snapshot", spy_snapshot)
    monkeypatch.setattr(cli, "add_site", spy_add_site)

    rc = cli.main(["add-site", "--id", "f1", "--name", "Feed", "--feed-url", "https://e.com/f.xml"])

    assert rc == 0
    assert events == ["snapshot", "add_site"]  # ordering enforced by the CLI
    out = _out(capsys)
    assert out == {"site_id": "f1", "kind": "feed", "snapshotted": 2}
    # The snapshot rows and the config entry both landed.
    assert [s.id for s in load_sites(sites_path())] == ["f1"]
    with contextlib.closing(open_db(db_path())) as conn:
        assert is_seen(conn, canonical_url("https://e.com/posts/1"))
        assert is_seen(conn, canonical_url("https://e.com/posts/2"))


def test_add_site_fetch_error_writes_no_config(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_client(monkeypatch)

    def boom(site: SiteConfig, *, client: object) -> list[Entry]:
        raise FetchError("https://e.com/f.xml")

    monkeypatch.setattr(cli, "fetch_entries", boom)
    rc = cli.main(["add-site", "--id", "f1", "--name", "Feed", "--feed-url", "https://e.com/f.xml"])

    assert rc == 1
    assert not sites_path().exists()  # config never written on a gather failure


def test_filesystem_error_surfaces_as_clean_exit(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # An OSError from a durable config write (disk full / permission / rename
    # failure) must surface as `error: …` + exit 1, not a traceback — the same
    # operational-failure principle that covers rem's absence (CON-004).
    _no_client(monkeypatch)
    monkeypatch.setattr(cli, "fetch_entries", lambda site, *, client: [])

    def boom(path: Path, site: SiteConfig) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(cli, "add_site", boom)
    rc = cli.main(["add-site", "--id", "f1", "--name", "Feed", "--feed-url", "https://e.com/f.xml"])
    assert rc == 1
    assert "error: disk full" in capsys.readouterr().err


# --- list-sites -----------------------------------------------------------


def test_list_sites_shape(state_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    add_site(sites_path(), SiteConfig(id="f1", name="Feed", feed_url="https://e.com/f.xml"))
    assert cli.main(["list-sites"]) == 0
    out = _out(capsys)
    assert out == [
        {
            "id": "f1",
            "name": "Feed",
            "kind": "feed",
            "feed_url": "https://e.com/f.xml",
            "index_url": None,
            "article_url_pattern": None,
            "selection": None,
        }
    ]


# --- new-entries ----------------------------------------------------------


def test_new_entries_round_robin_truncation_leaves_later_sites_unseen(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_client(monkeypatch)
    add_site(sites_path(), SiteConfig(id="a", name="A", feed_url="https://a.com/f.xml"))
    add_site(sites_path(), SiteConfig(id="b", name="B", feed_url="https://b.com/f.xml"))

    def fake_gather(conn: sqlite3.Connection, site: SiteConfig, *, client: object) -> GatherResult:
        if site.id == "a":
            ents = [_entry(f"https://a.com/{i}") for i in range(3)]
        else:
            ents = [_entry("https://b.com/0")]
        return GatherResult(entries=ents, index_matches=len(ents), zero_links=False, error=None)

    monkeypatch.setattr(cli, "gather_new", fake_gather)

    rc = cli.main(["new-entries", "--global-cap", "3"])
    assert rc == 0
    out = _out(capsys)
    urls = [e["url"] for e in out["entries"]]
    # Round-robin (REQ-010): site b's single entry is reached before site a's
    # tail is exhausted, so a noisy site a can't starve b. a/2 is truncated.
    assert urls == ["https://a.com/0", "https://b.com/0", "https://a.com/1"]
    assert "https://a.com/2" not in urls
    assert out["sites"] == [
        {"site_id": "a", "zero_links": False, "error": None},
        {"site_id": "b", "zero_links": False, "error": None},
    ]
    # Nothing is recorded by new-entries; the truncated entry reappears next run.
    with contextlib.closing(open_db(db_path())) as conn:
        assert count(conn) == 0


def test_new_entries_unknown_site_id_exits_nonzero(
    state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["new-entries", "--site-id", "nope"]) == 1
    assert "error:" in capsys.readouterr().err


# --- remind / mark-seen ---------------------------------------------------


def test_remind_adds_then_records_atomically(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events: list[str] = []
    real_record = cli.record

    def fake_add(*_a: object, **_k: object) -> str:
        events.append("add")
        return "RID-1"

    def spy_record(
        conn: sqlite3.Connection,
        url: CanonicalUrl,
        site_id: str,
        title: str | None,
        kept: int | None,
    ) -> None:
        events.append("record")
        real_record(conn, url, site_id, title, kept)

    monkeypatch.setattr(cli, "add_reminder", fake_add)
    monkeypatch.setattr(cli, "record", spy_record)

    rc = cli.main(
        ["remind", "--site-id", "f1", "--url", "https://e.com/a", "--title", "T", "--notes", "N"]
    )
    assert rc == 0
    assert events == ["add", "record"]  # rem add first, seen record second (REQ-009)
    out = _out(capsys)
    assert out == {"id": "RID-1", "url": "https://e.com/a", "kept": True}
    with contextlib.closing(open_db(db_path())) as conn:
        assert is_seen(conn, canonical_url("https://e.com/a"))


def test_remind_does_not_record_when_add_raises(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> str:
        raise ReminderError(["rem"], 1, "list not found")

    monkeypatch.setattr(cli, "add_reminder", boom)
    rc = cli.main(
        ["remind", "--site-id", "f1", "--url", "https://e.com/a", "--title", "T", "--notes", "N"]
    )
    assert rc == 1
    # The reminder failed, so the entry must stay unseen and retry next run.
    with contextlib.closing(open_db(db_path())) as conn:
        assert not is_seen(conn, canonical_url("https://e.com/a"))
        assert count(conn) == 0


def test_mark_seen_records_drop(state_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(["mark-seen", "--site-id", "f1", "--url", "https://e.com/a", "--title", "T"])
    assert rc == 0
    out = _out(capsys)
    assert out == {"url": "https://e.com/a", "kept": False}
    with contextlib.closing(open_db(db_path())) as conn:
        assert is_seen(conn, canonical_url("https://e.com/a"))
        row = conn.execute(
            "SELECT kept FROM seen WHERE canonical_url = ?", ("https://e.com/a",)
        ).fetchone()
        assert row[0] == 0


# --- heal-site ------------------------------------------------------------


def test_heal_site_snapshots_exactly_the_new_pattern_matches(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_client(monkeypatch)
    add_site(
        sites_path(),
        SiteConfig(
            id="s1",
            name="Scrape",
            index_url="https://e.com/blog",
            article_url_pattern=r"^/old/[^/]+/?$",
        ),
    )

    new_pattern = r"^/posts/[^/]+/?$"
    healed = [
        _entry("https://e.com/posts/a", kind="scrape"),
        _entry("https://e.com/posts/b", kind="scrape"),
    ]
    captured: dict[str, object] = {}

    def fake_fetch_entries(site: SiteConfig, *, client: object) -> list[Entry]:
        # The re-scrape sees the NEW pattern (applied via in-memory replace)
        # even though config is written last.
        captured["pattern"] = site.article_url_pattern
        return healed

    monkeypatch.setattr(cli, "fetch_entries", fake_fetch_entries)
    monkeypatch.setattr(cli, "report", lambda message, **_k: "ALERT-1")

    rc = cli.main(["heal-site", "--site-id", "s1", "--pattern", new_pattern])
    assert rc == 0
    assert captured["pattern"] == new_pattern
    out = _out(capsys)
    assert out == {
        "site_id": "s1",
        "pattern": new_pattern,
        "snapshotted": 2,
        "reminder_id": "ALERT-1",
    }
    # The pattern was rewritten in config...
    assert load_sites(sites_path())[0].article_url_pattern == new_pattern
    # ...and the snapshot set is EXACTLY the fetched matches, nothing more.
    with contextlib.closing(open_db(db_path())) as conn:
        rows = {r[0] for r in conn.execute("SELECT canonical_url FROM seen")}
    assert rows == {"https://e.com/posts/a", "https://e.com/posts/b"}


def test_heal_site_fetch_failure_leaves_config_and_seen_untouched(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The flood guard (REQ-002/REQ-006): if the re-scrape fails, config must NOT
    # be left carrying the new pattern with no snapshot under it. Snapshot-first /
    # config-last makes a fetch failure a clean no-op modulo the still-broken
    # pattern (which simply re-triggers heal next run).
    _no_client(monkeypatch)
    old_pattern = r"^/old/[^/]+/?$"
    add_site(
        sites_path(),
        SiteConfig(
            id="s1", name="Scrape", index_url="https://e.com/blog", article_url_pattern=old_pattern
        ),
    )

    def boom(site: SiteConfig, *, client: object) -> list[Entry]:
        raise FetchError("https://e.com/blog")

    monkeypatch.setattr(cli, "fetch_entries", boom)
    # report must never be reached on a fetch failure.
    monkeypatch.setattr(cli, "report", lambda *a, **k: pytest.fail("report called on failed heal"))

    rc = cli.main(["heal-site", "--site-id", "s1", "--pattern", r"^/posts/[^/]+/?$"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err
    # Config still on the OLD pattern; no back-catalog snapshotted.
    assert load_sites(sites_path())[0].article_url_pattern == old_pattern
    with contextlib.closing(open_db(db_path())) as conn:
        assert count(conn) == 0


def test_heal_site_rejects_feed_site(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Healing a feed site is meaningless and would corrupt the exactly-one
    # invariant; reject before any fetch/snapshot side effect.
    _no_client(monkeypatch)
    add_site(sites_path(), SiteConfig(id="f1", name="Feed", feed_url="https://e.com/f.xml"))
    monkeypatch.setattr(cli, "fetch_entries", lambda *a, **k: pytest.fail("fetched a feed site"))

    assert cli.main(["heal-site", "--site-id", "f1", "--pattern", "^/x/"]) == 1
    assert "scrape sites only" in capsys.readouterr().err


def test_heal_site_unknown_id_exits_nonzero(
    state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_site(sites_path(), SiteConfig(id="s1", name="S", feed_url="https://e.com/f.xml"))
    assert cli.main(["heal-site", "--site-id", "nope", "--pattern", "^/x/"]) == 1
    assert "error:" in capsys.readouterr().err


# --- dispatch root --------------------------------------------------------


def test_no_subcommand_errors() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main([])
    assert excinfo.value.code == 2  # argparse: required subcommand missing
