"""Behavior tests for the vault output sink (``write_feed_note``).

A tmp vault, no real iCloud path: the create/update merge, the always-present
defaults, the human-disposition preservation, and the collision contract are
checked against real files written through ``kboat.write.upsert``.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

import feed_filter.vault as vault_mod
from feed_filter.canonical import CanonicalUrl
from feed_filter.vault import VaultError, write_feed_note
from kboat.frontmatter import Value, parse_frontmatter
from kboat.lock import LOCK_NAME, VaultLockedError, vault_lock
from kboat.naming import url_slug

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
        with pytest.raises(VaultLockedError), vault_lock(tmp_path):
            pytest.fail("the write must hold the lock while it runs")
        return real_upsert(*args, **kwargs)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(vault_mod, "upsert", upsert_under_lock)
        assert _write(tmp_path)["status"] == "created"
    assert not (tmp_path / LOCK_NAME).exists()


def test_a_held_vault_is_waited_for_rather_than_refused(tmp_path: Path) -> None:
    # One note per process, so an immediate refusal would cost this entry a whole
    # run; the write waits for the holder instead. No monkeypatch: the shipped
    # `VAULT_LOCK_WAIT_S` is what makes this pass, so setting it to zero — which
    # would collapse the asymmetry the docstring promises — fails here.
    took_the_lock = threading.Event()
    released = threading.Event()

    def hold() -> None:
        with vault_lock(tmp_path):
            took_the_lock.set()
            time.sleep(0.1)
        released.set()

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert took_the_lock.wait(timeout=5), "the holder never took the lock"
        result = _write(tmp_path)
    finally:
        holder.join()
    # Waiting is the assertion, not a side effect of losing the start race: the
    # holder had the lock before the write began, and the write only got it back
    # once the holder let go.
    assert released.is_set()
    assert result["status"] == "created"


def test_an_expired_wait_raises_and_writes_no_note(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The never-lost half: the entry is not written, so the caller raises before
    # its seen-record and the next run retries it.
    monkeypatch.setattr(vault_mod, "VAULT_LOCK_WAIT_S", 0.1)
    with vault_lock(tmp_path), pytest.raises(VaultLockedError):
        _write(tmp_path)
    assert not (tmp_path / "Feeds").exists()
