"""Tests for `kboat.write.upsert` — create/update, merge-preserve, stamps, body."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kboat.frontmatter import body_after_frontmatter, parse_frontmatter
from kboat.schema import FEED, KINDLE, REPO, SOURCE
from kboat.write import upsert


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    for sub in ("Sources", "Kindles", "Repos", "Feeds"):
        (tmp_path / sub).mkdir()
    return tmp_path


def _fm(vault: Path, rel: str) -> dict[str, object]:
    return dict(parse_frontmatter((vault / rel).read_text(encoding="utf-8")))


def test_source_create_stamps_and_fills(vault: Path) -> None:
    rec = {
        "slug": "s1",
        "fields": {"type": "source", "title": "T", "url": "https://x", "source_type": "web_page"},
    }
    result = upsert(SOURCE, vault, rec, today="2026-06-13")
    assert result == {"status": "created", "slug": "s1", "path": "Sources/s1.md"}
    fm = _fm(vault, "Sources/s1.md")
    assert fm["added_date"] == "2026-06-13"  # created-stamp
    # present-required fields filled on create
    for b in ("reading", "distill", "keep", "dismiss", "blocked", "picked"):
        assert fm[b] is False
    # An empty list (block or inline) is written `[]`, so it re-reads as [].
    assert fm["filed_date"] is None and fm["topics"] == [] and fm["tags"] == []
    # present=False fields are not synthesised on create.
    assert "gemini_url" not in fm and "notebooklm_id" not in fm


def test_source_update_preserves_human_fields(vault: Path) -> None:
    upsert(
        SOURCE,
        vault,
        {
            "slug": "s1",
            "fields": {
                "type": "source",
                "url": "https://x",
                "source_type": "web_page",
                "title": "old",
            },
        },
        today="2026-06-10",
    )
    # Human marks it read and to-keep on disk.
    path = vault / "Sources" / "s1.md"
    path.write_text(
        path.read_text()
        .replace("reading: false", "reading: true")
        .replace("keep: false", "keep: true"),
        encoding="utf-8",
    )
    # Re-ingest provides only metadata (never dispositions).
    upsert(
        SOURCE,
        vault,
        {
            "slug": "s1",
            "fields": {"type": "source", "url": "https://x", "title": "new", "summary": "s"},
        },
        today="2026-06-20",
    )
    fm = _fm(vault, "Sources/s1.md")
    assert fm["reading"] is True and fm["keep"] is True  # preserved
    assert fm["title"] == "new" and fm["summary"] == "s"  # updated
    assert fm["added_date"] == "2026-06-10"  # created-stamp not bumped


def test_collision_never_overwrites(vault: Path) -> None:
    upsert(
        SOURCE,
        vault,
        {
            "slug": "s1",
            "fields": {
                "type": "source",
                "url": "https://a",
                "source_type": "web_page",
                "title": "A",
            },
        },
        today="2026-06-13",
    )
    result = upsert(
        SOURCE,
        vault,
        {"slug": "s1", "fields": {"type": "source", "url": "https://DIFFERENT", "title": "B"}},
        today="2026-06-13",
    )
    assert result["status"] == "collision"
    assert result["reason"] == "identity_differs"  # the other reason is unreadable_identity
    assert _fm(vault, "Sources/s1.md")["title"] == "A"  # untouched


def test_kindle_body_is_preserved(vault: Path) -> None:
    upsert(
        KINDLE,
        vault,
        {
            "slug": "B0",
            "fields": {
                "type": "kindle",
                "title": "Book",
                "reading_link": "https://r",
                "store_link": "https://s",
            },
            "body": "## Highlights\n\n- a quote\n",
        },
        today="2026-06-13",
    )
    assert "## Highlights\n\n- a quote" in (vault / "Kindles" / "B0.md").read_text()
    # An update without a body keeps the highlights.
    upsert(
        KINDLE,
        vault,
        {"slug": "B0", "fields": {"type": "kindle", "title": "Book 2nd ed"}},
        today="2026-06-20",
    )
    text = (vault / "Kindles" / "B0.md").read_text()
    assert "- a quote" in body_after_frontmatter(text)
    assert _fm(vault, "Kindles/B0.md")["title"] == "Book 2nd ed"


def test_repo_stamps_refreshed_every_write_added_once(vault: Path) -> None:
    base = {
        "type": "repo",
        "url": "https://github.com/o/r",
        "title": "o/r",
        "role": "lib",
        "status": "recent",
    }
    upsert(REPO, vault, {"slug": "r1", "fields": base}, today="2026-06-10")
    upsert(REPO, vault, {"slug": "r1", "fields": {**base, "status": "active"}}, today="2026-06-20")
    fm = _fm(vault, "Repos/r1.md")
    assert fm["added_date"] == "2026-06-10"  # created-stamp preserved
    assert fm["refreshed_date"] == "2026-06-20"  # refreshed every write
    assert fm["status"] == "active"


def test_repo_notes_body_preserved_on_update(vault: Path) -> None:
    base = {
        "type": "repo",
        "url": "https://github.com/o/r",
        "title": "o/r",
        "role": "lib",
        "status": "recent",
    }
    upsert(REPO, vault, {"slug": "r1", "fields": base, "body": "my hand notes"}, today="2026-06-10")
    upsert(REPO, vault, {"slug": "r1", "fields": {**base, "status": "slow"}}, today="2026-06-20")
    assert "my hand notes" in (vault / "Repos" / "r1.md").read_text()


def test_inline_list_survives_an_update_that_omits_it(vault: Path) -> None:
    base = {
        "type": "repo",
        "url": "https://github.com/o/r",
        "title": "o/r",
        "role": "lib",
        "status": "recent",
    }
    upsert(
        REPO, vault, {"slug": "r1", "fields": {**base, "topics": ["a", "b"]}}, today="2026-06-10"
    )
    # An update that does not re-provide `topics` must preserve the inline list;
    # it is carried back verbatim rather than re-rendered from the raw `[a, b]`
    # string the reader hands back for one.
    upsert(REPO, vault, {"slug": "r1", "fields": {**base, "status": "slow"}}, today="2026-06-20")
    assert "topics: [a, b]" in (vault / "Repos" / "r1.md").read_text()


_FEED_FIELDS = {
    "type": "feed",
    "title": "A post",
    "url": "https://example.com/post",
    "feed_kind": "article",
    "site_id": "ex",
    "summary": "old",
}


def test_feed_update_keeps_a_hand_added_body_and_unmodellable_frontmatter(vault: Path) -> None:
    """A `body: none` schema is where both hazards meet: nothing K-Boat writes
    goes below the fence, and a `Feeds/` note is re-written unattended on every
    forum re-remind. Neither the reader's prose nor a plugin's own properties may
    be collateral of that write."""
    upsert(FEED, vault, {"slug": "f1", "fields": _FEED_FIELDS}, today="2026-07-19")
    path = vault / "Feeds" / "f1.md"
    # The human adds properties Obsidian understands but the reader does not,
    # plus a note to self under the fence.
    path.write_text(
        path.read_text().replace(
            "---\ntype: feed",
            '---\ntype: feed\ncssclasses: ["wide"]\nmy-rating: 4\nplugin:\n  colour: red\n  pinned: true',
            1,
        )
        + "\nWhy I kept this: it answers the mapping question.\n",
        encoding="utf-8",
    )

    upsert(
        FEED,
        vault,
        {"slug": "f1", "fields": {**_FEED_FIELDS, "summary": "new"}},
        today="2026-07-20",
    )

    text = path.read_text()
    assert parse_frontmatter(text)["summary"] == "new"
    assert body_after_frontmatter(text).strip() == (
        "Why I kept this: it answers the mapping question."
    )
    for line in ("my-rating: 4", "plugin:", "  colour: red", "  pinned: true"):
        assert line in text
    # `cssclasses` is a flow list, which the reader *can* hold (as its raw text),
    # so it round-trips as a field rather than as a raw line — either way present,
    # and still a list rather than a quoted string.
    assert 'cssclasses: ["wide"]' in text


def test_a_block_list_field_written_inline_by_hand_is_not_emptied(vault: Path) -> None:
    # `topics` is a block-list field, so an inline `[a, b]` is not the shape the
    # writer emits — but reading it as "not a list" and writing `[]` would delete
    # the topics on the next unattended pass.
    upsert(
        SOURCE,
        vault,
        {
            "slug": "s3",
            "fields": {
                "type": "source",
                "title": "T",
                "url": "https://x",
                "source_type": "web_page",
            },
        },
        today="2026-06-10",
    )
    path = vault / "Sources" / "s3.md"
    path.write_text(path.read_text().replace("topics: []", "topics: [mapping, walking]"))

    upsert(SOURCE, vault, {"slug": "s3", "fields": {"type": "source", "summary": "s"}}, today="x")

    assert "topics: [mapping, walking]" in path.read_text()


def test_repeated_updates_settle_and_stop_changing_the_note(vault: Path) -> None:
    """The property behind every claim here. A first write may move hand-added
    keys below the schema's own, which have a canonical order; after that a write
    that changes nothing must change nothing, or an unattended daily routine
    degrades a note a little at a time."""
    hand_written = (
        "aliases:",
        "  - Working title",
        'cssclasses: ["wide"]',
        'marker: "[done]"',
        "my-rating: 4",
        "plugin:",
        "  colour: red",
    )
    upsert(FEED, vault, {"slug": "f4", "fields": _FEED_FIELDS}, today="2026-07-19")
    path = vault / "Feeds" / "f4.md"
    path.write_text(
        path.read_text().replace(
            "---\ntype: feed", "---\ntype: feed\n" + "\n".join(hand_written), 1
        )
        + "\nA note to self.\n",
        encoding="utf-8",
    )

    upsert(FEED, vault, {"slug": "f4", "fields": _FEED_FIELDS}, today="2026-07-20")
    settled = path.read_text()
    upsert(FEED, vault, {"slug": "f4", "fields": _FEED_FIELDS}, today="2026-07-21")

    assert path.read_text() == settled
    # Settling is not an excuse for losing anything on the way there.
    for line in hand_written:
        assert f"\n{line}\n" in settled
    assert body_after_frontmatter(settled).strip() == "A note to self."


def test_a_comment_survives_a_write_to_the_key_it_trails(vault: Path) -> None:
    # The reader's own annotation is exactly the kind of thing an unattended
    # rewrite must not quietly take with it.
    upsert(FEED, vault, {"slug": "f9", "fields": _FEED_FIELDS}, today="2026-07-19")
    path = vault / "Feeds" / "f9.md"
    path.write_text(
        path.read_text().replace("summary: old", "summary: old\n# why I kept this"),
        encoding="utf-8",
    )

    upsert(
        FEED,
        vault,
        {"slug": "f9", "fields": {**_FEED_FIELDS, "summary": "new"}},
        today="2026-07-20",
    )

    text = path.read_text()
    assert "# why I kept this" in text
    assert parse_frontmatter(text)["summary"] == "new"


def test_a_shadow_of_a_written_key_cannot_outlive_the_value_written(vault: Path) -> None:
    # `summary : Shadow` is outside the reader's key grammar, so it decodes to no
    # key at all. Carried, it would be re-emitted *after* the rendered value and
    # win on YAML's last-key-wins — the write would silently not take.
    upsert(FEED, vault, {"slug": "fa", "fields": _FEED_FIELDS}, today="2026-07-19")
    path = vault / "Feeds" / "fa.md"
    path.write_text(
        path.read_text().replace("summary: old", "summary : Shadow\nsummary: old"),
        encoding="utf-8",
    )

    upsert(
        FEED,
        vault,
        {"slug": "fa", "fields": {**_FEED_FIELDS, "summary": "new"}},
        today="2026-07-20",
    )

    assert yaml.safe_load(path.read_text().split("---\n")[1])["summary"] == "new"


@pytest.mark.parametrize(
    ("held", "why"),
    [
        ("url: >-\n  https://example.com/post", "a folded scalar the reader cannot decode"),
        ('"url": https://example.com/post', "a quoted key, outside the key grammar"),
        ("url : https://example.com/post", "a space before the colon, likewise"),
        # What Obsidian writes when a property's type is set to List — two
        # clicks. It decodes perfectly well and still cannot be compared.
        ("url:\n  - https://example.com/post", "a one-item list, not a string"),
    ],
)
def test_an_identity_the_reader_cannot_decode_refuses_the_write(
    vault: Path, held: str, why: str
) -> None:
    """The collision check exists to refuse a write, so it has to fail closed. An
    unreadable `url` is as much reason to stop as one that plainly differs —
    writing anyway would both bypass de-dup-by-identity and leave the note
    carrying the key twice."""
    upsert(FEED, vault, {"slug": "f6", "fields": _FEED_FIELDS}, today="2026-07-19")
    path = vault / "Feeds" / "f6.md"
    path.write_text(
        path.read_text().replace("url: https://example.com/post", held), encoding="utf-8"
    )
    before = path.read_text()

    result = upsert(
        FEED,
        vault,
        {"slug": "f6", "fields": {**_FEED_FIELDS, "url": "https://example.com/OTHER"}},
        today="2026-07-20",
    )

    assert result["status"] == "collision", why
    assert result["reason"] == "unreadable_identity", why
    assert path.read_text() == before, why


def test_an_empty_identity_is_a_blank_to_fill_not_a_claim_to_contradict(vault: Path) -> None:
    upsert(FEED, vault, {"slug": "f8", "fields": _FEED_FIELDS}, today="2026-07-19")
    path = vault / "Feeds" / "f8.md"
    path.write_text(path.read_text().replace("url: https://example.com/post", "url:"))

    result = upsert(FEED, vault, {"slug": "f8", "fields": _FEED_FIELDS}, today="2026-07-20")

    assert result["status"] == "updated"
    assert parse_frontmatter(path.read_text())["url"] == "https://example.com/post"


def test_an_unreadable_identity_with_nothing_to_compare_is_not_a_refusal(vault: Path) -> None:
    # Refusing needs something to refuse. A record that names no identity has no
    # claim to check, and the entry is carried back as it stands — so a note is
    # not made permanently unwritable by a hand-edit that touched nothing else.
    upsert(FEED, vault, {"slug": "f7", "fields": _FEED_FIELDS}, today="2026-07-19")
    path = vault / "Feeds" / "f7.md"
    path.write_text(
        path.read_text().replace(
            "url: https://example.com/post", "url: >-\n  https://example.com/post"
        ),
        encoding="utf-8",
    )

    result = upsert(
        FEED,
        vault,
        {"slug": "f7", "fields": {"type": "feed", "summary": "new"}},
        today="2026-07-20",
    )

    assert result["status"] == "updated"
    text = path.read_text()
    assert "url: >-\n  https://example.com/post" in text
    assert parse_frontmatter(text)["summary"] == "new"


def test_a_repeated_key_keeps_the_value_the_reader_sees(vault: Path) -> None:
    # A duplicated key is a hand-edit slip, but both Obsidian and this reader take
    # the last one — so a write that kept the first would silently change the
    # note's meaning while claiming to have changed nothing.
    upsert(FEED, vault, {"slug": "f5", "fields": _FEED_FIELDS}, today="2026-07-19")
    path = vault / "Feeds" / "f5.md"
    path.write_text(
        path.read_text().replace(
            "---\ntype: feed", "---\ntype: feed\nmy_rating: 1\nmy_rating: 9", 1
        ),
        encoding="utf-8",
    )
    before = parse_frontmatter(path.read_text())["my_rating"]

    upsert(FEED, vault, {"slug": "f5", "fields": _FEED_FIELDS}, today="2026-07-20")

    text = path.read_text()
    assert parse_frontmatter(text)["my_rating"] == before == "9"
    assert text.count("my_rating:") == 1


@pytest.mark.parametrize("blank", ["", "\n", "   "], ids=["empty", "newline", "spaces"])
def test_a_blank_body_in_the_record_does_not_erase_the_note_body(vault: Path, blank: str) -> None:
    # A skill composing a body from an extraction that came back empty must not
    # delete the highlights that are already there.
    rec = {
        "slug": "B1",
        "fields": {
            "type": "kindle",
            "title": "Book",
            "reading_link": "https://r",
            "store_link": "https://s",
        },
    }
    upsert(KINDLE, vault, {**rec, "body": "## Highlights\n\n- a quote\n"}, today="2026-06-13")

    upsert(KINDLE, vault, {**rec, "body": blank}, today="2026-06-20")

    assert "- a quote" in body_after_frontmatter((vault / "Kindles" / "B1.md").read_text())


def test_verbatim_mode_replaces_the_body_when_a_new_one_is_given(vault: Path) -> None:
    # The other half of the blank-body rule: a real body still wins, so
    # preserving is not the same as refusing to update.
    rec = {
        "slug": "B2",
        "fields": {
            "type": "kindle",
            "title": "Book",
            "reading_link": "https://r",
            "store_link": "https://s",
        },
    }
    upsert(KINDLE, vault, {**rec, "body": "## Highlights\n\n- first pass\n"}, today="2026-06-13")

    upsert(KINDLE, vault, {**rec, "body": "## Highlights\n\n- second pass\n"}, today="2026-06-20")

    body = body_after_frontmatter((vault / "Kindles" / "B2.md").read_text())
    assert "- second pass" in body and "- first pass" not in body


def test_notes_mode_keeps_prose_above_its_own_section(vault: Path) -> None:
    # `body: "notes"` owns the `## Notes` section, not the whole body: a repo
    # note's refresh runs unattended, so a preamble above the heading must not be
    # collateral of it.
    base = {
        "type": "repo",
        "url": "https://github.com/o/r",
        "title": "o/r",
        "role": "lib",
        "status": "recent",
    }
    # The preamble names the heading twice over — as a deeper heading and in
    # prose — because a substring match would split the body at one of those and
    # rearrange the reader's own words.
    preamble = (
        "### Notes on chapter 3\n\ndetail\n\nSee the ## Notes section below.\n\n"
        "```markdown\n## Notes\n\n- one point per line\n```"
    )
    upsert(REPO, vault, {"slug": "r2", "fields": base}, today="2026-06-10")
    path = vault / "Repos" / "r2.md"
    path.write_text(
        path.read_text().replace("## Notes", f"{preamble}\n\n## Notes\n\nmy notes"),
        encoding="utf-8",
    )

    upsert(REPO, vault, {"slug": "r2", "fields": base, "body": "revised notes"}, today="2026-06-20")

    body = body_after_frontmatter(path.read_text())
    assert body.strip() == f"{preamble}\n\n## Notes\n\nrevised notes"


@pytest.mark.parametrize(
    ("preamble", "why"),
    [
        ("### Notes on chapter 3\n\nSee the ## Notes section.", "a deeper heading and prose"),
        ("```markdown\n## Notes\n```", "a fenced block quoting the heading"),
        ("````markdown\n```\n## Notes\n```\n````", "a fence quoting a fence"),
        ("~~~\n## Notes\n~~~", "a tilde fence"),
        ("Paste:\n```python\nfoo()", "a fence the human never closed"),
    ],
)
def test_notes_mode_settles_whatever_the_preamble_quotes(
    vault: Path, preamble: str, why: str
) -> None:
    """The `## Notes` heading has to be found where it really is. Splitting at a
    quoted one rearranges the human's prose; failing to find the real one appends
    a second heading, and then a third — one per unattended run."""
    base = {
        "type": "repo",
        "url": "https://github.com/o/r",
        "title": "o/r",
        "role": "lib",
        "status": "recent",
    }
    upsert(REPO, vault, {"slug": "r3", "fields": base}, today="2026-06-10")
    path = vault / "Repos" / "r3.md"
    path.write_text(
        path.read_text().replace("## Notes", f"{preamble}\n\n## Notes\n\nmy notes"),
        encoding="utf-8",
    )

    for day in ("2026-06-11", "2026-06-12", "2026-06-13"):
        upsert(REPO, vault, {"slug": "r3", "fields": base}, today=day)

    # Exact equality after three passes covers both halves: a wrong split would
    # move the prose, and a heading it failed to find would be appended again.
    assert body_after_frontmatter(path.read_text()).strip() == (
        f"{preamble}\n\n## Notes\n\nmy notes"
    ), why


def test_a_none_schema_still_refuses_to_author_a_body(vault: Path) -> None:
    # Preserving a body is not the same as accepting one: `body: none` says the
    # writer authors none, so a record that supplies one is not honoured.
    upsert(
        FEED,
        vault,
        {"slug": "f2", "fields": _FEED_FIELDS, "body": "text K-Boat should not write"},
        today="2026-07-19",
    )
    assert body_after_frontmatter((vault / "Feeds" / "f2.md").read_text()).strip() == ""


def test_a_field_the_record_provides_wins_over_its_unmodellable_block(vault: Path) -> None:
    # `summary` held as a block scalar cannot survive alongside the value being
    # written, or the note would carry the key twice and Obsidian would read one
    # of them arbitrarily.
    upsert(FEED, vault, {"slug": "f3", "fields": _FEED_FIELDS}, today="2026-07-19")
    path = vault / "Feeds" / "f3.md"
    path.write_text(
        path.read_text().replace("summary: old", "summary: |\n  a hand-written\n  block scalar"),
        encoding="utf-8",
    )

    upsert(
        FEED,
        vault,
        {"slug": "f3", "fields": {**_FEED_FIELDS, "summary": "new"}},
        today="2026-07-20",
    )

    text = path.read_text()
    assert text.count("summary:") == 1
    assert parse_frontmatter(text)["summary"] == "new"


def test_source_body_survives_a_metadata_backfill(vault: Path) -> None:
    upsert(
        SOURCE,
        vault,
        {
            "slug": "s2",
            "fields": {
                "type": "source",
                "title": "T",
                "url": "https://x",
                "source_type": "web_page",
            },
        },
        today="2026-06-10",
    )
    path = vault / "Sources" / "s2.md"
    path.write_text(path.read_text() + "\n## My reading notes\n\n- a thought\n", encoding="utf-8")

    upsert(
        SOURCE,
        vault,
        {"slug": "s2", "fields": {"type": "source", "summary": "s", "topics": ["mapping"]}},
        today="2026-06-20",
    )

    text = path.read_text()
    assert "- a thought" in body_after_frontmatter(text)
    fm = parse_frontmatter(text)
    assert fm["summary"] == "s" and fm["topics"] == ["mapping"]
