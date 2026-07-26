"""Dispatch + JSON-shape + ordering-invariant tests for the CLI.

Network is monkeypatched at the ``cli`` module boundary; the
seen-store, ``sites.toml``, and the output vault are real tmp files (``state_dir`` fixture) so the
ordering invariants — snapshot-before-config, remind-then-record, snapshot the
exact healed URLs, forum record-then-poll — are asserted against actual persisted
state.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from feed_filter import browser, cli
from feed_filter.browser import BrowserFetchError, MissingPlaywrightError
from feed_filter.canonical import CanonicalUrl, canonical_url
from feed_filter.config import (
    DEFAULT_QUERY_RESULTS,
    SUMMARY_PREVIEW_CHARS,
    db_path,
    sites_path,
    vault_path,
)
from feed_filter.discover import DiscoveryCandidate, DiscoveryRejection, DiscoveryResult
from feed_filter.exa import ExaError, QueryHit, QueryOutcome
from feed_filter.feeds import Entry, EntryKind
from feed_filter.fetch import FetchError
from feed_filter.forum_pipeline import (
    AdmitResult,
    GatherForumResult,
    PolledTopic,
    RuleACandidate,
    RuleBCandidate,
    TriggerPost,
)
from feed_filter.pipeline import FetchOutcome
from feed_filter.seen import count, is_seen, open_db
from feed_filter.sites import SiteConfig, add_site, load_sites
from feed_filter.vault import VaultError
from kboat.frontmatter import Value, parse_frontmatter
from kboat.naming import url_slug


def _feed_note_fm(url: str) -> dict[str, Value]:
    """Parse the frontmatter of the Feeds note written for ``url``."""
    note = vault_path() / "Feeds" / f"{url_slug(str(canonical_url(url)))}.md"
    return parse_frontmatter(note.read_text(encoding="utf-8"))


def _no_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub out the real httpx client; monkeypatched core fns ignore it."""
    monkeypatch.setattr(cli, "build_client", lambda: contextlib.nullcontext(None))


def _entry(url: str, title: str | None = None, kind: EntryKind = "feed") -> Entry:
    return Entry(canonical_url(url), title, None, None, kind)


def _out(capsys: pytest.CaptureFixture[str]) -> Any:
    return json.loads(capsys.readouterr().out)


def _remind_args(command: str) -> list[str]:
    """What a note-writing subcommand requires, for a test about another flag."""
    topic = ["--topic-id", "1234"] if command == "forum-remind" else []
    return ["--site-id", "f1", "--title", "T", *topic]


# --- discover -------------------------------------------------------------


def test_discover_emits_candidates(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_client(monkeypatch)
    candidate = DiscoveryCandidate(
        feed_url="https://e.example.com/f.xml",
        feed_type="feed",
        index_url="",
        article_url_pattern="",
        sample_urls=("https://e.example.com/a",),
        entry_count=3,
    )
    monkeypatch.setattr(cli, "discover", lambda url, *, client: DiscoveryResult((candidate,), None))

    assert cli.main(["discover", "https://e.example.com"]) == 0
    out = _out(capsys)
    assert out["candidates"][0]["feed_url"] == "https://e.example.com/f.xml"
    assert out["candidates"][0]["sample_urls"] == ["https://e.example.com/a"]
    assert out["rejection"] is None


def test_discover_passes_rejection_through(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_client(monkeypatch)
    rejection = DiscoveryRejection("no_article_clusters", "point at the listing page")
    monkeypatch.setattr(cli, "discover", lambda url, *, client: DiscoveryResult((), rejection))

    assert cli.main(["discover", "https://e.example.com"]) == 0
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
    assert cli.main(["discover", "https://e.example.com"]) == 1
    assert "error:" in capsys.readouterr().err


# --- add-site -------------------------------------------------------------


def test_add_site_snapshots_before_writing_config(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_client(monkeypatch)
    entries = [_entry("https://e.example.com/posts/1"), _entry("https://e.example.com/posts/2")]
    monkeypatch.setattr(cli, "fetch_entries", lambda site, *, client: entries)

    events: list[str] = []
    real_snapshot = cli.snapshot
    real_add_site = cli.add_site

    def spy_snapshot(conn: sqlite3.Connection, site_id: str, urls: list[CanonicalUrl]) -> None:
        # The config file must not exist yet when the snapshot commits.
        assert not sites_path().exists()
        events.append("snapshot")
        real_snapshot(conn, site_id, urls)

    def spy_add_site(path: Path, site: SiteConfig) -> None:
        events.append("add_site")
        real_add_site(path, site)

    monkeypatch.setattr(cli, "snapshot", spy_snapshot)
    monkeypatch.setattr(cli, "add_site", spy_add_site)

    rc = cli.main(
        ["add-site", "--id", "f1", "--name", "Feed", "--feed-url", "https://e.example.com/f.xml"]
    )

    assert rc == 0
    assert events == ["snapshot", "add_site"]  # ordering enforced by the CLI
    out = _out(capsys)
    assert out == {"site_id": "f1", "kind": "feed", "snapshotted": 2}
    # The snapshot rows and the config entry both landed.
    assert [s.id for s in load_sites(sites_path())] == ["f1"]
    with contextlib.closing(open_db(db_path())) as conn:
        assert is_seen(conn, canonical_url("https://e.example.com/posts/1"))
        assert is_seen(conn, canonical_url("https://e.example.com/posts/2"))


def test_add_site_fetch_error_writes_no_config(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _no_client(monkeypatch)

    def boom(site: SiteConfig, *, client: object) -> list[Entry]:
        raise FetchError("https://e.example.com/f.xml")

    monkeypatch.setattr(cli, "fetch_entries", boom)
    rc = cli.main(
        ["add-site", "--id", "f1", "--name", "Feed", "--feed-url", "https://e.example.com/f.xml"]
    )

    assert rc == 1
    assert not sites_path().exists()  # config never written on a gather failure


def test_add_site_rejects_an_uncompilable_pattern_before_fetching(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A bad pattern fails registration up front, not as a traceback from the snapshot.

    The cold-start snapshot scrapes the index under the new pattern, so it compiles
    it before ``add_site``'s own guard is ever reached — an unguarded CLI would exit
    on a raw ``re.error`` instead of the ``error: …`` contract.
    """
    _no_client(monkeypatch)
    monkeypatch.setattr(cli, "fetch_entries", lambda *a, **k: pytest.fail("fetched before check"))

    rc = cli.main(
        [
            "add-site",
            "--id",
            "s1",
            "--name",
            "S",
            "--index-url",
            "https://e.example.com/blog",
            "--article-url-pattern",
            "/a(",
        ]
    )

    assert rc == 1
    assert "not a valid regex" in capsys.readouterr().err
    assert not sites_path().exists()  # nothing registered


def test_filesystem_error_surfaces_as_clean_exit(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # An OSError from a durable config write (disk full / permission / rename
    # failure) must surface as `error: …` + exit 1, not a traceback — the same
    # operational-failure principle that covers a vault write failure.
    _no_client(monkeypatch)
    monkeypatch.setattr(cli, "fetch_entries", lambda site, *, client: [])

    def boom(path: Path, site: SiteConfig) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(cli, "add_site", boom)
    rc = cli.main(
        ["add-site", "--id", "f1", "--name", "Feed", "--feed-url", "https://e.example.com/f.xml"]
    )
    assert rc == 1
    assert "error: disk full" in capsys.readouterr().err


def test_seen_store_error_surfaces_as_clean_exit(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A seen-store failure reports like any other operational one, not as a traceback.

    The case this exists for is a lock timeout: two processes opening the store at
    once serialize on the migration's ``BEGIN IMMEDIATE``, and the loser can exceed
    the busy timeout. Every skill parses the ``error: …`` + exit 1 contract, so a
    traceback there would break the run summary rather than report a retryable run.
    """
    _no_client(monkeypatch)
    add_site(sites_path(), SiteConfig(id="a", name="A", feed_url="https://a.example.com/f.xml"))

    def boom(path: Path) -> None:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(cli, "open_db", boom)
    capsys.readouterr()  # drop the add-site output

    assert cli.main(["new-entries"]) == 1
    assert "error: database is locked" in capsys.readouterr().err


# --- list-sites -----------------------------------------------------------


def test_list_sites_shape(state_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    add_site(sites_path(), SiteConfig(id="f1", name="Feed", feed_url="https://e.example.com/f.xml"))
    assert cli.main(["list-sites"]) == 0
    out = _out(capsys)
    assert out == [
        {
            "id": "f1",
            "name": "Feed",
            "kind": "feed",
            "feed_url": "https://e.example.com/f.xml",
            "index_url": None,
            "article_url_pattern": None,
            "forum_url": None,
            "forum_subject": None,
            "like_threshold": None,
            "interest_like_threshold": None,
            "daily_watch_count": None,
            "weekly_watch_count": None,
            "poll_offsets_days": None,
            "selection": None,
            "requires_browser": False,
            "enabled": True,
        }
    ]


def test_list_sites_projects_forum_config(
    state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A forum site must be faithfully projected so the forum-run skill can read
    # forum_subject for the Rule-A native-subject exclusion via the CLI
    # contract, never by reading sites.toml directly.
    add_site(
        sites_path(),
        SiteConfig(
            id="ef",
            name="Elixir Forum",
            forum_url="https://elixirforum.com",
            forum_subject="Elixir",
            like_threshold=8,
            poll_offsets_days=(0, 2, 9),
        ),
    )
    assert cli.main(["list-sites"]) == 0
    row = _out(capsys)[0]
    assert row["kind"] == "forum"
    assert row["forum_url"] == "https://elixirforum.com"
    assert row["forum_subject"] == "Elixir"
    assert row["like_threshold"] == 8
    assert row["poll_offsets_days"] == [0, 2, 9]
    # Unset tuning fields project as None (the config default applies at use time).
    assert row["interest_like_threshold"] is None
    assert row["daily_watch_count"] is None
    assert row["weekly_watch_count"] is None


def test_list_sites_surfaces_requires_browser(
    state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # list-sites is the JSON projection of the full model; a browser-backed site
    # must be distinguishable from an httpx one in the inventory the skills read.
    add_site(
        sites_path(),
        SiteConfig(
            id="js", name="JS", feed_url="https://e.example.com/f.xml", requires_browser=True
        ),
    )
    assert cli.main(["list-sites"]) == 0
    assert _out(capsys)[0]["requires_browser"] is True


# --- enable / disable -----------------------------------------------------


def test_disable_then_enable_site(state_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    add_site(sites_path(), SiteConfig(id="s", name="S", feed_url="https://e.example.com/f.xml"))

    assert cli.main(["disable-site", "--site-id", "s"]) == 0
    assert _out(capsys) == {"site_id": "s", "enabled": False}
    assert load_sites(sites_path())[0].enabled is False
    assert "enabled = false" in sites_path().read_text(encoding="utf-8")

    assert cli.main(["enable-site", "--site-id", "s"]) == 0
    assert _out(capsys) == {"site_id": "s", "enabled": True}
    assert load_sites(sites_path())[0].enabled is True
    assert "enabled" not in sites_path().read_text(encoding="utf-8")


def test_disable_site_unknown_id_exits_nonzero(
    state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_site(sites_path(), SiteConfig(id="s", name="S", feed_url="https://e.example.com/f.xml"))
    assert cli.main(["disable-site", "--site-id", "nope"]) == 1
    assert "error:" in capsys.readouterr().err


def test_new_entries_skips_disabled_site(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_client(monkeypatch)
    add_site(sites_path(), SiteConfig(id="on", name="On", feed_url="https://a.example.com/f.xml"))
    add_site(sites_path(), SiteConfig(id="off", name="Off", feed_url="https://b.example.com/f.xml"))
    assert cli.main(["disable-site", "--site-id", "off"]) == 0
    capsys.readouterr()

    gathered_ids: list[str] = []

    def fake_fetch(site: SiteConfig, *, client: object) -> FetchOutcome:
        gathered_ids.append(site.id)
        return FetchOutcome(entries=[], error=None)

    monkeypatch.setattr(cli, "fetch_site", fake_fetch)

    assert cli.main(["new-entries"]) == 0
    out = _out(capsys)
    assert gathered_ids == ["on"]  # the disabled site is never gathered
    assert [s["site_id"] for s in out["sites"]] == ["on"]  # and absent from the status


# --- new-entries ----------------------------------------------------------


def test_new_entries_round_robin_truncation_leaves_later_sites_unseen(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_client(monkeypatch)
    add_site(sites_path(), SiteConfig(id="a", name="A", feed_url="https://a.example.com/f.xml"))
    add_site(sites_path(), SiteConfig(id="b", name="B", feed_url="https://b.example.com/f.xml"))

    def fake_fetch(site: SiteConfig, *, client: object) -> FetchOutcome:
        if site.id == "a":
            ents = [_entry(f"https://a.example.com/{i}") for i in range(3)]
        else:
            ents = [_entry("https://b.example.com/0")]
        return FetchOutcome(entries=ents, error=None)

    monkeypatch.setattr(cli, "fetch_site", fake_fetch)

    rc = cli.main(["new-entries", "--global-cap", "3"])
    assert rc == 0
    out = _out(capsys)
    urls = [e["url"] for e in out["entries"]]
    # Round-robin: site b's single entry is reached before site a's
    # tail is exhausted, so a noisy site a can't starve b. a/2 is truncated.
    assert urls == ["https://a.example.com/0", "https://b.example.com/0", "https://a.example.com/1"]
    assert "https://a.example.com/2" not in urls
    assert out["sites"] == [
        {
            "site_id": "a",
            "zero_links": False,
            "error": None,
            "unexpected_error": False,
            "consecutive_failures": 0,
            "persistent": False,
        },
        {
            "site_id": "b",
            "zero_links": False,
            "error": None,
            "unexpected_error": False,
            "consecutive_failures": 0,
            "persistent": False,
        },
    ]
    # Nothing is recorded by new-entries; the truncated entry reappears next run.
    with contextlib.closing(open_db(db_path())) as conn:
        assert count(conn) == 0


def test_new_entries_caches_full_body_and_emits_only_preview(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The full feed body is stashed in the body cache (pulled later via
    ``entry-body``); stdout carries only a bounded preview, so the run orchestrator
    never sees the body."""
    _no_client(monkeypatch)
    add_site(sites_path(), SiteConfig(id="a", name="A", feed_url="https://a.example.com/f.xml"))
    body = "Full article body. " + "x" * 1000  # longer than SUMMARY_PREVIEW_CHARS
    url = "https://a.example.com/deep"

    def fake_fetch(site: SiteConfig, *, client: object) -> FetchOutcome:
        ents = [Entry(canonical_url(url), "Deep", body, None, "feed")]
        return FetchOutcome(entries=ents, error=None)

    monkeypatch.setattr(cli, "fetch_site", fake_fetch)

    assert cli.main(["new-entries"]) == 0
    entry = _out(capsys)["entries"][0]
    # stdout summary is a bounded preview (with an ellipsis), not the full body.
    assert entry["summary"] == body[:SUMMARY_PREVIEW_CHARS] + "…"
    assert len(entry["summary"]) == SUMMARY_PREVIEW_CHARS + 1

    # entry-body pulls the full body back from the cache for the judge.
    assert cli.main(["entry-body", "--url", url]) == 0
    assert _out(capsys) == {"url": url, "body": body}


def test_entry_body_cache_miss_returns_null(
    state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A url not cached by ``new-entries`` (a scrape entry, or a stale/interrupted
    run) returns ``body: null``, so the judge falls back to a WebFetch."""
    url = "https://x.example.com/gone"
    assert cli.main(["entry-body", "--url", url]) == 0
    assert _out(capsys) == {"url": url, "body": None}


def test_entry_body_canonicalizes_the_lookup_url(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A non-canonical variant of a cached entry's URL still hits the cache.

    The cache is keyed by the canonical URL ``new-entries`` emitted, so a caller
    holding a tracking-tagged or fragment-bearing variant of it — the same page —
    must be normalized to that key, matching ``remind`` and ``mark-seen``, rather
    than reported as a miss and re-fetched in full.
    """
    _no_client(monkeypatch)
    add_site(sites_path(), SiteConfig(id="a", name="A", feed_url="https://a.example.com/f.xml"))
    body = "Full article body."

    def fake_fetch(site: SiteConfig, *, client: object) -> FetchOutcome:
        url = canonical_url("https://a.example.com/deep")
        return FetchOutcome(entries=[Entry(url, "Deep", body, None, "feed")], error=None)

    monkeypatch.setattr(cli, "fetch_site", fake_fetch)

    assert cli.main(["new-entries"]) == 0
    cached = _out(capsys)["entries"][0]["url"]

    variant = "https://a.example.com/deep?utm_source=rss#intro"
    assert str(canonical_url(variant)) == cached, "premise: the variant is the same page"
    assert cli.main(["entry-body", "--url", variant]) == 0
    # The emitted url is the canonical key the body was found under, not the input.
    assert _out(capsys) == {"url": cached, "body": body}


def test_new_entries_caches_duplicate_canonical_url_without_crashing(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Two feeds carrying the same article emit two entries with one canonical URL;
    the body cache must tolerate the duplicate (keeping the longer body; equal length
    keeps the first), not wedge the run on the PRIMARY KEY — nothing is recorded seen
    during ``new-entries`` to collapse them, and the never-lost bias keeps both on
    stdout (dedupe is downstream)."""
    _no_client(monkeypatch)
    add_site(sites_path(), SiteConfig(id="a", name="A", feed_url="https://a.example.com/f.xml"))
    add_site(sites_path(), SiteConfig(id="b", name="B", feed_url="https://b.example.com/f.xml"))

    def fake_fetch(site: SiteConfig, *, client: object) -> FetchOutcome:
        ents = [
            Entry(
                canonical_url("https://shared.example.com/post"),
                "Shared",
                f"body {site.id}",
                None,
                "feed",
            )
        ]
        return FetchOutcome(entries=ents, error=None)

    monkeypatch.setattr(cli, "fetch_site", fake_fetch)

    assert cli.main(["new-entries"]) == 0
    urls = [e["url"] for e in _out(capsys)["entries"]]
    assert len(urls) == 2 and len(set(urls)) == 1  # both emitted, one canonical URL

    assert cli.main(["entry-body", "--url", urls[0]]) == 0
    assert _out(capsys)["body"] in {"body a", "body b"}


def test_new_entries_does_not_cache_capped_entries(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Only the emitted (post global-cap) set is cached; an entry truncated by the
    cap has no cached body, so ``entry-body`` misses and the judge would WebFetch."""
    _no_client(monkeypatch)
    add_site(sites_path(), SiteConfig(id="a", name="A", feed_url="https://a.example.com/f.xml"))

    def fake_fetch(site: SiteConfig, *, client: object) -> FetchOutcome:
        ents = [
            Entry(canonical_url(f"https://a.example.com/{i}"), f"T{i}", f"body {i}", None, "feed")
            for i in range(3)
        ]
        return FetchOutcome(entries=ents, error=None)

    monkeypatch.setattr(cli, "fetch_site", fake_fetch)

    assert cli.main(["new-entries", "--global-cap", "2"]) == 0
    emitted = [e["url"] for e in _out(capsys)["entries"]]
    assert len(emitted) == 2
    dropped = str(canonical_url("https://a.example.com/2"))
    assert dropped not in emitted

    assert cli.main(["entry-body", "--url", dropped]) == 0
    assert _out(capsys)["body"] is None


def test_new_entries_zero_links_does_not_increment_failure_counter(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A zero_links scrape is a broken pattern (healed by heal-site), not an outage,
    so it must leave the site-health counter at 0 — persistence tracks
    unreachability, not broken patterns. Only a non-None gather error increments.
    """
    _no_client(monkeypatch)
    add_site(
        sites_path(),
        SiteConfig(
            id="s",
            name="S",
            index_url="https://s.example.com/",
            article_url_pattern="/post/*",
        ),
    )

    # A scrape site whose live index yields no pattern match: fetch returns zero
    # entries, so filter_gathered derives zero_links (index_matches == 0) itself.
    def fake_fetch(site: SiteConfig, *, client: object) -> FetchOutcome:
        return FetchOutcome(entries=[], error=None)

    monkeypatch.setattr(cli, "fetch_site", fake_fetch)

    assert cli.main(["new-entries"]) == 0
    status = _out(capsys)["sites"][0]
    assert status["zero_links"] is True
    assert status["consecutive_failures"] == 0
    assert status["persistent"] is False


def test_new_entries_fetches_hosts_concurrently_but_serializes_same_host(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bounded per-host concurrency: distinct hosts fetch in parallel, but two sites
    on one host never fetch concurrently (crawler politeness).

    Two hosts each carry two sites. A ``Barrier(2)`` rendezvous inside the fake
    fetch forces the two *hosts* to be in flight simultaneously — a serial gather
    would deadlock on the first wait and hit the timeout, so the run completing at
    all proves cross-host concurrency. A per-host in-flight counter proves the two
    same-host sites are fetched one at a time (max concurrency per host == 1).
    """
    for host in ("a", "b"):
        for n in (1, 2):
            add_site(
                sites_path(),
                SiteConfig(
                    id=f"{host}{n}",
                    name=f"{host}{n}",
                    feed_url=f"https://{host}.example.com/{n}.xml",
                ),
            )
    _no_client(monkeypatch)

    barrier = threading.Barrier(2, timeout=5)  # two hosts must rendezvous per wave
    lock = threading.Lock()
    active: dict[str, int] = {}
    peak: dict[str, int] = {}

    def fake_fetch(site: SiteConfig, *, client: object) -> FetchOutcome:
        host = urlsplit(site.feed_url or "").netloc
        with lock:
            active[host] = active.get(host, 0) + 1
            peak[host] = max(peak.get(host, 0), active[host])
        barrier.wait()  # blocks until the other host's worker also arrives
        with lock:
            active[host] -= 1
        return FetchOutcome(entries=[], error=None)

    monkeypatch.setattr(cli, "fetch_site", fake_fetch)

    assert cli.main(["new-entries"]) == 0
    # Every host was reached; none ever had two simultaneous in-flight fetches.
    assert set(peak) == {"a.example.com", "b.example.com"}
    assert max(peak.values()) == 1


def test_new_entries_unexpected_worker_exception_is_isolated_to_its_site(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An *unexpected* (non-``FetchError``) exception costs only its own site.

    It is absorbed into that site's ``error`` exactly as a fetch failure is, so
    every other site's entries still reach stdout — a bug on one of ~80 sites must
    not discard the whole gather. The failure stays visible (``sites[].error``, and
    the site-health counter it increments) and records nothing seen, so the next
    run retries it.

    ``b1`` and ``b2`` share a host, so they run in one worker: the isolation has to
    be per site, not per host group, or ``b2`` would be lost with ``b1``. ``c1``
    fails the classified way (as ``fetch_site`` absorbs a ``FetchError``) so the two
    kinds are asserted side by side: only the unclassified one is flagged, which is
    what lets the run summary recommend a site remedy for one and a bug report for
    the other.
    """
    hosts = {"a": ["a1"], "b": ["b1", "b2"], "c": ["c1"]}
    for host, ids in hosts.items():
        for site_id in ids:
            add_site(
                sites_path(),
                SiteConfig(
                    id=site_id,
                    name=site_id,
                    feed_url=f"https://{host}.example.com/{site_id}.xml",
                ),
            )
    _no_client(monkeypatch)

    def boom(site: SiteConfig, *, client: object) -> FetchOutcome:
        if site.id == "b1":
            raise RuntimeError("bug in a worker")
        if site.id == "c1":
            return FetchOutcome(entries=[], error="fetch of 'https://c…' failed")
        return FetchOutcome(entries=[_entry(f"https://e.example.com/{site.id}")], error=None)

    monkeypatch.setattr(cli, "fetch_site", boom)
    capsys.readouterr()  # drop the add-site output

    assert cli.main(["new-entries"]) == 0
    out = _out(capsys)
    assert [e["site_id"] for e in out["entries"]] == ["a1", "b2"]

    status = {s["site_id"]: s for s in out["sites"]}
    assert status["b1"]["error"] == "RuntimeError: bug in a worker"
    assert status["b1"]["unexpected_error"] is True  # a bug, not an outage
    assert status["b1"]["consecutive_failures"] == 1  # escalates like any outage
    assert status["c1"]["error"] is not None  # a real outage, reported as one
    assert status["c1"]["unexpected_error"] is False
    assert all(status[i]["error"] is None for i in ("a1", "b2"))
    assert all(status[i]["unexpected_error"] is False for i in ("a1", "b2"))


def test_new_entries_does_not_absorb_a_missing_browser(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An unusable browser install fails the run instead of becoming a site error.

    It is the run's failure, not the site's: absorbing it would report one
    environment problem as N site outages, escalate them through the site-health
    counter, and bury the install command the operator needs in a per-site message.
    """
    add_site(
        sites_path(),
        SiteConfig(id="a", name="A", feed_url="https://a.example.com/f.xml", requires_browser=True),
    )
    add_site(sites_path(), SiteConfig(id="b", name="B", feed_url="https://b.example.com/f.xml"))
    _no_client(monkeypatch)

    def boom(site: SiteConfig, *, client: object) -> FetchOutcome:
        if site.requires_browser:
            raise MissingPlaywrightError("install chromium")
        return FetchOutcome(entries=[], error=None)

    monkeypatch.setattr(cli, "fetch_site", boom)
    capsys.readouterr()  # drop the add-site output

    assert cli.main(["new-entries"]) == 1
    captured = capsys.readouterr()
    assert "install chromium" in captured.err
    assert captured.out == ""  # no partial, exit-0 gather emitted


def test_new_entries_unknown_site_id_exits_nonzero(
    state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["new-entries", "--site-id", "nope"]) == 1
    assert "error:" in capsys.readouterr().err


# --- remind / mark-seen ---------------------------------------------------


def test_remind_writes_then_records_atomically(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    events: list[str] = []
    real_write = cli.write_feed_note
    real_record = cli.record

    def spy_write(*a: Any, **k: Any) -> dict[str, object]:
        events.append("write")
        return real_write(*a, **k)

    def spy_record(
        conn: sqlite3.Connection,
        url: CanonicalUrl,
        site_id: str,
        title: str | None,
        kept: int | None,
    ) -> None:
        events.append("record")
        real_record(conn, url, site_id, title, kept)

    monkeypatch.setattr(cli, "write_feed_note", spy_write)
    monkeypatch.setattr(cli, "record", spy_record)

    url = "https://e.example.com/a"
    rc = cli.main(
        [
            "remind",
            "--site-id",
            "f1",
            "--url",
            url,
            "--title",
            "T",
            "--summary",
            "N",
            "--today",
            "2026-07-19",
        ]
    )
    assert rc == 0
    assert events == ["write", "record"]  # vault write first, seen record second
    out = _out(capsys)
    assert out == {
        "slug": url_slug(str(canonical_url(url))),
        "url": url,
        "kept": True,
        "status": "created",
    }
    assert _feed_note_fm(url)["feed_kind"] == "article"  # the article path
    with contextlib.closing(open_db(db_path())) as conn:
        assert is_seen(conn, canonical_url(url))


def test_remind_writes_wall_and_summary(
    state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # The --wall flag marks the note behind a login/paywall; --summary lands as
    # the note's summary. Both are new to the vault sink.
    url = "https://e.example.com/walled"
    rc = cli.main(
        [
            "remind",
            "--site-id",
            "f1",
            "--url",
            url,
            "--title",
            "T",
            "--summary",
            "gist",
            "--wall",
            "--today",
            "2026-07-19",
        ]
    )
    assert rc == 0
    fm = _feed_note_fm(url)
    assert fm["wall"] is True
    assert fm["summary"] == "gist"


def test_remind_does_not_record_when_write_fails(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> dict[str, object]:
        raise VaultError("slug collision")

    monkeypatch.setattr(cli, "write_feed_note", boom)
    rc = cli.main(["remind", "--site-id", "f1", "--url", "https://e.example.com/a", "--title", "T"])
    assert rc == 1
    # The write failed, so the entry must stay unseen and retry next run.
    with contextlib.closing(open_db(db_path())) as conn:
        assert not is_seen(conn, canonical_url("https://e.example.com/a"))
        assert count(conn) == 0


def test_mark_seen_records_drop(state_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cli.main(
        ["mark-seen", "--site-id", "f1", "--url", "https://e.example.com/a", "--title", "T"]
    )
    assert rc == 0
    out = _out(capsys)
    assert out == {"url": "https://e.example.com/a", "kept": False}
    with contextlib.closing(open_db(db_path())) as conn:
        assert is_seen(conn, canonical_url("https://e.example.com/a"))
        row = conn.execute(
            "SELECT kept FROM seen WHERE canonical_url = ?", ("https://e.example.com/a",)
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
            index_url="https://e.example.com/blog",
            article_url_pattern=r"^/old/[^/]+/?$",
        ),
    )

    new_pattern = r"^/posts/[^/]+/?$"
    healed = [
        _entry("https://e.example.com/posts/a", kind="scrape"),
        _entry("https://e.example.com/posts/b", kind="scrape"),
    ]
    captured: dict[str, object] = {}

    def fake_fetch_entries(site: SiteConfig, *, client: object) -> list[Entry]:
        # The re-scrape sees the NEW pattern (applied via in-memory replace)
        # even though config is written last.
        captured["pattern"] = site.article_url_pattern
        return healed

    monkeypatch.setattr(cli, "fetch_entries", fake_fetch_entries)

    rc = cli.main(["heal-site", "--site-id", "s1", "--pattern", new_pattern])
    assert rc == 0
    assert captured["pattern"] == new_pattern
    out = _out(capsys)
    assert out == {
        "site_id": "s1",
        "pattern": new_pattern,
        "snapshotted": 2,
    }
    # The pattern was rewritten in config...
    assert load_sites(sites_path())[0].article_url_pattern == new_pattern
    # ...and the snapshot set is EXACTLY the fetched matches, nothing more.
    with contextlib.closing(open_db(db_path())) as conn:
        rows = {r[0] for r in conn.execute("SELECT canonical_url FROM seen")}
    assert rows == {"https://e.example.com/posts/a", "https://e.example.com/posts/b"}


def test_heal_site_fetch_failure_leaves_config_and_seen_untouched(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # The flood guard: if the re-scrape fails, config must NOT
    # be left carrying the new pattern with no snapshot under it. Snapshot-first /
    # config-last makes a fetch failure a clean no-op modulo the still-broken
    # pattern (which simply re-triggers heal next run).
    _no_client(monkeypatch)
    old_pattern = r"^/old/[^/]+/?$"
    add_site(
        sites_path(),
        SiteConfig(
            id="s1",
            name="Scrape",
            index_url="https://e.example.com/blog",
            article_url_pattern=old_pattern,
        ),
    )

    def boom(site: SiteConfig, *, client: object) -> list[Entry]:
        raise FetchError("https://e.example.com/blog")

    monkeypatch.setattr(cli, "fetch_entries", boom)

    rc = cli.main(["heal-site", "--site-id", "s1", "--pattern", r"^/posts/[^/]+/?$"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err
    # Config still on the OLD pattern; no back-catalog snapshotted.
    assert load_sites(sites_path())[0].article_url_pattern == old_pattern
    with contextlib.closing(open_db(db_path())) as conn:
        assert count(conn) == 0


def test_heal_site_rejects_an_uncompilable_pattern_before_fetching(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pattern the self-heal picked but cannot compile fails before any side effect.

    The re-scrape would raise a raw ``re.error`` (a traceback, not the CLI's
    ``error: …`` contract), and `update_pattern` refuses it at the end anyway — so
    the check belongs up front, where the skill's "non-zero exit means the re-scrape
    failed before the config write" reading still holds.
    """
    _no_client(monkeypatch)
    add_site(
        sites_path(),
        SiteConfig(
            id="s1",
            name="Scrape",
            index_url="https://e.example.com/blog",
            article_url_pattern=r"^/posts/[^/]+/?$",
        ),
    )
    monkeypatch.setattr(cli, "fetch_entries", lambda *a, **k: pytest.fail("fetched before check"))

    assert cli.main(["heal-site", "--site-id", "s1", "--pattern", "/a("]) == 1
    assert "not a valid regex" in capsys.readouterr().err
    assert load_sites(sites_path())[0].article_url_pattern == r"^/posts/[^/]+/?$"


def test_heal_site_rejects_feed_site(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Healing a feed site is meaningless and would corrupt the exactly-one
    # invariant; reject before any fetch/snapshot side effect.
    _no_client(monkeypatch)
    add_site(sites_path(), SiteConfig(id="f1", name="Feed", feed_url="https://e.example.com/f.xml"))
    monkeypatch.setattr(cli, "fetch_entries", lambda *a, **k: pytest.fail("fetched a feed site"))

    assert cli.main(["heal-site", "--site-id", "f1", "--pattern", "^/x/"]) == 1
    assert "scrape sites only" in capsys.readouterr().err


def test_heal_site_unknown_id_exits_nonzero(
    state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    add_site(sites_path(), SiteConfig(id="s1", name="S", feed_url="https://e.example.com/f.xml"))
    assert cli.main(["heal-site", "--site-id", "nope", "--pattern", "^/x/"]) == 1
    assert "error:" in capsys.readouterr().err


def test_heal_site_refuses_disabled_site(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A disabled site is inert: heal-site must refuse before any fetch, so a disabled
    # requires_browser site can't slip past the (disabled-ignoring) gate into a raw
    # ModuleNotFoundError. Enable it first.
    _no_client(monkeypatch)
    add_site(
        sites_path(),
        SiteConfig(
            id="s1",
            name="S",
            index_url="https://e.example.com/blog",
            article_url_pattern=r"^/old/[^/]+/?$",
        ),
    )
    assert cli.main(["disable-site", "--site-id", "s1"]) == 0
    capsys.readouterr()
    monkeypatch.setattr(cli, "fetch_entries", lambda *a, **k: pytest.fail("healed a disabled site"))

    assert cli.main(["heal-site", "--site-id", "s1", "--pattern", r"^/posts/[^/]+/?$"]) == 1
    assert "enabled sites only" in capsys.readouterr().err


# --- browser opt-in path (gate + teardown) --------------------------------


def test_new_entries_gate_fires_before_any_fetch(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A flagged site with Playwright missing must fail at the startup gate
    # BEFORE any gather, with the install command — not a mid-run ModuleNotFoundError.
    add_site(
        sites_path(),
        SiteConfig(
            id="js", name="JS", feed_url="https://e.example.com/f.xml", requires_browser=True
        ),
    )
    monkeypatch.setattr(browser, "_playwright_installed", lambda: False)
    monkeypatch.setattr(cli, "fetch_site", lambda *a, **k: pytest.fail("fetched before the gate"))

    rc = cli.main(["new-entries"])
    assert rc == 1
    assert "uv sync --extra browser" in capsys.readouterr().err


def test_add_site_requires_browser_missing_playwright_fails_before_snapshot(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # add-site writes config last, so the on-disk gate can't see the new site; the
    # in-memory require_playwright_for must fail before the snapshot fetch.
    monkeypatch.setattr(browser, "_playwright_installed", lambda: False)
    monkeypatch.setattr(
        cli, "fetch_entries", lambda *a, **k: pytest.fail("fetched before the gate")
    )

    rc = cli.main(
        [
            "add-site",
            "--id",
            "js",
            "--name",
            "JS",
            "--feed-url",
            "https://e/f.xml",
            "--requires-browser",
        ]
    )
    assert rc == 1
    assert "uv sync --extra browser" in capsys.readouterr().err
    assert not sites_path().exists()  # no config written on a gate failure


def test_heal_site_closes_browser_even_on_error(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # F2: heal-site re-scrapes, so it must tear down a lazily-launched browser in its
    # finally even when the re-scrape errors (it was previously omitted entirely).
    _no_client(monkeypatch)
    add_site(
        sites_path(),
        SiteConfig(
            id="s1",
            name="S",
            index_url="https://e.example.com/blog",
            article_url_pattern=r"^/old/[^/]+/?$",
            requires_browser=True,
        ),
    )

    def boom(site: SiteConfig, *, client: object) -> list[Entry]:
        raise BrowserFetchError("render failed")

    closed: list[bool] = []
    monkeypatch.setattr(cli, "fetch_entries", boom)
    monkeypatch.setattr(cli, "close_browser", lambda: closed.append(True))

    rc = cli.main(["heal-site", "--site-id", "s1", "--pattern", r"^/posts/[^/]+/?$"])
    assert rc == 1
    assert closed == [True]  # torn down despite the BrowserFetchError
    assert "error:" in capsys.readouterr().err


def test_new_entries_closes_browser_in_finally(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The teardown is unconditional: even a plain httpx run closes the (never-launched)
    # browser, so a lazily-launched one can never leak.
    _no_client(monkeypatch)
    add_site(sites_path(), SiteConfig(id="a", name="A", feed_url="https://a.example.com/f.xml"))
    monkeypatch.setattr(
        cli,
        "fetch_site",
        lambda *a, **k: FetchOutcome(entries=[], error=None),
    )
    closed: list[bool] = []
    monkeypatch.setattr(cli, "close_browser", lambda: closed.append(True))

    assert cli.main(["new-entries"]) == 0
    assert closed == [True]


# --- dispatch root --------------------------------------------------------


def test_no_subcommand_errors() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main([])
    assert excinfo.value.code == 2  # argparse: required subcommand missing


@pytest.mark.parametrize("command", ["remind", "forum-remind"])
def test_the_today_hook_stamps_a_date_the_note_can_hold(
    command: str, state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # `--today` is stamped into a Feeds/ note verbatim, so it goes through the
    # same validation as every other CLI reaching that writer. `20260719` is a
    # date `fromisoformat` reads and a vault date property is not, so it lands
    # canonicalised rather than as itself.
    url = f"https://e.example.com/canon-{command}"
    assert cli.main([command, *_remind_args(command), "--url", url, "--today", "20260719"]) == 0
    capsys.readouterr()
    assert _feed_note_fm(url)["added_date"] == "2026-07-19"


@pytest.mark.parametrize("command", ["remind", "forum-remind"])
def test_the_today_hook_refuses_what_is_no_date(
    command: str, state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # And a value that is no date at all fails at parse time — with a vault to
    # write into, so what stops the run is the flag rather than the sandbox.
    with pytest.raises(SystemExit) as excinfo:
        cli.main([command, *_remind_args(command), "--url", "https://e.x/a", "--today", "soon"])
    assert excinfo.value.code == 2
    assert "YYYY-MM-DD" in capsys.readouterr().err


# =============================================================================
# Forum subcommands
# =============================================================================

FORUM_URL = "https://forum.example.com"
FORUM_SITE_ID = "ef"


def _forum_site(*, site_id: str = FORUM_SITE_ID) -> SiteConfig:
    return SiteConfig(id=site_id, name="Example Forum", forum_url=FORUM_URL)


def _add_forum_site(*, site_id: str = FORUM_SITE_ID) -> SiteConfig:
    """Add a forum site to sites.toml; returns the config object."""
    site = _forum_site(site_id=site_id)
    add_site(sites_path(), site)
    return site


# --- add-forum ---------------------------------------------------------------


def test_add_forum_writes_config_no_snapshot(
    state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """add-forum writes config only; no seen-store rows (no back-catalog snapshot)."""
    rc = cli.main(
        ["add-forum", "--id", FORUM_SITE_ID, "--name", "Example Forum", "--forum-url", FORUM_URL]
    )
    assert rc == 0
    out = _out(capsys)
    assert out == {"site_id": FORUM_SITE_ID, "kind": "forum", "forum_url": FORUM_URL}

    # Config written.
    sites = load_sites(sites_path())
    assert len(sites) == 1
    assert sites[0].id == FORUM_SITE_ID
    assert sites[0].kind == "forum"
    assert sites[0].forum_url == FORUM_URL

    # No seen-store rows (admission is per-poll, not at registration).
    with contextlib.closing(open_db(db_path())) as conn:
        assert count(conn) == 0


def test_add_forum_with_tuning_flags(state_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """add-forum persists per-site tuning overrides."""
    rc = cli.main(
        [
            "add-forum",
            "--id",
            FORUM_SITE_ID,
            "--name",
            "Example Forum",
            "--forum-url",
            FORUM_URL,
            "--like-threshold",
            "4",
            "--interest-like-threshold",
            "2",
            "--daily-watch-count",
            "5",
            "--weekly-watch-count",
            "10",
            "--poll-offsets-days",
            "0",
            "3",
            "14",
        ]
    )
    assert rc == 0
    sites = load_sites(sites_path())
    s = sites[0]
    assert s.like_threshold == 4
    assert s.interest_like_threshold == 2
    assert s.daily_watch_count == 5
    assert s.weekly_watch_count == 10
    assert s.poll_offsets_days == (0, 3, 14)


def test_add_forum_duplicate_id_exits_nonzero(
    state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """add-forum rejects a duplicate site id."""
    cli.main(["add-forum", "--id", FORUM_SITE_ID, "--name", "F", "--forum-url", FORUM_URL])
    capsys.readouterr()
    rc = cli.main(["add-forum", "--id", FORUM_SITE_ID, "--name", "F2", "--forum-url", FORUM_URL])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


# --- forum-new ---------------------------------------------------------------


def _fake_admit_result(
    *,
    candidates: list[RuleACandidate] | None = None,
    error: str | None = None,
    fetch_count: int = 0,
) -> AdmitResult:
    return AdmitResult(candidates=candidates or [], error=error, fetch_count=fetch_count)


def _fake_gather_result(
    *,
    candidates: list[RuleBCandidate] | None = None,
    polled: list[PolledTopic] | None = None,
    error: str | None = None,
    fetch_count: int = 0,
) -> GatherForumResult:
    return GatherForumResult(
        candidates=candidates or [],
        polled_topics=polled or [],
        error=error,
        fetch_count=fetch_count,
    )


def _rule_a(topic_id: int, *, site_id: str = FORUM_SITE_ID) -> RuleACandidate:
    return RuleACandidate(
        topic_id=topic_id,
        topic_url=f"{FORUM_URL}/t/topic-{topic_id}/{topic_id}",
        title=f"Topic {topic_id}",
        op_text=f"OP text for {topic_id}",
    )


def _rule_b(topic_id: int, *, site_id: str = FORUM_SITE_ID) -> RuleBCandidate:
    return RuleBCandidate(
        topic_id=topic_id,
        topic_url=f"{FORUM_URL}/t/topic-{topic_id}/{topic_id}",
        title=f"Topic {topic_id}",
        trigger_posts=[
            TriggerPost(post_id=topic_id * 10, post_number=2, like_count=8, text="Nice post")
        ],
        effective_threshold=6,
    )


def test_forum_new_emits_topics_and_polls(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """forum-new emits both Rule-A and Rule-B candidates, plus polls worklist."""
    _no_client(monkeypatch)
    _add_forum_site()

    rule_a_cand = _rule_a(101)
    rule_b_cand = _rule_b(201)
    polled = [PolledTopic(topic_id=201, like_count=15)]

    monkeypatch.setattr(
        cli,
        "admit_from_feeds",
        lambda conn, site, *, client, now: _fake_admit_result(
            candidates=[rule_a_cand], fetch_count=3
        ),
    )
    monkeypatch.setattr(
        cli,
        "gather_forum",
        lambda conn, site, *, client, now: _fake_gather_result(
            candidates=[rule_b_cand], polled=polled, fetch_count=1
        ),
    )

    rc = cli.main(["forum-new"])
    assert rc == 0
    out = _out(capsys)

    # discourse_fetches sums the per-path fetch counts across sites (3 RSS + 1 JSON).
    assert out["discourse_fetches"] == 4

    topics = out["topics"]
    rules = {t["rule"] for t in topics}
    assert "A" in rules
    assert "B" in rules

    # Rule-A entry shape.
    a_entry = next(t for t in topics if t["rule"] == "A")
    assert a_entry["site_id"] == FORUM_SITE_ID
    assert a_entry["topic_id"] == 101
    assert a_entry["op_text"] == "OP text for 101"

    # Rule-B entry shape.
    b_entry = next(t for t in topics if t["rule"] == "B")
    assert b_entry["site_id"] == FORUM_SITE_ID
    assert b_entry["topic_id"] == 201
    assert b_entry["effective_threshold"] == 6
    assert len(b_entry["trigger_posts"]) == 1
    assert b_entry["trigger_posts"][0]["post_id"] == 2010

    # Polls worklist: topic 201 has exactly one candidate, which survived the cap.
    assert out["polls"] == [{"site_id": FORUM_SITE_ID, "topic_id": 201, "like_count": 15}]

    # Site status. A reachable gather (the fake's all_feeds_failed defaults False)
    # resets the site-health counter, so consecutive_failures is 0 / not persistent.
    assert out["sites"] == [
        {
            "site_id": FORUM_SITE_ID,
            "error": None,
            "consecutive_failures": 0,
            "persistent": False,
        }
    ]


def test_forum_new_sums_discourse_fetches_across_sites(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """discourse_fetches accumulates across every forum site, not reset per-site."""
    _no_client(monkeypatch)
    _add_forum_site()  # id "ef"
    _add_forum_site(site_id="ef2")

    monkeypatch.setattr(
        cli,
        "admit_from_feeds",
        lambda conn, site, *, client, now: _fake_admit_result(fetch_count=3),
    )
    monkeypatch.setattr(
        cli,
        "gather_forum",
        lambda conn, site, *, client, now: _fake_gather_result(
            fetch_count=2 if site.id == "ef2" else 1
        ),
    )

    rc = cli.main(["forum-new"])
    assert rc == 0
    out = _out(capsys)

    # Both sites processed: ef → admit 3 + gather 1, ef2 → admit 3 + gather 2 ⇒ 9.
    assert {s["site_id"] for s in out["sites"]} == {FORUM_SITE_ID, "ef2"}
    assert out["discourse_fetches"] == 9


def test_forum_new_absorbs_per_site_errors(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """forum-new absorbs errors from admit_from_feeds and gather_forum into site status."""
    _no_client(monkeypatch)
    _add_forum_site()

    monkeypatch.setattr(
        cli,
        "admit_from_feeds",
        lambda conn, site, *, client, now: _fake_admit_result(error="feed fetch failed"),
    )
    monkeypatch.setattr(
        cli,
        "gather_forum",
        lambda conn, site, *, client, now: _fake_gather_result(error="json fetch failed"),
    )

    rc = cli.main(["forum-new"])
    assert rc == 0
    out = _out(capsys)
    status = out["sites"][0]
    assert status["site_id"] == FORUM_SITE_ID
    # Both errors joined.
    assert "feed fetch failed" in status["error"]
    assert "json fetch failed" in status["error"]


def test_forum_new_excludes_article_sites(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """forum-new ignores article (feed/scrape) sites."""
    _no_client(monkeypatch)
    # Register an article site and a forum site.
    add_site(
        sites_path(), SiteConfig(id="art", name="Art", feed_url="https://art.example.com/f.xml")
    )
    _add_forum_site()

    gathered_ids: list[str] = []

    def fake_admit(
        conn: sqlite3.Connection, site: SiteConfig, *, client: object, now: int
    ) -> AdmitResult:
        gathered_ids.append(site.id)
        return _fake_admit_result()

    monkeypatch.setattr(cli, "admit_from_feeds", fake_admit)
    monkeypatch.setattr(cli, "gather_forum", lambda *a, **k: _fake_gather_result())

    cli.main(["forum-new"])
    # Only the forum site was gathered.
    assert gathered_ids == [FORUM_SITE_ID]


def test_new_entries_excludes_forum_sites(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """new-entries ignores forum sites."""
    _no_client(monkeypatch)
    add_site(
        sites_path(), SiteConfig(id="art", name="Art", feed_url="https://art.example.com/f.xml")
    )
    _add_forum_site()

    gathered_ids: list[str] = []

    def fake_fetch(site: SiteConfig, *, client: object) -> FetchOutcome:
        gathered_ids.append(site.id)
        return FetchOutcome(entries=[], error=None)

    monkeypatch.setattr(cli, "fetch_site", fake_fetch)

    cli.main(["new-entries"])
    # Only the article site was gathered.
    assert gathered_ids == ["art"]


def test_forum_new_cap_safety_withholds_truncated_topic(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A topic whose candidate is truncated by the global cap is excluded from polls.

    Never-lost invariant: do not finalize a topic until all its
    candidates are dispositioned.  With global-cap=1 and two topics (each with
    one candidate), the second topic's candidate is truncated and must not appear
    in polls.
    """
    _no_client(monkeypatch)
    _add_forum_site()

    # Two Rule-B topics; each has exactly one candidate.
    b1 = _rule_b(201)
    b2 = _rule_b(202)
    polled = [PolledTopic(topic_id=201, like_count=10), PolledTopic(topic_id=202, like_count=5)]

    monkeypatch.setattr(cli, "admit_from_feeds", lambda *a, **k: _fake_admit_result())
    monkeypatch.setattr(
        cli,
        "gather_forum",
        lambda *a, **k: _fake_gather_result(candidates=[b1, b2], polled=polled),
    )

    rc = cli.main(["forum-new", "--global-cap", "1"])
    assert rc == 0
    out = _out(capsys)

    # Only one topic survived the cap.
    assert len(out["topics"]) == 1
    surviving_topic_id = out["topics"][0]["topic_id"]
    # Only the surviving topic is in polls; the truncated one is withheld.
    poll_topic_ids = {p["topic_id"] for p in out["polls"]}
    assert surviving_topic_id in poll_topic_ids
    other_topic_id = 202 if surviving_topic_id == 201 else 201
    assert other_topic_id not in poll_topic_ids


def test_forum_new_cap_safety_includes_zero_candidate_topics(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A short-circuited / no-qualifying-post topic (zero candidates) IS included in polls.

    A topic with zero candidates has before_count == after_count == 0, so it is
    trivially cap-safe.  The cap cannot have truncated it because it never produced
    a candidate to begin with.
    """
    _no_client(monkeypatch)
    _add_forum_site()

    # No candidates; polled topic still in the finalize worklist.
    polled = [PolledTopic(topic_id=999, like_count=3)]

    monkeypatch.setattr(cli, "admit_from_feeds", lambda *a, **k: _fake_admit_result())
    monkeypatch.setattr(
        cli,
        "gather_forum",
        lambda *a, **k: _fake_gather_result(candidates=[], polled=polled),
    )

    rc = cli.main(["forum-new", "--global-cap", "1"])
    assert rc == 0
    out = _out(capsys)

    # No topics (no candidates).
    assert out["topics"] == []
    # But the zero-candidate topic IS finalize-safe.
    assert out["polls"] == [{"site_id": FORUM_SITE_ID, "topic_id": 999, "like_count": 3}]


def test_forum_new_cap_safety_withholds_topic_split_between_rule_a_and_b(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A single topic yielding BOTH a Rule-A and a Rule-B candidate is withheld from
    polls when the cap truncates either one.

    This is the subtlest branch of the cap-safe predicate: the same ``(site_id,
    topic_id)`` produces two candidate entries (before_count == 2). With
    global-cap=1 the cap falls *between* them, so exactly one survives
    (after_count == 1) and the topic must NOT be finalized — its other candidate
    was never dispositioned, so finalizing would advance the watch past an
    unjudged post (never-lost). It re-polls next run instead.
    """
    _no_client(monkeypatch)
    _add_forum_site()

    # One topic (201) surfaced by BOTH rules in the same run: newly admitted (Rule
    # A) and already due with a qualifying post (Rule B).
    a = _rule_a(201)
    b = _rule_b(201)
    polled = [PolledTopic(topic_id=201, like_count=15)]

    monkeypatch.setattr(
        cli,
        "admit_from_feeds",
        lambda *a_, **k: _fake_admit_result(candidates=[a]),
    )
    monkeypatch.setattr(
        cli,
        "gather_forum",
        lambda *a_, **k: _fake_gather_result(candidates=[b], polled=polled),
    )

    rc = cli.main(["forum-new", "--global-cap", "1"])
    assert rc == 0
    out = _out(capsys)

    # The cap kept exactly one of the topic's two candidates...
    assert len(out["topics"]) == 1
    assert out["topics"][0]["topic_id"] == 201
    # ...so the topic is NOT finalize-safe and must be withheld from polls.
    assert out["polls"] == []


# --- forum-remind ------------------------------------------------------------


def test_forum_remind_writes_then_records(
    state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """forum-remind writes a feed_kind=forum note and records the post seen."""
    _add_forum_site()
    url = f"{FORUM_URL}/t/topic/1234"

    rc = cli.main(
        [
            "forum-remind",
            "--site-id",
            FORUM_SITE_ID,
            "--topic-id",
            "1234",
            "--post-id",
            "5001",
            "--url",
            url,
            "--title",
            "My Topic",
            "--summary",
            "Great post",
            "--today",
            "2026-07-19",
        ]
    )
    assert rc == 0
    out = _out(capsys)
    assert out["kept"] is True
    assert out["post_id"] == 5001
    assert out["slug"] == url_slug(str(canonical_url(url)))
    assert out["status"] == "created"
    assert _feed_note_fm(url)["feed_kind"] == "forum"

    # Post is recorded seen in forum_post_seen; the topic-grain interest verdict
    # is NOT touched (a Rule-B keep carries no --is-op; the two axes are independent).
    with contextlib.closing(open_db(db_path())) as conn:
        from feed_filter.forum_store import is_post_seen, op_interest_kept

        assert is_post_seen(conn, FORUM_SITE_ID, 5001)
        assert op_interest_kept(conn, FORUM_SITE_ID, 1234) is None


def test_forum_remind_reremind_upserts_same_note(
    state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The former open-reminder suppression case: the same topic taken up twice
    (both rules, or a cross-run re-remind as a post crosses the like threshold)
    resolves to one hash-named note. The second keep upserts that SAME note — an
    idempotent update, not a duplicate — and records each disposition."""
    from feed_filter.forum_store import admit_topic, is_post_seen, op_interest_kept

    _add_forum_site()
    url = f"{FORUM_URL}/t/topic/1234"
    with contextlib.closing(open_db(db_path())) as conn:
        admit_topic(conn, FORUM_SITE_ID, 1234, first_seen_at=0)

    # Rule-A keep of the OP.
    rc1 = cli.main(
        [
            "forum-remind",
            "--site-id",
            FORUM_SITE_ID,
            "--topic-id",
            "1234",
            "--url",
            url,
            "--title",
            "My Topic",
            "--is-op",
            "--today",
            "2026-07-19",
        ]
    )
    capsys.readouterr()  # clear the first emit
    # Rule-B keep of a later post in the same topic.
    rc2 = cli.main(
        [
            "forum-remind",
            "--site-id",
            FORUM_SITE_ID,
            "--topic-id",
            "1234",
            "--post-id",
            "5001",
            "--url",
            url,
            "--title",
            "My Topic",
            "--summary",
            "hot",
            "--today",
            "2026-07-19",
        ]
    )
    assert rc1 == 0 and rc2 == 0
    assert _out(capsys)["status"] == "updated"  # the second write updated the note

    # Exactly one note (the same slug), written twice.
    notes = list((vault_path() / "Feeds").glob("*.md"))
    assert [n.name for n in notes] == [f"{url_slug(str(canonical_url(url)))}.md"]
    # Both axes recorded independently.
    with contextlib.closing(open_db(db_path())) as conn:
        assert op_interest_kept(conn, FORUM_SITE_ID, 1234) == 1
        assert is_post_seen(conn, FORUM_SITE_ID, 5001)


def test_forum_remind_rule_a_keep_records_verdict_only(
    state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A Rule-A keep is ``--is-op`` with no ``--post-id``: it records the
    topic-grain interest verdict but writes NO post-grain seen, so a later
    Rule-B pass can still re-judge the OP (the two axes are independent).
    """
    from feed_filter.forum_store import admit_topic, is_post_seen, op_interest_kept

    _add_forum_site()
    with contextlib.closing(open_db(db_path())) as conn:
        admit_topic(conn, FORUM_SITE_ID, 1234, first_seen_at=0)

    rc = cli.main(
        [
            "forum-remind",
            "--site-id",
            FORUM_SITE_ID,
            "--topic-id",
            "1234",
            "--url",
            f"{FORUM_URL}/t/topic/1234",
            "--title",
            "My Topic",
            "--is-op",
            "--today",
            "2026-07-19",
        ]
    )
    assert rc == 0
    assert _out(capsys)["post_id"] is None  # no post-grain key on a Rule-A keep

    with contextlib.closing(open_db(db_path())) as conn:
        assert op_interest_kept(conn, FORUM_SITE_ID, 1234) == 1
        # No post-grain seen written: the OP id is not even known to Rule A.
        assert not is_post_seen(conn, FORUM_SITE_ID, 5001)


def test_forum_remind_does_not_record_when_write_fails(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """forum-remind: if the vault write fails, the post must stay unseen."""
    _add_forum_site()

    def boom(*_a: object, **_k: object) -> dict[str, object]:
        raise VaultError("slug collision")

    monkeypatch.setattr(cli, "write_feed_note", boom)

    rc = cli.main(
        [
            "forum-remind",
            "--site-id",
            FORUM_SITE_ID,
            "--topic-id",
            "1234",
            "--post-id",
            "5001",
            "--url",
            f"{FORUM_URL}/t/topic/1234",
            "--title",
            "T",
        ]
    )
    assert rc == 1
    with contextlib.closing(open_db(db_path())) as conn:
        from feed_filter.forum_store import is_post_seen

        assert not is_post_seen(conn, FORUM_SITE_ID, 5001)


# --- forum-mark-seen ---------------------------------------------------------


def test_forum_mark_seen_records_drop(state_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """forum-mark-seen records kept=0 for the post; no note."""
    _add_forum_site()

    rc = cli.main(
        [
            "forum-mark-seen",
            "--site-id",
            FORUM_SITE_ID,
            "--topic-id",
            "1234",
            "--post-id",
            "5001",
            "--url",
            f"{FORUM_URL}/t/topic/1234",
            "--title",
            "T",
        ]
    )
    assert rc == 0
    out = _out(capsys)
    assert out["kept"] is False
    assert out["post_id"] == 5001

    with contextlib.closing(open_db(db_path())) as conn:
        from feed_filter.forum_store import is_post_seen

        assert is_post_seen(conn, FORUM_SITE_ID, 5001)


def test_forum_mark_seen_rule_a_drop_records_verdict_only(
    state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A Rule-A drop is ``--is-op`` with no ``--post-id``: it records the
    interest verdict (kept=0) but writes NO post-grain seen, so if the OP later
    gains likes Rule B re-judges it (the user-confirmed behaviour).
    """
    from feed_filter.forum_store import admit_topic, is_post_seen, op_interest_kept

    _add_forum_site()

    with contextlib.closing(open_db(db_path())) as conn:
        admit_topic(conn, FORUM_SITE_ID, 1234, first_seen_at=0)

    rc = cli.main(
        [
            "forum-mark-seen",
            "--site-id",
            FORUM_SITE_ID,
            "--topic-id",
            "1234",
            "--url",
            f"{FORUM_URL}/t/topic/1234",
            "--title",
            "T",
            "--is-op",
        ]
    )
    assert rc == 0
    assert _out(capsys)["post_id"] is None

    with contextlib.closing(open_db(db_path())) as conn:
        assert op_interest_kept(conn, FORUM_SITE_ID, 1234) == 0
        assert not is_post_seen(conn, FORUM_SITE_ID, 5001)  # OP not seen at post grain


# --- forum-poll-done ---------------------------------------------------------


def test_forum_poll_done_advances_counter(
    state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """forum-poll-done increments completed_polls and stores like_count."""
    from feed_filter.forum_store import admit_topic

    _add_forum_site()

    with contextlib.closing(open_db(db_path())) as conn:
        admit_topic(conn, FORUM_SITE_ID, 1234, first_seen_at=0)

    rc = cli.main(
        [
            "forum-poll-done",
            "--site-id",
            FORUM_SITE_ID,
            "--topic-id",
            "1234",
            "--like-count",
            "15",
        ]
    )
    assert rc == 0
    out = _out(capsys)
    assert out["site_id"] == FORUM_SITE_ID
    assert out["topic_id"] == 1234
    assert out["like_count"] == 15

    with contextlib.closing(open_db(db_path())) as conn:
        row = conn.execute(
            "SELECT completed_polls, last_like_count, retired FROM forum_watch "
            "WHERE site_id = ? AND topic_id = ?",
            (FORUM_SITE_ID, 1234),
        ).fetchone()
    assert row[0] == 1  # completed_polls advanced
    assert row[1] == 15  # like_count stored


def test_forum_poll_done_retires_at_last_offset(
    state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """forum-poll-done sets retired=1 when all offsets are consumed."""
    from feed_filter.forum_store import admit_topic

    # Register with a single-offset schedule so one poll retires the topic.
    add_site(
        sites_path(),
        SiteConfig(
            id=FORUM_SITE_ID,
            name="F",
            forum_url=FORUM_URL,
            poll_offsets_days=(0,),
        ),
    )
    with contextlib.closing(open_db(db_path())) as conn:
        admit_topic(conn, FORUM_SITE_ID, 1234, first_seen_at=0)

    rc = cli.main(
        [
            "forum-poll-done",
            "--site-id",
            FORUM_SITE_ID,
            "--topic-id",
            "1234",
            "--like-count",
            "10",
        ]
    )
    assert rc == 0

    with contextlib.closing(open_db(db_path())) as conn:
        row = conn.execute(
            "SELECT retired FROM forum_watch WHERE site_id = ? AND topic_id = ?",
            (FORUM_SITE_ID, 1234),
        ).fetchone()
    assert row[0] == 1  # retired after last offset


def test_forum_poll_done_unknown_site_exits_nonzero(
    state_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """forum-poll-done exits non-zero when the site id is not registered."""
    rc = cli.main(["forum-poll-done", "--site-id", "nope", "--topic-id", "1", "--like-count", "0"])
    assert rc == 1
    assert "error:" in capsys.readouterr().err


# --- query-new ------------------------------------------------------------


def _query_stub(monkeypatch: pytest.MonkeyPatch, by_query: dict[str, Any]) -> list[tuple[str, int]]:
    """Replace the Exa call with a canned per-query outcome.

    Returns the call log as ``(query, num_results)`` pairs — Exa bills per request
    and again per result past ten, so how many requests a run makes and how large
    each one asks for are both cost invariants worth asserting.
    """
    calls: list[tuple[str, int]] = []

    def fake_search(_client: Any, query: str, *, num_results: int) -> Any:
        calls.append((query, num_results))
        outcome = by_query[query]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    _no_client(monkeypatch)
    monkeypatch.setattr(cli, "search", fake_search)
    return calls


def _outcome(query: str, urls: list[str], *, error: str | None = None, cost: float = 0.007) -> Any:
    return QueryOutcome(
        query=query,
        hits=tuple(QueryHit(url=u, title=f"T {u}", text="body text") for u in urls),
        cost_dollars=cost,
        error=error,
    )


def test_query_new_emits_entries_with_query_provenance(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _query_stub(
        monkeypatch, {"walking maps": _outcome("walking maps", ["https://a.example/p"])}
    )

    assert cli.main(["query-new", "--query", "walking maps"]) == 0

    # One request per query, asking for the billed-as-one-unit default.
    assert calls == [("walking maps", DEFAULT_QUERY_RESULTS)]

    out = _out(capsys)
    assert out["entries"] == [
        {
            "site_id": cli.QUERY_SITE_ID,
            "url": "https://a.example/p",
            "title": "T https://a.example/p",
            "summary": "body text",
            "kind": "query",
            "query": "walking maps",
        }
    ]
    assert out["queries"] == [{"query": "walking maps", "found": 1, "new": 1, "error": None}]
    assert out["cost_dollars"] == 0.007


def test_query_new_drops_already_seen_pages(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A page another gather already dispositioned is not re-offered by a query."""
    with contextlib.closing(open_db(db_path())) as conn:
        conn.execute(
            "INSERT INTO seen (canonical_url, site_id, title, kept, seen_at) VALUES (?,?,?,?,?)",
            (str(canonical_url("https://old.example/p")), "some-feed", "t", 1, 0),
        )
        conn.commit()
    # Non-canonical spellings: the seen-store match and the emitted URL both have
    # to go through canonical_url, not the raw hit URL.
    _query_stub(
        monkeypatch,
        {"q": _outcome("q", ["https://OLD.example/p/?utm_source=exa", "https://new.example/p/"])},
    )

    assert cli.main(["query-new", "--query", "q"]) == 0

    out = _out(capsys)
    assert [e["url"] for e in out["entries"]] == [str(canonical_url("https://new.example/p/"))]
    assert out["queries"][0] == {"query": "q", "found": 2, "new": 1, "error": None}


def test_query_new_dedupes_across_queries_keeping_the_first(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    shared = "https://both.example/p"
    _query_stub(
        monkeypatch,
        {
            "first": _outcome("first", [shared]),
            "second": _outcome("second", [shared, "https://only2.example/p"]),
        },
    )

    assert cli.main(["query-new", "--query", "first", "--query", "second"]) == 0

    out = _out(capsys)
    assert [(e["url"], e["query"]) for e in out["entries"]] == [
        (shared, "first"),
        ("https://only2.example/p", "second"),
    ]
    assert [q["new"] for q in out["queries"]] == [1, 1]


def test_query_new_one_failing_query_does_not_lose_the_others(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _query_stub(
        monkeypatch,
        {
            "bad": _outcome("bad", [], error="HTTPStatusError: 500"),
            "good": _outcome("good", ["https://a.example/p"]),
        },
    )

    assert cli.main(["query-new", "--query", "bad", "--query", "good"]) == 0

    out = _out(capsys)
    assert [e["url"] for e in out["entries"]] == ["https://a.example/p"]
    assert out["queries"][0]["error"] == "HTTPStatusError: 500"
    assert out["queries"][1]["error"] is None


def test_query_new_missing_key_is_reported_per_query_not_a_crash(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _query_stub(monkeypatch, {"q": ExaError("EXA_API_KEY is unset")})

    assert cli.main(["query-new", "--query", "q"]) == 0

    out = _out(capsys)
    assert out["entries"] == []
    assert "EXA_API_KEY" in out["queries"][0]["error"]


def test_query_new_records_nothing_seen(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Like the other gathers, query-new only offers — remind/mark-seen record."""
    _query_stub(monkeypatch, {"q": _outcome("q", ["https://a.example/p"])})

    assert cli.main(["query-new", "--query", "q"]) == 0

    with contextlib.closing(open_db(db_path())) as conn:
        assert count(conn) == 0


def test_query_new_global_cap_truncates_and_leaves_the_rest_unseen(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    urls = [f"https://a.example/p{i}" for i in range(5)]
    _query_stub(monkeypatch, {"q": _outcome("q", urls)})

    assert cli.main(["query-new", "--query", "q", "--global-cap", "2"]) == 0

    out = _out(capsys)
    assert len(out["entries"]) == 2
    with contextlib.closing(open_db(db_path())) as conn:
        assert count(conn) == 0


def test_query_new_previews_long_page_text(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    long_outcome = QueryOutcome(
        query="q",
        hits=(
            QueryHit(url="https://a.example/p", title="T", text="x" * (SUMMARY_PREVIEW_CHARS + 50)),
        ),
        cost_dollars=0.007,
    )
    _query_stub(monkeypatch, {"q": long_outcome})

    assert cli.main(["query-new", "--query", "q"]) == 0

    summary = _out(capsys)["entries"][0]["summary"]
    assert len(summary) == SUMMARY_PREVIEW_CHARS + 1  # + the ellipsis
    assert summary.endswith("…")


def test_query_new_sums_cost_across_queries(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _query_stub(
        monkeypatch,
        {
            "a": _outcome("a", ["https://a.example/p"], cost=0.007),
            "b": _outcome("b", ["https://b.example/p"], cost=0.008),
        },
    )

    assert cli.main(["query-new", "--query", "a", "--query", "b"]) == 0

    assert _out(capsys)["cost_dollars"] == 0.015


def test_query_new_issues_one_request_per_query_in_order(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exa bills per request, so a run must not re-ask or reorder."""
    calls = _query_stub(
        monkeypatch,
        {"a": _outcome("a", ["https://a.example/p"]), "b": _outcome("b", ["https://b.example/p"])},
    )

    assert cli.main(["query-new", "--query", "a", "--query", "b"]) == 0

    assert calls == [("a", DEFAULT_QUERY_RESULTS), ("b", DEFAULT_QUERY_RESULTS)]


def test_query_new_num_results_reaches_the_search(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _query_stub(monkeypatch, {"q": _outcome("q", [])})

    assert cli.main(["query-new", "--query", "q", "--num-results", "3"]) == 0

    assert calls == [("q", 3)]


def test_query_new_interleaves_queries_before_the_cap(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A broad query must not starve a narrow one at the cap, and `new` is pre-cap."""
    _query_stub(
        monkeypatch,
        {
            "broad": _outcome("broad", [f"https://b.example/p{i}" for i in range(3)]),
            "narrow": _outcome("narrow", ["https://n.example/p"]),
        },
    )

    rc = cli.main(["query-new", "--query", "broad", "--query", "narrow", "--global-cap", "2"])
    assert rc == 0

    out = _out(capsys)
    # Plain concatenation would emit ["broad", "broad"] and drop the narrow query.
    assert [e["query"] for e in out["entries"]] == ["broad", "narrow"]
    assert [q["new"] for q in out["queries"]] == [3, 1]


@pytest.mark.parametrize("value", ["0", "-1"])
def test_query_new_rejects_a_meaningless_num_results(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """The one flag that multiplies a bill fails at parse time, before any request."""
    calls = _query_stub(monkeypatch, {"q": _outcome("q", [])})

    with pytest.raises(SystemExit):
        cli.main(["query-new", "--query", "q", "--num-results", value])

    assert calls == []


def test_query_new_never_emits_a_non_json_cost(
    state_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each query's cost is finite, but their sum can still overflow — and the
    emitted document is the only contract with the skills, so it must parse."""
    huge = 1e308
    _query_stub(
        monkeypatch,
        {
            "a": _outcome("a", ["https://a.example/p"], cost=huge),
            "b": _outcome("b", ["https://b.example/p"], cost=huge),
        },
    )

    assert cli.main(["query-new", "--query", "a", "--query", "b"]) == 0

    raw = capsys.readouterr().out

    def _reject(token: str) -> float:
        raise AssertionError(f"emitted a non-JSON token: {token}")

    assert json.loads(raw, parse_constant=_reject)["cost_dollars"] == 0.0
