"""Tests for repo identity: parsing, canonicalization, and slug stability."""

from __future__ import annotations

import pytest

from kboat_repos.identity import canonical_slug, canonical_url, parse_repo


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
