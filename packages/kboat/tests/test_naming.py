"""The shared URL-hash slug recipe."""

from __future__ import annotations

import hashlib

from kboat.naming import url_slug


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
