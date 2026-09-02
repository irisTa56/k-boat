"""Behavior tests for the vault output sink (``write_feed_note``).

A tmp vault, no real iCloud path: the create/update merge, the always-present
defaults, the human-disposition preservation, and the collision contract are
checked against real files written through ``kboat.write.upsert``.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

import feed_filter.vault as vault_mod
import kboat.lock
from feed_filter.vault import VaultError, write_feed_note
from kboat.canonical import CanonicalUrl, canonical_url
from kboat.frontmatter import Value, parse_frontmatter
from kboat.lock import VaultLockedError, vault_lock
from kboat.naming import url_slug
from kboat.note.__main__ import main as note_cli

CU = CanonicalUrl("https://example.com/post")


def _fm(vault: Path, cu: str) -> dict[str, Value]:
    note = vault / "Feeds" / f"{url_slug(cu)}.md"
    return parse_frontmatter(note.read_text(encoding="utf-8"))


def test_create_writes_a_feed_note_with_defaults(tmp_path: Path) -> None:
    result = write_feed_note(
        tmp_path,
        CU,
        title="A post",
        feed_kind="article",
        site_id="ex",
        summary="a snippet",
        wall=False,
        today="2026-07-19",
    )
    assert result["status"] == "created"
    assert result["slug"] == url_slug(str(CU))
    fm = _fm(tmp_path, str(CU))
    assert fm["type"] == "feed"
    assert fm["url"] == str(CU)
    assert fm["feed_kind"] == "article"
    assert fm["site_id"] == "ex"
    assert fm["summary"] == "a snippet"
    assert fm["added_date"] == "2026-07-19"
    # All four status booleans are present and false on a fresh card. `shelved`
    # is the one feed-filter never writes — `upsert`'s always-present default
    # fills it; the other three feed-filter writes itself.
    assert fm["read"] is False
    assert fm["shelved"] is False
    assert fm["dismissed"] is False
    assert fm["wall"] is False


def test_blank_title_falls_back_to_url(tmp_path: Path) -> None:
    # `remind --title ""` (the skills' wall / judging-error path) must still write
    # a note whose required `title` is non-empty — the URL is the fallback.
    for i, blank in enumerate(("", "   ")):
        v = tmp_path / f"v{i}"
        v.mkdir()
        write_feed_note(
            v,
            CU,
            title=blank,
            feed_kind="article",
            site_id="ex",
            summary="",
            wall=False,
            today="2026-07-19",
        )
        assert _fm(v, str(CU))["title"] == str(CU)


def test_wall_and_forum_kind_are_written(tmp_path: Path) -> None:
    write_feed_note(
        tmp_path,
        CU,
        title="T",
        feed_kind="forum",
        site_id="ex",
        summary="",
        wall=True,
        today="2026-07-19",
    )
    fm = _fm(tmp_path, str(CU))
    assert fm["feed_kind"] == "forum"
    assert fm["wall"] is True


def test_rewrite_resurfaces_hidden_cards_preserves_shelved(tmp_path: Path) -> None:
    write_feed_note(
        tmp_path,
        CU,
        title="T",
        feed_kind="forum",
        site_id="ex",
        summary="first",
        wall=False,
        today="2026-07-19",
    )
    note = tmp_path / "Feeds" / f"{url_slug(str(CU))}.md"
    # The human ticks every disposition box at once, so each flag's treatment on
    # the re-write has to hold on its own rather than only one of them.
    note.write_text(
        note.read_text()
        .replace("read: false", "read: true")
        .replace("shelved: false", "shelved: true")
        .replace("dismissed: false", "dismissed: true"),
        encoding="utf-8",
    )
    # A re-remind (a later qualifying forum post) upserts the same note.
    result = write_feed_note(
        tmp_path,
        CU,
        title="T",
        feed_kind="forum",
        site_id="ex",
        summary="second",
        wall=True,
        today="2026-07-20",
    )
    assert result["status"] == "updated"
    fm = _fm(tmp_path, str(CU))
    assert fm["shelved"] is True  # the reader's "read later" is preserved
    # Both hiding flags reset — the topic resurfaces on new activity.
    assert fm["read"] is False
    assert fm["dismissed"] is False
    assert fm["wall"] is True  # feed-filter's field refreshed
    assert fm["summary"] == "second"
    assert fm["added_date"] == "2026-07-19"  # created stamp stable across the re-write


@pytest.mark.parametrize(
    ("link", "why"),
    [
        ("https://example.com/post", "the canonical form itself"),
        ("https://example.com/post/", "a trailing slash"),
        ("HTTPS://Example.COM/post", "another casing"),
        ("https://example.com/post?utm_source=news", "a tracking parameter"),
        ("https://example.com/post#intro", "a fragment"),
    ],
)
def test_the_note_lands_where_the_slug_oracle_says(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], link: str, why: str
) -> None:
    """Parity between the two members that name notes in one vault.

    feed-filter canonicalizes at gather time and hashes the result; a K-Boat
    skill asks `kboat-note slug` for the same page. The two agreeing is what
    stops one page from occupying a `Feeds/` note and a `Sources/` note under
    different names — so the check is against the CLI a skill actually calls,
    not against a second copy of the recipe.
    """
    assert note_cli(["slug", link]) == 0
    oracle = json.loads(capsys.readouterr().out)

    result = write_feed_note(
        tmp_path,
        canonical_url(link),
        title="A post",
        feed_kind="article",
        site_id="ex",
        summary="",
        wall=False,
        today="2026-07-19",
    )

    assert result["status"] == "created", "the writer verifies the slug, so parity is enforced"
    assert result["slug"] == oracle["slug"], why
    assert (tmp_path / "Feeds" / f"{oracle['slug']}.md").exists(), why


def test_an_unreadable_url_says_so_rather_than_blaming_a_hash_clash(tmp_path: Path) -> None:
    # The two refusals need different words: this one is a note to repair by
    # hand, not the astronomically-unlikely 48-bit clash the other message names.
    slug = url_slug(str(CU))
    (tmp_path / "Feeds").mkdir()
    (tmp_path / "Feeds" / f"{slug}.md").write_text(
        "---\ntype: feed\ntitle: T\nurl: >-\n  https://example.com/post\n"
        "shelved: false\ndismissed: false\nwall: false\n"
        "feed_kind: article\nsite_id: ex\nsummary:\nadded_date: 2026-07-01\n---\n",
        encoding="utf-8",
    )
    with pytest.raises(VaultError, match="cannot be read"):
        write_feed_note(
            tmp_path,
            CU,
            title="Mine",
            feed_kind="article",
            site_id="ex",
            summary="",
            wall=False,
            today="2026-07-19",
        )


def test_collision_raises_vault_error(tmp_path: Path) -> None:
    # A note already at this slug carrying a DIFFERENT url is a collision.
    slug = url_slug(str(CU))
    (tmp_path / "Feeds").mkdir()
    (tmp_path / "Feeds" / f"{slug}.md").write_text(
        "---\ntype: feed\ntitle: Other\nurl: https://other.example/x\n"
        "read: false\nshelved: false\ndismissed: false\nwall: false\n"
        "feed_kind: article\nsite_id: ex\nsummary:\nadded_date: 2026-07-01\n---\n",
        encoding="utf-8",
    )
    with pytest.raises(VaultError, match="already holds a different url"):
        write_feed_note(
            tmp_path,
            CU,
            title="Mine",
            feed_kind="article",
            site_id="ex",
            summary="",
            wall=False,
            today="2026-07-19",
        )


def test_a_refusal_this_module_does_not_know_still_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Never-lost rests on nothing being recorded seen unless a note landed, so
    # the test is for a *written* note, not for the refusals listed here — a
    # status added to the writer later must not slip through as success.
    monkeypatch.setattr(
        vault_mod, "upsert", lambda *_args, **_kwargs: {"status": "a refusal from the future"}
    )
    (tmp_path / "Feeds").mkdir()

    with pytest.raises(VaultError, match="refused the note"):
        write_feed_note(
            tmp_path,
            CU,
            title="Mine",
            feed_kind="article",
            site_id="ex",
            summary="",
            wall=False,
            today="2026-07-19",
        )


def _write(vault: Path) -> dict[str, object]:
    return write_feed_note(
        vault,
        CU,
        title="A post",
        feed_kind="article",
        site_id="ex",
        summary="",
        wall=False,
        today="2026-07-19",
    )


def test_the_write_is_held_under_the_vault_lock(tmp_path: Path) -> None:
    # Serialized against a K-Boat run writing the same vault: while the note is
    # being written, nobody else can take the lock.
    real_upsert = vault_mod.upsert

    def upsert_under_lock(*args: object, **kwargs: object) -> dict[str, object]:
        with pytest.raises(VaultLockedError), vault_lock(tmp_path, wait_s=0.0):
            pytest.fail("the write must hold the lock while it runs")
        return real_upsert(*args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(vault_mod, "upsert", upsert_under_lock)
        assert _write(tmp_path)["status"] == "created"
    # The lock file outlives the hold, so what must be gone is the hold: taking it
    # again with no wait at all only succeeds if the write released it.
    with vault_lock(tmp_path, wait_s=0.0):
        pass


def test_a_held_vault_is_waited_for_rather_than_refused(tmp_path: Path) -> None:
    # feed-filter takes the lock on the shared terms, and the wait is what it depends
    # on most: its unit is one entry per process, so a refusal drops that entry until
    # the next gather rediscovers it. No monkeypatch — the shipped
    # `kboat.lock.DEFAULT_WAIT_S` is what makes this pass, so zeroing it fails here.
    took_the_lock = threading.Event()
    hold_on = threading.Event()
    held_for = 0.1

    def hold() -> None:
        with vault_lock(tmp_path):
            took_the_lock.set()
            # Held until the writing thread has read its start clock — no timeout, since
            # waking on one would let this sleep, and the release after it, begin before
            # that reading and leave the assertion below measuring a hold it did not span.
            hold_on.wait()
            time.sleep(held_for)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert took_the_lock.wait(timeout=5), "the holder never took the lock"
        started = time.monotonic()
        hold_on.set()
        result = _write(tmp_path)
        waited = time.monotonic() - started
    finally:
        hold_on.set()  # idempotent: on a path that never reached the set above, this ends the holder
        holder.join()

    # `hold_on` is set after `started`, so the holder's sleep begins no earlier than that
    # reading and its release no earlier than `started` plus `held_for` — and `flock`
    # hands the lock on only at that release. So the write spans a hold it could not have
    # jumped, timed by one thread's own clock: the release instant is not observable from
    # out here, and a stamp the holder takes after its `with` block is a stamp taken
    # after the release it claims to mark.
    assert result["status"] == "created"
    assert waited >= held_for


def test_an_expired_wait_raises_and_writes_no_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The never-lost half: the entry is not written, so the caller raises before
    # its seen-record and the next run retries it.
    monkeypatch.setattr(kboat.lock, "DEFAULT_WAIT_S", 0.1)
    with vault_lock(tmp_path), pytest.raises(VaultLockedError):
        _write(tmp_path)
    assert not (tmp_path / "Feeds").exists()
