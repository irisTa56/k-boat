"""Vault note naming — the shared URL-hash slug recipe.

The `kboat-vault-conventions` skill specifies it: a content note is named by the
first 12 hex characters of the SHA-256 of its identity URL, hashed verbatim (no
trailing newline). This is the one code implementation of that recipe, shared by
every vault writer — the repo catalogue (`kboat.repos.identity`) and the
feed-filter member's vault output — so the slug can never drift between them.
"""

from __future__ import annotations

import hashlib


def url_slug(url: str) -> str:
    """First 12 hex characters of the SHA-256 of `url` (UTF-8, verbatim).

    The caller is responsible for canonicalizing `url` first if it wants URL
    variants of one page to collapse to a single slug; this function hashes
    exactly what it is given.
    """
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]
