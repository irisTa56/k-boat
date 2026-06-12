"""Tests for `kboat.write.upsert` — create/update, merge-preserve, stamps, body."""

from __future__ import annotations

from pathlib import Path

import pytest

from kboat.frontmatter import body_after_frontmatter, parse_frontmatter
from kboat.schema import KINDLE, REPO, SOURCE
from kboat.write import upsert


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    for sub in ("Sources", "Kindles", "Repos"):
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
    # An empty block list is written bare (`topics:`), so it re-reads as None.
    assert fm["filed_date"] is None and fm["topics"] is None and fm["tags"] == []
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
    # An update that does not re-provide `topics` must preserve the inline list
    # (it re-reads as the raw `[a, b]` string and is passed through unchanged).
    upsert(REPO, vault, {"slug": "r1", "fields": {**base, "status": "slow"}}, today="2026-06-20")
    assert "topics: [a, b]" in (vault / "Repos" / "r1.md").read_text()
