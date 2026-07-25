"""Behavior tests for the vault output sink (``write_feed_note``).

A tmp vault, no real iCloud path: the create/update merge, the always-present
defaults, the human-disposition preservation, and the collision contract are
checked against real files written through ``kboat.write.upsert``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from feed_filter.canonical import CanonicalUrl
from feed_filter.vault import VaultError, write_feed_note
from kboat.frontmatter import Value, parse_frontmatter
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
