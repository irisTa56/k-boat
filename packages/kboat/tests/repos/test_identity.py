"""Tests for repo identity: parsing, canonicalization, and slug stability."""

from __future__ import annotations

import pytest

from kboat.repos.identity import (
    canonical_slug,
    canonical_url,
    github_file_source,
    parse_repo,
)


@pytest.mark.parametrize(
    "url, owner, repo",
    [
        ("https://github.com/google/A2A", "google", "A2A"),
        ("https://github.com/google/A2A/", "google", "A2A"),
        ("https://github.com/google/A2A.git", "google", "A2A"),
        ("http://github.com/google/A2A", "google", "A2A"),
        ("https://github.com/google/A2A/tree/main/spec", "google", "A2A"),
        ("https://github.com/google/A2A/blob/main/README.md", "google", "A2A"),
        ("https://github.com/google/A2A/issues/12", "google", "A2A"),
        ("https://github.com/google/A2A?tab=readme", "google", "A2A"),
        ("https://github.com/google/A2A#install", "google", "A2A"),
        ("https://www.github.com/google/A2A", "google", "A2A"),
    ],
)
def test_parse_variants_collapse(url: str, owner: str, repo: str) -> None:
    assert parse_repo(url) == (owner, repo)


def test_dotgit_suffix_only_strips_dotgit() -> None:
    # `.git` is stripped as a whole suffix, never char-by-char.
    assert parse_repo("https://github.com/google/cadvisor") == ("google", "cadvisor")
    assert parse_repo("https://github.com/google/cadvisor.git") == ("google", "cadvisor")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/google/A2A",
        "https://github.com/google",  # bare profile, no repo
        "https://github.com/orgs/google",  # reserved route
        "https://github.com/features/copilot",  # reserved top-level route
        "https://github.com/about/x",
        "https://github.com/pricing/x",
        "https://github.com/explore/y",
        "not a url",
        "",
    ],
)
def test_non_repo_urls_are_rejected(url: str) -> None:
    assert parse_repo(url) == (None, None)
    assert canonical_slug(url) is None


def test_canonical_url() -> None:
    assert canonical_url("google", "A2A") == "https://github.com/google/A2A"


def test_slug_is_stable_across_variants() -> None:
    base = canonical_slug("https://github.com/google/A2A")
    assert base is not None
    assert len(base) == 12
    for variant in (
        "https://github.com/google/A2A/",
        "https://github.com/google/A2A.git",
        "https://github.com/google/A2A/tree/main",
        "http://github.com/google/A2A#x",
    ):
        assert canonical_slug(variant) == base


def test_distinct_repos_get_distinct_slugs() -> None:
    assert canonical_slug("https://github.com/google/A2A") != canonical_slug(
        "https://github.com/google/B2B"
    )


@pytest.mark.parametrize(
    "url, source_type, source_url",
    [
        # `.pdf` blob -> PDF source, rewritten to the raw download URL.
        (
            "https://github.com/junhuihuang/ebooks/blob/master/Ray%20v2%20Architecture.pdf",
            "pdf",
            "https://raw.githubusercontent.com/junhuihuang/ebooks/master/Ray%20v2%20Architecture.pdf",
        ),
        # The `/raw/` blob form rewrites the same way.
        (
            "https://github.com/o/r/raw/main/doc.pdf",
            "pdf",
            "https://raw.githubusercontent.com/o/r/main/doc.pdf",
        ),
        # A nested path is preserved in the raw URL.
        (
            "https://github.com/o/r/blob/main/dir/sub/paper.pdf",
            "pdf",
            "https://raw.githubusercontent.com/o/r/main/dir/sub/paper.pdf",
        ),
        # A query/fragment is stripped from the rewritten raw URL.
        (
            "https://github.com/o/r/blob/main/x.pdf?raw=true#page=2",
            "pdf",
            "https://raw.githubusercontent.com/o/r/main/x.pdf",
        ),
        # `.md` blob -> web_page, keeping the rendered blob page as the URL.
        (
            "https://github.com/o/r/blob/main/README.md",
            "web_page",
            "https://github.com/o/r/blob/main/README.md",
        ),
        # Extension match is case-insensitive.
        (
            "https://github.com/o/r/blob/main/Doc.PDF",
            "pdf",
            "https://raw.githubusercontent.com/o/r/main/Doc.PDF",
        ),
        # A fully-qualified `refs/heads/<branch>` permalink collapses to the bare
        # branch — same canonical URL as the plain `/main/` form below.
        (
            "https://github.com/o/r/blob/refs/heads/main/x.pdf",
            "pdf",
            "https://raw.githubusercontent.com/o/r/main/x.pdf",
        ),
        (
            "https://github.com/o/r/blob/main/x.pdf",
            "pdf",
            "https://raw.githubusercontent.com/o/r/main/x.pdf",
        ),
        # `refs/tags/<tag>` collapses the same way, for a `.md`.
        (
            "https://github.com/o/r/blob/refs/tags/v1.0/GUIDE.md",
            "web_page",
            "https://github.com/o/r/blob/v1.0/GUIDE.md",
        ),
        # A `.md` linked via `/raw/` (which serves text/plain) is normalized to
        # its rendered blob page.
        (
            "https://github.com/o/r/raw/main/README.md",
            "web_page",
            "https://github.com/o/r/blob/main/README.md",
        ),
    ],
)
def test_github_file_source_classifies_readable_files(
    url: str, source_type: str, source_url: str
) -> None:
    assert github_file_source(url) == (source_type, source_url)


def test_github_file_source_collapses_ref_forms_to_one_url() -> None:
    # The `refs/heads/<branch>` permalink and the plain `/<branch>/` link must
    # produce the same source URL (and thus the same de-dup slug), as the repo
    # path collapses its own URL variants.
    permalink = github_file_source("https://github.com/o/r/blob/refs/heads/main/dir/x.pdf")
    plain = github_file_source("https://github.com/o/r/blob/main/dir/x.pdf")
    assert permalink == plain
    assert permalink is not None


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/google/A2A",  # repo root
        "https://github.com/google/A2A/tree/main/spec",  # directory deep link
        "https://github.com/google/A2A/issues/12",  # non-file deep link
        "https://github.com/o/r/blob/main/script.py",  # another extension stays a repo
        "https://github.com/o/r/blob/main/data.txt",
        "https://github.com/o/r/blob/main/notes.markdown",  # only `.md`, not `.markdown`
        "https://github.com/o/r/blob/main/x.pdf.txt",  # last segment decides — not a PDF
        "https://github.com/o/r/blob/main/x.md.gz",  # nor a markdown
        "https://github.com/o/r/blob/main/sub/",  # directory, no file segment
        "https://github.com/o/r/blob/main",  # ref only, no file
        "https://github.com/o/r/blob/refs/heads/main",  # qualified ref, still no file
        "https://example.com/o/r/blob/main/x.pdf",  # not github.com
        "https://github.com/orgs/foo/blob/main/x.md",  # reserved owner
        "",
    ],
)
def test_github_file_source_returns_none_for_repos_and_other_files(url: str) -> None:
    assert github_file_source(url) is None
