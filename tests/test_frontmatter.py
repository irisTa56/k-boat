"""Tests for the shared frontmatter primitives' public helpers."""

from __future__ import annotations

import pytest

from kboat.frontmatter import (
    FrontmatterError,
    body_after_frontmatter,
    strip_frontmatter,
)


def test_strip_frontmatter_returns_body_when_block_present() -> None:
    text = "---\ntags: [daily]\n---\n\n## Notes\n- a\n"
    assert strip_frontmatter(text) == "\n## Notes\n- a\n"


def test_strip_frontmatter_keeps_thematic_break_in_body() -> None:
    # Only the first `---` after the opener closes the block; a later `---`
    # thematic break stays in the returned body.
    text = "---\ntags: x\n---\nintro\n\n---\n\nmore\n"
    assert strip_frontmatter(text) == "intro\n\n---\n\nmore\n"


def test_strip_frontmatter_handles_crlf() -> None:
    text = "---\r\ntags: x\r\n---\r\nbody\r\n"
    assert strip_frontmatter(text) == "body\r\n"


def test_strip_frontmatter_tolerates_whitespace_around_fence() -> None:
    # Deliberate widening over the old dailynotes scanner: fences are matched via
    # the module's shared `_fence_bounds` (`.strip() == "---"`), so surrounding
    # whitespace on a fence line is tolerated rather than treated as "not a fence".
    assert strip_frontmatter("  ---\ntags: x\n  ---\nbody\n") == "body\n"
    assert strip_frontmatter("--- \ntags: x\n--- \nbody\n") == "body\n"


def test_strip_frontmatter_passthrough_without_opening_fence() -> None:
    # No leading `---`: not frontmatter, returned whole (the lenient contract).
    text = "just a plain line\n"
    assert strip_frontmatter(text) == text


def test_strip_frontmatter_passthrough_when_fence_unclosed() -> None:
    # A leading `---` with no closing fence is not frontmatter; keep it whole,
    # where body_after_frontmatter would raise.
    text = "---\nstray dashes, then prose\n"
    assert strip_frontmatter(text) == text
    with pytest.raises(FrontmatterError):
        body_after_frontmatter(text)


def test_strip_frontmatter_passthrough_on_empty_text() -> None:
    assert strip_frontmatter("") == ""
