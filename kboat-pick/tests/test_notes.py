"""Tests for frontmatter reading and the scoped `picked` rewrite."""

from __future__ import annotations

import pytest

from kboat_pick.notes import FrontmatterError, parse_frontmatter, set_picked

NOTE = """\
---
type: source
title: "A: colon title"
source_type: web_page
url: https://example.com/a
summary: "One sentence."
topics:
  - "Apache Iceberg"
  - 隠れたパーティショニング
added_date: 2026-06-01
blocked: false
picked: false
notebooklm_id: abc-123
---
"""


def test_parse_scalars_and_lists() -> None:
    fm = parse_frontmatter(NOTE)
    assert fm["type"] == "source"
    assert fm["title"] == "A: colon title"  # quotes stripped, inner colon kept
    assert fm["summary"] == "One sentence."
    assert fm["blocked"] is False
    assert fm["picked"] is False
    assert fm["topics"] == ["Apache Iceberg", "隠れたパーティショニング"]


def test_parse_empty_and_inline_empty_list() -> None:
    fm = parse_frontmatter("---\nsummary:\ntopics: []\nfiled_date:\n---\n")
    assert fm["summary"] is None
    assert fm["topics"] == []
    assert fm["filed_date"] is None


def test_parse_requires_fence() -> None:
    with pytest.raises(FrontmatterError):
        parse_frontmatter("no frontmatter here\n")


def test_set_picked_rewrites_existing_line() -> None:
    out = set_picked(NOTE, True)
    assert "picked: true" in out
    assert "picked: false" not in out
    # Only the picked line changed; neighbours intact.
    assert "blocked: false" in out
    assert "notebooklm_id: abc-123" in out
    assert parse_frontmatter(out)["picked"] is True
    # Idempotent round-trip back to false.
    assert parse_frontmatter(set_picked(out, False))["picked"] is False


def test_set_picked_inserts_after_blocked_when_absent() -> None:
    note = "---\ntype: source\nblocked: false\nnotebooklm_id: x\n---\n"
    out = set_picked(note, True)
    lines = out.splitlines()
    assert lines[lines.index("blocked: false") + 1] == "picked: true"
    assert parse_frontmatter(out)["picked"] is True


def test_set_picked_preserves_body() -> None:
    note = NOTE + "\nsome body text\n"
    out = set_picked(note, True)
    assert out.endswith("some body text\n")
