"""Vault note naming — the shared canonical-URL slug recipe.

The `kboat-vault-conventions` skill specifies it: a URL-named note is the first
12 hex characters of the SHA-256 of its identity URL's *canonical* form. This is
the one code implementation of that recipe — `note_slug` is the oracle every
writer and every skill asks (through `kboat-note slug`), so source, repo, and
feed notes cannot come to name the same page differently.

`url_slug` is the hash step alone, kept separate because the two have different
questions: `note_slug` answers "where does this page's note live", `url_slug`
"what is the hash of this exact string".
"""

from __future__ import annotations

import hashlib

from kboat.canonical import canonical_url


def url_slug(url: str) -> str:
    """First 12 hex characters of the SHA-256 of `url` (UTF-8, verbatim).

    The caller is responsible for canonicalizing `url` first if it wants URL
    variants of one page to collapse to a single slug; this function hashes
    exactly what it is given. `note_slug` is that composition, and is what a
    vault note is named by.
    """
    return hashlib.sha256(url.encode("utf-8")).hexdigest()[:12]


def note_slug(url: str) -> str:
    """The vault slug for `url`: the hash of its canonical form.

    One recipe over the note's own `url` field, whatever the note type. Which URL
    a type *stores* is still that type's own decision — a repo note holds the
    constructed `https://github.com/<owner>/<repo>`, which the routing in
    `kboat.repos.identity` derives — but once stored, the slug follows from it
    with no per-type step. That is what makes the slug machine-verifiable: the
    write contract recomputes it from the record's `url` and refuses a mismatch.

    Raises `ValueError` for a string no URL parser can take at all — a malformed
    IPv6 literal, a port past 65535 — since there is no canonical form to hash.
    A caller reaching here with untrusted text (a queue capture, a note a human
    edited) has to answer for that: `canonical.resolve_link` makes the same
    refusal for a link it cannot resolve.
    """
    return url_slug(str(canonical_url(url)))
