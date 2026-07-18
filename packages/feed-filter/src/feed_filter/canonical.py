"""Canonical URL normalization — the sole dedupe key.

A canonical URL has a lowercased scheme + host, default ports dropped, no
fragment, tracking params stripped, query params sorted, percent-encodings
upper-cased, duplicate path slashes collapsed, and a normalized trailing slash.
The same article reached by two different links must canonicalize identically;
two genuinely distinct articles must not collide.

``canonical_url`` returns a ``CanonicalUrl`` — a ``NewType`` over ``str`` so the
seen-store (the dedupe authority) can require, and ``ty`` can enforce,
that every key it stores has actually been canonicalized.
"""

from __future__ import annotations

import re
from typing import NewType
from urllib.parse import SplitResult, parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

CanonicalUrl = NewType("CanonicalUrl", str)

# Query keys that carry no content identity (analytics / click attribution).
# Exact matches plus the ``utm_*`` / ``mc_*`` prefixes used by Google and
# Mailchimp. Matched case-insensitively. ``ref`` is deliberately NOT stripped:
# it is content-bearing on some sites (e.g. a git ref), and the project favors
# never-lost over never-duplicated, so collapsing a real ``ref`` would
# be the worse failure.
_TRACKING_EXACT = frozenset({"gclid", "fbclid"})
_TRACKING_PREFIXES = ("utm_", "mc_")

# Keys that are tracking *only* for a specific value shape — stripped when the
# value matches (case-sensitively, as the source emits it), kept otherwise. Maps
# a lowercased key to value prefixes. Host-agnostic, like the sets above: the
# match is on the key+value, not the site, so any host serving the shape is
# normalized the same way.
#
# ``source``: Medium appends an RSS-click attribution value to every feed link
# — ``rss----<publicationId>---<N>`` for a publication feed, ``rss-<userId>---
# ---<N>`` for an ``@user`` feed — but serves the same article without it across
# fetches; a retained value then forks the dedupe key and re-reminds the article
# each time the form flips. Both shapes share the ``rss-`` prefix, which is what
# we match. A bare ``source`` is, like ``ref``, potentially content-bearing
# elsewhere (a CMS may route on it), and unlike ``ref``'s git usage has no
# common content-bearing form — but the never-lost bias still says
# keep it rather than collapse two genuinely distinct pages. Matching the value
# case-sensitively (Medium emits lowercase ``rss-``) keeps the strip narrow: a
# value like ``RSS-digest`` is left alone rather than risk a never-lost collapse.
_TRACKING_VALUE_PREFIXES: dict[str, tuple[str, ...]] = {"source": ("rss-",)}

_DEFAULT_PORTS = {"http": 80, "https": 443}

_DUP_SLASH = re.compile(r"/{2,}")
_PCT = re.compile(r"%[0-9a-fA-F]{2}")


def _is_tracking(key: str, value: str) -> bool:
    k = key.lower()
    if k in _TRACKING_EXACT or k.startswith(_TRACKING_PREFIXES):
        return True
    prefixes = _TRACKING_VALUE_PREFIXES.get(k)
    return prefixes is not None and value.startswith(prefixes)


def _normalize_netloc(parts: SplitResult, scheme: str) -> str:
    # ``.hostname`` is already lowercased and strips IPv6 brackets; re-add them.
    # userinfo is case-sensitive (RFC 3986), so it is preserved verbatim.
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    prefix = ""
    if parts.username is not None:
        prefix = parts.username
        if parts.password is not None:
            prefix += f":{parts.password}"
        prefix += "@"
    netloc = prefix + host
    port = parts.port
    if port is not None and port != _DEFAULT_PORTS.get(scheme):
        netloc += f":{port}"
    return netloc


def _strip_tracking(query: str) -> str:
    # keep_blank_values so a valueless ``?foo`` survives (as ``foo=``) rather
    # than vanishing. Sorted so param order can't fork the dedupe key.
    kept = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if not _is_tracking(k, v)]
    kept.sort()
    return urlencode(kept)


def _normalize_path(path: str) -> str:
    path = _DUP_SLASH.sub("/", path)
    # Percent-encoding hex digits are case-insensitive (RFC 3986); upper-case
    # them so ``%2f`` and ``%2F`` dedupe (the query side already does via
    # urlencode). Unreserved-character decoding is intentionally not attempted.
    path = _PCT.sub(lambda m: m.group(0).upper(), path)
    if not path:
        return "/"
    # Drop a trailing slash on everything but the bare root, so ``/blog/`` and
    # ``/blog`` dedupe to one key.
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return path


def canonical_url(url: str, base: str | None = None) -> CanonicalUrl:
    """Return the canonical form of ``url``.

    When ``base`` is given, ``url`` may be relative and is resolved against it
    first. Idempotent: ``canonical_url(canonical_url(x)) == canonical_url(x)``.
    """
    if base is not None:
        url = urljoin(base, url)
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    return CanonicalUrl(
        urlunsplit(
            (
                scheme,
                _normalize_netloc(parts, scheme),
                _normalize_path(parts.path),
                _strip_tracking(parts.query),
                "",  # fragment always dropped
            )
        )
    )


def resolve_link(base_url: str, href: str | None, *, require_same_host: bool = False) -> str | None:
    """Resolve ``href`` against ``base_url``, rejecting non-article links.

    Co-located with ``canonical_url`` because both ingest paths turn raw link
    values into URLs before canonicalizing: ``feeds.parse_feed`` (cross-host
    syndication is legitimate, so no host guard) and ``scrape.scrape_index``
    (``index_url`` binds a single host, so ``require_same_host=True``). Keeping
    the resolution rules in one place stops the two callers from drifting apart.

    Returns ``None`` (caller skips the link) when ``href`` is missing, blank,
    fragment-only (``#anchor`` — these collapse to the base URL once the
    fragment is stripped), non-http(s) (``javascript:`` / ``mailto:`` would
    explode in ``httpx``), or — under ``require_same_host`` — points off-host.
    """
    if href is None:
        return None
    stripped = str(href).strip()
    if not stripped or stripped.startswith("#"):
        return None
    absolute = urljoin(base_url, stripped)
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https"):
        return None
    if require_same_host and (parts.hostname or "") != (urlsplit(base_url).hostname or ""):
        return None
    return absolute
