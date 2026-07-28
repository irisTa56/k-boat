"""Tests for `kboat.write.upsert` — create/update, merge-preserve, stamps, body."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from kboat.frontmatter import body_after_frontmatter, parse_frontmatter
from kboat.naming import note_slug
from kboat.schema import FEED, KINDLE, REPO, SOURCE
from kboat.write import BadInputError, upsert

# The writer verifies a record's slug against its `url`, so a fixture names its
# note the way a real writer does. One URL per note type is enough — each test
# gets its own vault, so nothing here has to hold two notes of one type apart.
SOURCE_URL = "https://x"
FEED_URL = "https://example.com/post"
REPO_URL = "https://github.com/o/r"
S = note_slug(SOURCE_URL)
F = note_slug(FEED_URL)
R = note_slug(REPO_URL)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    for sub in ("Sources", "Kindles", "Repos", "Feeds"):
        (tmp_path / sub).mkdir()
    return tmp_path


def _fm(vault: Path, rel: str) -> dict[str, object]:
    return dict(parse_frontmatter((vault / rel).read_text(encoding="utf-8")))


def test_source_create_stamps_and_fills(vault: Path) -> None:
    rec = {
        "slug": S,
        "fields": {"type": "source", "title": "T", "url": SOURCE_URL, "source_type": "web_page"},
    }
    result = upsert(SOURCE, vault, rec, today="2026-06-13")
    assert result == {"status": "created", "slug": S, "path": f"Sources/{S}.md"}
    fm = _fm(vault, f"Sources/{S}.md")
    assert fm["added_date"] == "2026-06-13"  # created-stamp
    # present-required fields filled on create
    for b in ("reading", "distill", "keep", "dismiss", "blocked", "picked"):
        assert fm[b] is False
    # An empty list (block or inline) is written `[]`, so it re-reads as [].
    assert fm["filed_date"] is None and fm["topics"] == [] and fm["tags"] == []
    # present=False fields are not synthesised on create.
    assert "gemini_url" not in fm and "notebooklm_id" not in fm


def test_an_update_does_not_backfill_a_field_the_note_has_lost(vault: Path) -> None:
    # The create-time fill is create-time only. Backfilling on update would mean
    # re-rendering fields the write was never about, which is exactly what
    # protects the note's unmodelled properties — so drift is left for
    # `kboat-validate` to report and a human to repair, not silently patched.
    upsert(SOURCE, vault, {"slug": "s9", "fields": {"type": "source"}}, today="2026-06-13")
    path = vault / "Sources" / "s9.md"
    path.write_text(
        "\n".join(line for line in path.read_text().splitlines() if not line.startswith("reading:"))
        + "\n"
    )

    upsert(SOURCE, vault, {"slug": "s9", "fields": {"title": "T"}}, today="2026-06-20")

    fm = _fm(vault, "Sources/s9.md")
    assert "reading" not in fm
    assert fm["title"] == "T"


def test_source_update_preserves_human_fields(vault: Path) -> None:
    upsert(
        SOURCE,
        vault,
        {
            "slug": S,
            "fields": {
                "type": "source",
                "url": SOURCE_URL,
                "source_type": "web_page",
                "title": "old",
            },
        },
        today="2026-06-10",
    )
    # Human marks it read and to-keep on disk.
    path = vault / "Sources" / f"{S}.md"
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
            "slug": S,
            "fields": {"type": "source", "url": SOURCE_URL, "title": "new", "summary": "s"},
        },
        today="2026-06-20",
    )
    fm = _fm(vault, f"Sources/{S}.md")
    assert fm["reading"] is True and fm["keep"] is True  # preserved
    assert fm["title"] == "new" and fm["summary"] == "s"  # updated
    assert fm["added_date"] == "2026-06-10"  # created-stamp not bumped


def test_collision_never_overwrites(vault: Path) -> None:
    # Two URLs hashing to one slug is what this refuses, and no pair of URLs can
    # be found that does — so the clash is staged: the note for A is moved to the
    # slug B names, which is exactly the state a real 48-bit clash would leave.
    other = "https://DIFFERENT"
    upsert(
        SOURCE,
        vault,
        {
            "slug": note_slug("https://a"),
            "fields": {
                "type": "source",
                "url": "https://a",
                "source_type": "web_page",
                "title": "A",
            },
        },
        today="2026-06-13",
    )
    clashing = note_slug(other)
    (vault / "Sources" / f"{note_slug('https://a')}.md").rename(
        vault / "Sources" / f"{clashing}.md"
    )

    result = upsert(
        SOURCE,
        vault,
        {"slug": clashing, "fields": {"type": "source", "url": other, "title": "B"}},
        today="2026-06-13",
    )
    assert result["status"] == "collision"
    assert result["reason"] == "identity_differs"  # the other reason is unreadable_identity
    assert _fm(vault, f"Sources/{clashing}.md")["title"] == "A"  # untouched


@pytest.mark.parametrize(
    ("again", "why"),
    [
        ("https://x/", "a trailing slash"),
        ("https://x?utm_source=rss", "a feed link's tracking parameter"),
        ("https://x#intro", "a fragment"),
    ],
)
def test_the_same_page_reached_by_another_link_updates_rather_than_collides(
    vault: Path, again: str, why: str
) -> None:
    # The slug is the canonical URL's hash, so a second link to one page lands on
    # the note that page already has. Comparing the stored strings verbatim would
    # read that as a 48-bit clash and refuse — leaving a re-captured article stuck
    # in the queue, re-reported every run as a hash collision that never happened.
    upsert(
        SOURCE,
        vault,
        {
            "slug": S,
            "fields": {"type": "source", "url": SOURCE_URL, "title": "A", "notebooklm_id": "nb1"},
        },
        today="2026-06-13",
    )

    result = upsert(
        SOURCE,
        vault,
        {"slug": S, "fields": {"type": "source", "url": again, "title": "B"}},
        today="2026-06-20",
    )

    assert result["status"] == "updated", why
    fm = _fm(vault, f"Sources/{S}.md")
    assert fm["title"] == "B", why
    # The identity the note was created with is what it keeps: it is the
    # provenance, and the string a notebook's source is resolved by matching.
    assert fm["url"] == SOURCE_URL, why


def test_a_slug_the_url_does_not_name_is_refused(vault: Path) -> None:
    # The name and the identity are one fact, so the writer recomputes it rather
    # than trusting the record: a note filed anywhere else is a second identity
    # for the same page, and nothing later can tell that from a distinct page.
    result = upsert(
        SOURCE,
        vault,
        {
            "slug": "hand-picked",
            "fields": {"type": "source", "title": "T", "url": SOURCE_URL},
        },
        today="2026-07-25",
    )

    assert result == {
        "status": "slug_mismatch",
        "identity": "url",
        "url": SOURCE_URL,
        "expected": S,
        "got": "hand-picked",
    }
    assert list(vault.rglob("*.md")) == []


def test_a_stale_slug_is_refused_before_the_note_at_it_is_read(vault: Path) -> None:
    # The wrong slug names the wrong file, so checking it after reading would run
    # the collision check against a note the record was never about.
    (vault / "Sources" / "hand-picked.md").write_text(
        "---\ntype: source\nurl: https://somewhere/else\n---\nprose\n", encoding="utf-8"
    )

    result = upsert(
        SOURCE,
        vault,
        {"slug": "hand-picked", "fields": {"type": "source", "url": SOURCE_URL}},
        today="2026-07-25",
    )

    assert result["status"] == "slug_mismatch"  # not "collision"
    assert "prose" in (vault / "Sources" / "hand-picked.md").read_text()


def test_a_record_that_names_no_url_asks_nothing_about_identity(vault: Path) -> None:
    # A later write filling in `summary` carries no `url`, and an uploaded PDF
    # has none at all — neither makes a claim the writer could check.
    upsert(SOURCE, vault, {"slug": "handmade", "fields": {"type": "source"}}, today="2026-07-25")

    result = upsert(
        SOURCE, vault, {"slug": "handmade", "fields": {"summary": "s"}}, today="2026-07-26"
    )

    assert result["status"] == "updated"
    assert _fm(vault, "Sources/handmade.md")["summary"] == "s"


def test_an_identity_that_will_not_parse_still_refuses_the_write(vault: Path) -> None:
    # The comparison falls back to an exact match when the note's own url cannot
    # be canonicalized, so a hand-broken identity refuses rather than raising —
    # the check exists to refuse, so it fails closed.
    (vault / "Sources" / f"{S}.md").write_text(
        "---\ntype: source\nurl: https://x:99999/a\n---\n", encoding="utf-8"
    )

    result = upsert(
        SOURCE, vault, {"slug": S, "fields": {"type": "source", "url": SOURCE_URL}}, today="x"
    )

    assert result["status"] == "collision"
    assert result["reason"] == "identity_differs"


def test_a_kindle_slug_is_never_recomputed(vault: Path) -> None:
    # An ASIN is an id, derived from nothing, so there is no oracle to check it
    # against — and `url` is not even a field of the schema.
    result = upsert(
        KINDLE,
        vault,
        {
            "slug": "B00TEST",
            "fields": {
                "type": "kindle",
                "title": "Book",
                "reading_link": "https://r",
                "store_link": "https://s",
            },
        },
        today="2026-07-25",
    )

    assert result["status"] == "created"


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
        "url": REPO_URL,
        "title": "o/r",
        "role": "lib",
        "status": "recent",
    }
    upsert(REPO, vault, {"slug": R, "fields": base}, today="2026-06-10")
    upsert(REPO, vault, {"slug": R, "fields": {**base, "status": "active"}}, today="2026-06-20")
    fm = _fm(vault, f"Repos/{R}.md")
    assert fm["added_date"] == "2026-06-10"  # created-stamp preserved
    assert fm["refreshed_date"] == "2026-06-20"  # refreshed every write
    assert fm["status"] == "active"


def test_repo_notes_body_preserved_on_update(vault: Path) -> None:
    base = {
        "type": "repo",
        "url": REPO_URL,
        "title": "o/r",
        "role": "lib",
        "status": "recent",
    }
    upsert(REPO, vault, {"slug": R, "fields": base, "body": "my hand notes"}, today="2026-06-10")
    upsert(REPO, vault, {"slug": R, "fields": {**base, "status": "slow"}}, today="2026-06-20")
    assert "my hand notes" in (vault / "Repos" / f"{R}.md").read_text()


def test_inline_list_survives_an_update_that_omits_it(vault: Path) -> None:
    base = {
        "type": "repo",
        "url": REPO_URL,
        "title": "o/r",
        "role": "lib",
        "status": "recent",
    }
    upsert(REPO, vault, {"slug": R, "fields": {**base, "topics": ["a", "b"]}}, today="2026-06-10")
    # An update that does not re-provide `topics` must preserve the inline list;
    # it is carried back verbatim rather than re-rendered from the raw `[a, b]`
    # string the reader hands back for one.
    upsert(REPO, vault, {"slug": R, "fields": {**base, "status": "slow"}}, today="2026-06-20")
    assert "topics: [a, b]" in (vault / "Repos" / f"{R}.md").read_text()


_FEED_FIELDS = {
    "type": "feed",
    "title": "A post",
    "url": FEED_URL,
    "feed_kind": "article",
    "site_id": "ex",
    "summary": "old",
}


def test_feed_update_keeps_a_hand_added_body_and_unmodellable_frontmatter(vault: Path) -> None:
    """A `body: none` schema is where both hazards meet: nothing K-Boat writes
    goes below the fence, and a `Feeds/` note is re-written unattended on every
    forum re-remind. Neither the reader's prose nor a plugin's own properties may
    be collateral of that write."""
    upsert(FEED, vault, {"slug": F, "fields": _FEED_FIELDS}, today="2026-07-19")
    path = vault / "Feeds" / f"{F}.md"
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
        {"slug": F, "fields": {**_FEED_FIELDS, "summary": "new"}},
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
            "slug": S,
            "fields": {
                "type": "source",
                "title": "T",
                "url": SOURCE_URL,
                "source_type": "web_page",
            },
        },
        today="2026-06-10",
    )
    path = vault / "Sources" / f"{S}.md"
    path.write_text(path.read_text().replace("topics: []", "topics: [mapping, walking]"))

    upsert(SOURCE, vault, {"slug": S, "fields": {"type": "source", "summary": "s"}}, today="x")

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
    upsert(FEED, vault, {"slug": F, "fields": _FEED_FIELDS}, today="2026-07-19")
    path = vault / "Feeds" / f"{F}.md"
    path.write_text(
        path.read_text().replace(
            "---\ntype: feed", "---\ntype: feed\n" + "\n".join(hand_written), 1
        )
        + "\nA note to self.\n",
        encoding="utf-8",
    )

    upsert(FEED, vault, {"slug": F, "fields": _FEED_FIELDS}, today="2026-07-20")
    settled = path.read_text()
    upsert(FEED, vault, {"slug": F, "fields": _FEED_FIELDS}, today="2026-07-21")

    assert path.read_text() == settled
    # Settling is not an excuse for losing anything on the way there.
    for line in hand_written:
        assert f"\n{line}\n" in settled
    assert body_after_frontmatter(settled).strip() == "A note to self."


def test_a_comment_survives_a_write_to_the_key_it_trails(vault: Path) -> None:
    # The reader's own annotation is exactly the kind of thing an unattended
    # rewrite must not quietly take with it.
    upsert(FEED, vault, {"slug": F, "fields": _FEED_FIELDS}, today="2026-07-19")
    path = vault / "Feeds" / f"{F}.md"
    path.write_text(
        path.read_text().replace("summary: old", "summary: old\n# why I kept this"),
        encoding="utf-8",
    )

    upsert(
        FEED,
        vault,
        {"slug": F, "fields": {**_FEED_FIELDS, "summary": "new"}},
        today="2026-07-20",
    )

    text = path.read_text()
    assert "# why I kept this" in text
    assert parse_frontmatter(text)["summary"] == "new"


def test_a_shadow_of_a_written_key_cannot_outlive_the_value_written(vault: Path) -> None:
    # `summary : Shadow` is outside the reader's key grammar, so it decodes to no
    # key at all. Carried, it would be re-emitted *after* the rendered value and
    # win on YAML's last-key-wins — the write would silently not take.
    upsert(FEED, vault, {"slug": F, "fields": _FEED_FIELDS}, today="2026-07-19")
    path = vault / "Feeds" / f"{F}.md"
    path.write_text(
        path.read_text().replace("summary: old", "summary : Shadow\nsummary: old"),
        encoding="utf-8",
    )

    upsert(
        FEED,
        vault,
        {"slug": F, "fields": {**_FEED_FIELDS, "summary": "new"}},
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
    carrying the key twice.

    The record names the very URL the note was written from, so nothing but the
    unreadable shape is left to refuse it."""
    upsert(FEED, vault, {"slug": F, "fields": _FEED_FIELDS}, today="2026-07-19")
    path = vault / "Feeds" / f"{F}.md"
    path.write_text(
        path.read_text().replace("url: https://example.com/post", held), encoding="utf-8"
    )
    before = path.read_text()

    result = upsert(FEED, vault, {"slug": F, "fields": _FEED_FIELDS}, today="2026-07-20")

    assert result["status"] == "collision", why
    assert result["reason"] == "unreadable_identity", why
    assert path.read_text() == before, why


def test_an_empty_identity_is_a_blank_to_fill_not_a_claim_to_contradict(vault: Path) -> None:
    upsert(FEED, vault, {"slug": F, "fields": _FEED_FIELDS}, today="2026-07-19")
    path = vault / "Feeds" / f"{F}.md"
    path.write_text(path.read_text().replace("url: https://example.com/post", "url:"))

    result = upsert(FEED, vault, {"slug": F, "fields": _FEED_FIELDS}, today="2026-07-20")

    assert result["status"] == "updated"
    assert parse_frontmatter(path.read_text())["url"] == "https://example.com/post"


def test_an_unreadable_identity_with_nothing_to_compare_is_not_a_refusal(vault: Path) -> None:
    # Refusing needs something to refuse. A record that names no identity has no
    # claim to check, and the entry is carried back as it stands — so a note is
    # not made permanently unwritable by a hand-edit that touched nothing else.
    upsert(FEED, vault, {"slug": F, "fields": _FEED_FIELDS}, today="2026-07-19")
    path = vault / "Feeds" / f"{F}.md"
    path.write_text(
        path.read_text().replace(
            "url: https://example.com/post", "url: >-\n  https://example.com/post"
        ),
        encoding="utf-8",
    )

    result = upsert(
        FEED,
        vault,
        {"slug": F, "fields": {"type": "feed", "summary": "new"}},
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
    upsert(FEED, vault, {"slug": F, "fields": _FEED_FIELDS}, today="2026-07-19")
    path = vault / "Feeds" / f"{F}.md"
    path.write_text(
        path.read_text().replace(
            "---\ntype: feed", "---\ntype: feed\nmy_rating: 1\nmy_rating: 9", 1
        ),
        encoding="utf-8",
    )
    before = parse_frontmatter(path.read_text())["my_rating"]

    upsert(FEED, vault, {"slug": F, "fields": _FEED_FIELDS}, today="2026-07-20")

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
        "url": REPO_URL,
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
    upsert(REPO, vault, {"slug": R, "fields": base}, today="2026-06-10")
    path = vault / "Repos" / f"{R}.md"
    path.write_text(
        path.read_text().replace("## Notes", f"{preamble}\n\n## Notes\n\nmy notes"),
        encoding="utf-8",
    )

    upsert(REPO, vault, {"slug": R, "fields": base, "body": "revised notes"}, today="2026-06-20")

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
        "url": REPO_URL,
        "title": "o/r",
        "role": "lib",
        "status": "recent",
    }
    upsert(REPO, vault, {"slug": R, "fields": base}, today="2026-06-10")
    path = vault / "Repos" / f"{R}.md"
    path.write_text(
        path.read_text().replace("## Notes", f"{preamble}\n\n## Notes\n\nmy notes"),
        encoding="utf-8",
    )

    for day in ("2026-06-11", "2026-06-12", "2026-06-13"):
        upsert(REPO, vault, {"slug": R, "fields": base}, today=day)

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
        {"slug": F, "fields": _FEED_FIELDS, "body": "text K-Boat should not write"},
        today="2026-07-19",
    )
    assert body_after_frontmatter((vault / "Feeds" / f"{F}.md").read_text()).strip() == ""


def test_a_field_the_record_provides_wins_over_its_unmodellable_block(vault: Path) -> None:
    # `summary` held as a block scalar cannot survive alongside the value being
    # written, or the note would carry the key twice and Obsidian would read one
    # of them arbitrarily.
    upsert(FEED, vault, {"slug": F, "fields": _FEED_FIELDS}, today="2026-07-19")
    path = vault / "Feeds" / f"{F}.md"
    path.write_text(
        path.read_text().replace("summary: old", "summary: |\n  a hand-written\n  block scalar"),
        encoding="utf-8",
    )

    upsert(
        FEED,
        vault,
        {"slug": F, "fields": {**_FEED_FIELDS, "summary": "new"}},
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
            "slug": S,
            "fields": {
                "type": "source",
                "title": "T",
                "url": SOURCE_URL,
                "source_type": "web_page",
            },
        },
        today="2026-06-10",
    )
    path = vault / "Sources" / f"{S}.md"
    path.write_text(path.read_text() + "\n## My reading notes\n\n- a thought\n", encoding="utf-8")

    upsert(
        SOURCE,
        vault,
        {"slug": S, "fields": {"type": "source", "summary": "s", "topics": ["mapping"]}},
        today="2026-06-20",
    )

    text = path.read_text()
    assert "- a thought" in body_after_frontmatter(text)
    fm = parse_frontmatter(text)
    assert fm["summary"] == "s" and fm["topics"] == ["mapping"]


@pytest.mark.parametrize(
    ("slug", "why"),
    [
        ("../../evil", "a traversal out of the type's own directory"),
        ("Sources/s1", "a separator, which names a directory the type does not own"),
        ("a\\b", "the other platform's separator, which macOS keeps in the name"),
        ("a:b", "a character the vault's own naming rule forbids in a filename"),
        ("a\nb", "a name that is two lines"),
        (".hidden", "a leading dot, which the vault does not show"),
        ("", "no name at all"),
        ("   ", "nor whitespace"),
        (" a ", "a name the writer would have to change to write, and then misreport"),
        (None, "a record with no slug key reads as this one"),
        (7, "a slug that is not even text"),
    ],
)
def test_a_slug_that_is_no_filename_is_refused(vault: Path, slug: object, why: str) -> None:
    # `upsert` interpolates the slug into the note's path, and every caller
    # assembles that slug — so this is where a record that would write outside
    # its type's directory has to stop, for every note type at once.
    with pytest.raises(BadInputError):
        upsert(SOURCE, vault, {"slug": slug, "fields": {"type": "source", "title": "T"}}, today="x")

    assert list(vault.rglob("*.md")) == [], why
    assert list(vault.parent.glob("*.md")) == [], why  # nor outside it


def test_a_slug_may_hold_a_dot_that_does_not_lead(vault: Path) -> None:
    # The rule is about what a slug can reach, not about the characters in it:
    # a caller-chosen name is free to carry a dot anywhere else.
    result = upsert(
        SOURCE,
        vault,
        {"slug": "a.b", "fields": {"type": "source", "title": "T"}},
        today="2026-07-25",
    )
    assert result["status"] == "created"
    assert (vault / "Sources" / "a.b.md").exists()


def test_a_block_list_the_field_cannot_hold_still_settles(vault: Path) -> None:
    # The same fixpoint through the merge path, for the other list style: a null
    # item written into a block list is read back and re-written unchanged, so
    # it does not move the note a little on every unattended run.
    fields = {
        "type": "source",
        "title": "T",
        "url": SOURCE_URL,
        "source_type": "web_page",
        "topics": ["a", None],
    }
    upsert(SOURCE, vault, {"slug": S, "fields": fields}, today="2026-07-25")
    path = vault / "Sources" / f"{S}.md"
    first = path.read_text()

    upsert(SOURCE, vault, {"slug": S, "fields": dict(parse_frontmatter(first))}, today="2026-07-25")

    assert path.read_text() == first
    assert parse_frontmatter(first)["topics"] == ["a", ""]


def test_a_value_the_field_cannot_hold_still_settles(vault: Path) -> None:
    # The write contract's fixpoint, over exactly the values that have no valid
    # rendering: what the writer emits it reads back as itself, so the second
    # write is handed what the first one wrote and emits it again. Otherwise a
    # note holding one drifts every day, with no edit behind it.
    hostile = {
        "type": "repo",
        "url": REPO_URL,
        "title": "o/r",
        "stars": "a: b",
        "topics": "not a list",
        "domain": ["ok", None],
    }
    upsert(REPO, vault, {"slug": R, "fields": hostile}, today="2026-07-25")
    path = vault / "Repos" / f"{R}.md"
    first = path.read_text()

    # Re-written from what the note now holds, as a refresh reads it back.
    upsert(REPO, vault, {"slug": R, "fields": dict(parse_frontmatter(first))}, today="2026-07-25")

    assert path.read_text() == first
    assert yaml.safe_load(first.split("---\n")[1])["stars"] == "a: b"


@pytest.mark.parametrize(
    ("key", "why"),
    [
        ("a: b", "a name that would end the entry in the middle of itself"),
        ("weird\n---\nkey", "one that would close the frontmatter fence early"),
        ("", "a name that is no name"),
        ("my-rating", "one outside the reader's grammar, so written and then unreadable"),
        ("summary\n", "a newline at the very end, which a `$` anchor would have let through"),
    ],
)
def test_a_field_name_that_is_no_property_key_is_refused(vault: Path, key: str, why: str) -> None:
    # A wrong value is quoted and costs its own field. A name has no such
    # fallback — quoted, the property it writes is one the reader cannot decode
    # and `kboat-validate` cannot see — so the record is refused whole.
    with pytest.raises(BadInputError):
        upsert(
            SOURCE,
            vault,
            {"slug": "s9", "fields": {"type": "source", "title": "T", key: "v"}},
            today="2026-07-25",
        )

    assert not (vault / "Sources" / "s9.md").exists(), why


def test_a_hand_added_property_the_writer_would_refuse_still_survives_a_write(vault: Path) -> None:
    # The refusal is about what a record may *name*, not about what the note may
    # hold: a property the human wrote under a name of their own is carried back
    # untouched, which is the whole point of re-rendering only what changes.
    fields = {"type": "source", "title": "T", "url": SOURCE_URL, "source_type": "web_page"}
    upsert(SOURCE, vault, {"slug": S, "fields": fields}, today="2026-07-25")
    path = vault / "Sources" / f"{S}.md"
    path.write_text(path.read_text().replace("---\ntype:", "---\nmy-rating: 4\ntype:", 1))

    upsert(SOURCE, vault, {"slug": S, "fields": {"summary": "s"}}, today="2026-07-26")

    assert "\nmy-rating: 4\n" in path.read_text()


@pytest.mark.parametrize(
    ("break_char", "why"),
    [
        ("\u2028", "the Unicode line separator"),
        ("\u2029", "the paragraph separator"),
        ("\x85", "NEL"),
        ("\x0b", "the vertical tab"),
        ("\x1e", "the record separator"),
        ("\n", "and the ordinary newline"),
    ],
)
def test_a_line_break_inside_a_value_cannot_become_a_property(
    vault: Path, break_char: str, why: str
) -> None:
    # `summary` is written from model output over page text, and `title` from a
    # captured page — untrusted both. A break character in one used to end the
    # line here (though not for YAML or Obsidian), so its tail arrived as a
    # property, overwriting the note's own `url` identity on an unattended run.
    real = "https://real/a"
    hostile = f"a summary{break_char}url: https://evil/x{break_char}dismiss: true"
    # No other flow special in the item, so quoting it is the break's own doing.
    item = f"topic{break_char}tail"
    upsert(
        SOURCE,
        vault,
        {
            "slug": note_slug(real),
            "fields": {
                "type": "source",
                "title": "T",
                "url": real,
                "source_type": "web_page",
                "summary": hostile,
                "topics": ["a", item],  # block style
                "tags": ["a", item],  # inline style, quoted by the stricter rule
            },
        },
        today="2026-07-26",
    )

    text = (vault / "Sources" / f"{note_slug(real)}.md").read_text()
    fm = parse_frontmatter(text)
    assert fm["summary"] == hostile, why  # kept whole, in its own field
    assert fm["topics"] == ["a", item], why  # and so is a list item, either style
    assert fm["url"] == real, why  # the identity is untouched
    assert fm["dismiss"] is False, why  # and no disposition was invented
    # The note says the same to a reader that is not this one.
    loaded = yaml.safe_load(text.split("---\n")[1])
    assert loaded["url"] == real, why
    assert loaded["summary"] == hostile and loaded["topics"] == ["a", item], why
    assert loaded["tags"] == ["a", item], why
