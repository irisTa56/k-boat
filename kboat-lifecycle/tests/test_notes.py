"""Tests for frontmatter parsing and the filed_date line edit."""

import pytest

from kboat_lifecycle.notes import FrontmatterError, parse_frontmatter, set_filed_date

# A real-shaped source note (frontmatter only), used across the tests.
NOTE = """\
---
type: source
title: "Heterogeneous Federated Learning: State-of-the-art"
reading_link: "[[1f1db63db5f4.pdf]]"
reading: false
distill: true
keep: false
dismiss: false
source_type: pdf
url: https://arxiv.org/pdf/2307.10616
summary: "実用的な連合学習で不可避となるヘテロジニティ問題のサーベイ。"
topics:
  - 異種連邦学習
  - 統計的不均一性
added_date: 2026-06-05
filed_date:
distilled_date:
blocked: false
notebooklm_id: 794637b7-8d9d-44e5-a745-eaff627033cd
tags: []
---
"""


class TestParseFrontmatter:
    def test_scalars_and_booleans(self):
        fm = parse_frontmatter(NOTE)
        assert fm["type"] == "source"
        assert fm["distill"] is True
        assert fm["blocked"] is False
        assert fm["source_type"] == "pdf"

    def test_empty_field_is_none(self):
        fm = parse_frontmatter(NOTE)
        assert fm["filed_date"] is None
        assert fm["distilled_date"] is None

    def test_quoted_title_with_colon_kept_whole(self):
        fm = parse_frontmatter(NOTE)
        assert fm["title"] == "Heterogeneous Federated Learning: State-of-the-art"

    def test_url_unquoted(self):
        fm = parse_frontmatter(NOTE)
        assert fm["url"] == "https://arxiv.org/pdf/2307.10616"

    def test_list_value_skipped_not_misparsed(self):
        fm = parse_frontmatter(NOTE)
        # `topics:` is a list; it collapses to None and its `- ...` items do not
        # leak in as bogus keys.
        assert fm["topics"] is None
        assert "異種連邦学習" not in fm

    def test_missing_fence_raises(self):
        with pytest.raises(FrontmatterError):
            parse_frontmatter("no frontmatter here\n")

    def test_unclosed_fence_raises(self):
        with pytest.raises(FrontmatterError):
            parse_frontmatter("---\ntype: source\n")


class TestSetFiledDate:
    def test_stamp_only_changes_filed_date(self):
        out = set_filed_date(NOTE, "2026-06-05")
        assert "filed_date: 2026-06-05" in out
        # Every other line is untouched.
        before = [ln for ln in NOTE.splitlines() if not ln.startswith("filed_date:")]
        after = [ln for ln in out.splitlines() if not ln.startswith("filed_date:")]
        assert before == after

    def test_clear_round_trips(self):
        stamped = set_filed_date(NOTE, "2026-06-05")
        cleared = set_filed_date(stamped, None)
        assert cleared == NOTE

    def test_does_not_touch_distilled_date(self):
        out = set_filed_date(NOTE, "2026-06-05")
        assert "distilled_date:\n" in out

    def test_missing_filed_date_line_raises(self):
        note = "---\ntype: source\n---\n"
        with pytest.raises(FrontmatterError):
            set_filed_date(note, "2026-06-05")

    def test_only_frontmatter_filed_date_is_touched(self):
        # A `filed_date:` in the body (should one ever exist) must survive — only
        # the frontmatter key is rewritten.
        note = NOTE + "\nbody note\nfiled_date: not-this-one\n"
        out = set_filed_date(note, "2026-06-05")
        assert "filed_date: 2026-06-05" in out
        assert "filed_date: not-this-one" in out  # body line untouched
        assert out.count("filed_date:") == 2  # one frontmatter key + one body line

    def test_filed_date_outside_fence_does_not_satisfy_the_write(self):
        note = "---\ntype: source\n---\nfiled_date: in-body\n"
        with pytest.raises(FrontmatterError):
            set_filed_date(note, "2026-06-05")

    def test_indented_filed_date_is_not_a_top_level_key(self):
        note = "---\ntype: source\nnested:\n  filed_date: x\n---\n"
        with pytest.raises(FrontmatterError):
            set_filed_date(note, "2026-06-05")
