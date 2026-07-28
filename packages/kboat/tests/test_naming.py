"""The shared URL-hash slug recipe."""

from __future__ import annotations

import hashlib

import pytest

from kboat.canonical import canonical_url
from kboat.naming import note_slug, url_slug
from kboat.repos.identity import canonical_slug


def test_url_slug_is_first_12_hex_of_sha256() -> None:
    url = "https://example.com/post"
    expected = hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
    assert url_slug(url) == expected
    assert len(url_slug(url)) == 12
    assert all(c in "0123456789abcdef" for c in url_slug(url))


def test_url_slug_is_verbatim() -> None:
    # No normalization and no trailing newline: a trailing slash is a distinct
    # string and must hash differently (canonicalization is the caller's job).
    assert url_slug("https://x.test/a") != url_slug("https://x.test/a/")
    assert url_slug("https://x.test/a") != url_slug("https://x.test/a\n")


def test_note_slug_is_the_hash_of_the_canonical_form() -> None:
    url = "HTTPS://Example.com/post/?utm_source=x#top"
    assert note_slug(url) == url_slug(str(canonical_url(url)))


@pytest.mark.parametrize(
    ("variant", "why"),
    [
        ("https://x.test/a/", "a trailing slash"),
        ("https://x.test/a?utm_source=news", "a tracking parameter"),
        ("https://X.TEST/a", "a host in another case"),
        ("https://x.test/a#section", "a fragment"),
        ("https://x.test//a", "a doubled path slash"),
    ],
)
def test_one_page_reached_two_ways_gets_one_slug(variant: str, why: str) -> None:
    # The whole point of hashing the canonical form: a page linked two ways must
    # not occupy two notes.
    assert note_slug(variant) == note_slug("https://x.test/a"), why


def test_the_oracle_does_not_collapse_a_file_link_onto_its_repository() -> None:
    """Why routing is not the oracle's job.

    `kboat.repos.identity.canonical_slug` answers "which repo is this URL about",
    so it maps every deep link onto the repository — which is right for the repo
    catalogue and wrong for a slug: a `.md` or `.pdf` file inside a repo is
    ingested as its own source, and it would land on the repository's note.
    The generic oracle hashes the URL it is given, so the three stay apart.
    """
    repo = "https://github.com/astral-sh/ruff"
    readme = "https://github.com/astral-sh/ruff/blob/main/README.md"
    paper = "https://github.com/astral-sh/ruff/blob/main/docs/paper.pdf"

    assert canonical_slug(repo) == canonical_slug(readme) == canonical_slug(paper)
    assert len({note_slug(repo), note_slug(readme), note_slug(paper)}) == 3


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/astral-sh/ruff",
        "https://github.com/Netflix/dispatch",
        "https://github.com/BurntSushi/ripgrep.rs",
        "https://github.com/o/r-2",
    ],
    ids=["all lower case", "a mixed-case owner", "a dot in the name", "a digit and a dash"],
)
def test_the_repo_path_and_the_oracle_name_a_repository_alike(url: str) -> None:
    """Both sides now ask the one oracle, so what this pins is not the hash step
    but the step before it: that `parse_repo` → `canonical_url(owner, repo)`
    builds a URL `canonical_url` leaves alone. Were it ever to normalize one of
    them, the note would be written under a name the writer recomputes
    differently, and every repo write would be refused as a `slug_mismatch`.

    Cased and punctuated forms as well as the plain one: the canonicalizer
    lowercases the scheme and host only, and a rule that ever reached into the
    path would diverge here first — on the repositories, not on the URL a single
    example happens to use.
    """
    assert canonical_slug(url) == note_slug(url)
