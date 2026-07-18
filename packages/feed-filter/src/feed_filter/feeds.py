"""Feed parsing — ``feedparser`` over raw bytes.

``Entry`` is the shared ingest shape across the feed and scrape paths. ``title``,
``summary``, and ``published_at`` are populated for feed entries (any may still
be ``None`` for a sparse item) but are documented **feed-only**: the scrape path
(``scrape.scrape_index``) leaves them ``None`` and the subagent's page fetch
supplies a title for any keep. ``canonical_url`` is the dedupe key,
so it is typed ``CanonicalUrl`` and always set.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Literal

import feedparser

from feed_filter.canonical import CanonicalUrl, canonical_url, resolve_link

EntryKind = Literal["feed", "scrape"]

# Block-level tags whose boundaries become whitespace so prose does not run
# together once tags are dropped; inline tags (a, em, strong, code, span) add
# nothing, so a byline like ``<a>Name</a>, <a>Name</a>`` stays intact.
_BLOCK_TAGS = frozenset(
    {
        "p",
        "div",
        "br",
        "hr",
        "li",
        "ul",
        "ol",
        "tr",
        "td",
        "th",
        "table",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "blockquote",
        "pre",
        "section",
        "article",
        "header",
        "footer",
        "figure",
        "figcaption",
        "aside",
        "dl",
        "dt",
        "dd",
    }
)


class _TextExtractor(HTMLParser):
    """Collect visible text from an HTML fragment, dropping script/style.

    ``convert_charrefs=True`` unescapes entities inside text, so callers get
    ``&amp;`` as ``&``. Block boundaries emit a space; the caller collapses runs.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: object) -> None:
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self._parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self._parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def html_to_text(raw: str | None) -> str | None:
    """Flatten an HTML feed body to whitespace-collapsed plain text.

    Feeds carry the body as HTML; stripping tags here lets the title+summary
    judge stage read the prose directly. The input is a feed ``description`` or
    ``content:encoded`` (see ``_entry_summary``), HTML by contract; plain text
    with no markup or character references passes through unchanged, and
    empty/blank input (or a body that is all tags) returns ``None`` so downstream
    sees "no summary".
    """
    if not raw:
        return None
    extractor = _TextExtractor()
    extractor.feed(raw)
    text = " ".join(extractor.text().split())
    return text or None


@dataclass(frozen=True)
class Entry:
    """One ingestable item. ``kind`` records which path produced it."""

    canonical_url: CanonicalUrl
    title: str | None
    summary: str | None
    published_at: int | None
    kind: EntryKind


def _published_at(entry: object) -> int | None:
    """UTC unix timestamp from a feedparser entry, or ``None``.

    feedparser exposes parsed dates as UTC ``time.struct_time``. ``time.mktime``
    would read them as local time and shift by the host offset, so use
    ``calendar.timegm`` to keep the UTC contract.
    """
    for key in ("published_parsed", "updated_parsed"):
        value = getattr(entry, key, None)
        if value:
            try:
                return int(calendar.timegm(value))
            except (TypeError, ValueError, OverflowError):
                continue
    return None


# Medium serves the same article under shifting link hosts — ``medium.com/<pub>/``
# vs a publication's custom domain (e.g. ``netflixtechblog.com``) — so the link
# forks the dedupe key and the article re-reminds every time the host
# flips. (The other historical fork, a volatile ``?source=`` param, is already
# neutralized in ``canonical.py``; the host shift is the residual cause.) Every
# Medium item also carries ``<guid isPermaLink="false">https://medium.com/p/
# <hash></guid>``, invariant across host, slug, and query, which 302-redirects
# to the live article — so it is the durable dedupe identity (and a usable
# reminder link). Non-Medium feeds are unaffected: their guids don't match, so
# they keep deduping on the resolved link.
#
# The hash class is ``[0-9a-z]`` (wider than the hex Medium emits today) on
# purpose: with the host + ``/p/`` + ``fullmatch`` anchoring already rejecting
# every non-Medium guid, erring wide only risks keying a hypothetical non-hex
# ``/p/`` id on its own stable guid (still collision-free), whereas erring
# narrow could miss a real guid and silently re-remind (never-lost).
_MEDIUM_GUID = re.compile(r"https?://medium\.com/p/[0-9a-z]+/?", re.IGNORECASE)


def _dedupe_url(entry: object, absolute: str) -> CanonicalUrl:
    """Canonical dedupe key: Medium's stable ``/p/<hash>`` guid when present,
    else the resolved entry link."""
    guid = getattr(entry, "id", None)
    if isinstance(guid, str):
        g = guid.strip()
        if _MEDIUM_GUID.fullmatch(g):
            return canonical_url(g)
    return canonical_url(absolute)


# Safety ceiling on the flattened feed body handed to the judge. ``content:encoded``
# is the full article (often 6-20k chars); shipping it whole is the point (the
# judge assesses depth without a per-article fetch that a gated site turns into a
# wall). This cap only guards against a pathological feed shipping a megabyte-scale
# body — a normal article is never truncated, so the wall-avoidance holds. Applied
# to the plain text after tag stripping, not the raw HTML.
MAX_BODY_CHARS = 50_000


def _entry_body(entry: object) -> str | None:
    """The richest HTML body a feed entry carries: ``content:encoded`` if present,
    else ``<description>``.

    feedparser exposes ``<content:encoded>`` as ``entry.content`` — a list of
    content objects each with a ``value`` — and ``<description>`` as
    ``entry.summary``. Full-text feeds (WordPress, Medium, Substack) ship the whole
    article in ``content:encoded`` while ``<description>`` is a truncated excerpt
    (e.g. thenewstack.io: a ~700-char intro vs the 6-20k-char body); preferring
    ``content`` lets the title+summary judge stage decide depth from the feed
    itself instead of falling through to a per-article fetch that a Cloudflare-gated
    site returns as a wall. Excerpt-only feeds have no ``content``, so they fall
    back to ``<description>`` unchanged.
    """
    contents = getattr(entry, "content", None)
    if contents:
        # feedparser yields ``content`` items as ``FeedParserDict`` (dict-like), so
        # ``.get`` is always available; take the first with a non-empty ``value``.
        for c in contents:
            value = c.get("value")
            if value:
                return value
    return getattr(entry, "summary", None)


def _entry_summary(entry: object) -> str | None:
    """Flattened, capped plain-text body for judging (``_entry_body`` +
    ``html_to_text``, clamped to ``MAX_BODY_CHARS``)."""
    text = html_to_text(_entry_body(entry))
    if text is not None and len(text) > MAX_BODY_CHARS:
        text = text[:MAX_BODY_CHARS]
    return text


def parse_feed(body: bytes, base_url: str, *, sort: bool = True) -> list[Entry]:
    """Parse an RSS/Atom body into ``Entry`` objects.

    ``body`` is raw bytes, never ``str``: ``feedparser.parse`` treats a
    ``str`` that looks like a URL or path as a fetch/file-open instruction, so a
    ``str`` overload here would turn this pure parser into an SSRF/file-read
    footgun. Callers pass ``FetchResult.content``.

    Entries whose link is unresolvable (missing / fragment-only / non-http) are
    skipped (``resolve_link``). A malformed body that ``feedparser`` cannot
    recover any entry from yields ``[]`` rather than raising — a dead feed is an
    empty observation, not a crash (handled downstream).

    Ordering (``sort=True``, default): dated entries by ``published_at``
    descending, undated entries in source order at the tail. The per-site cap
    downstream then keeps the newest.

    ``sort=False``: entries are returned in feed source order with no date sort.
    Used by ``discourse.discover_topic_ids`` so a top-feed preserves rank order
    rather than being re-sorted by date.
    """
    parsed = feedparser.parse(body)
    raw_entries = getattr(parsed, "entries", []) or []
    # ``bozo`` with zero recovered entries = unparseable body. ``bozo`` *with*
    # entries (feedparser repaired partial damage) falls through normally.
    if getattr(parsed, "bozo", False) and not raw_entries:
        return []

    out: list[Entry] = []
    for entry in raw_entries:
        absolute = resolve_link(base_url, getattr(entry, "link", None))
        if absolute is None:
            continue
        out.append(
            Entry(
                canonical_url=_dedupe_url(entry, absolute),
                title=getattr(entry, "title", None) or None,
                summary=_entry_summary(entry),
                published_at=_published_at(entry),
                kind="feed",
            )
        )

    if not sort:
        return out

    # Two-pass split (rather than an ``or 0`` sort key) so an entry dated at the
    # epoch is not sorted alongside the genuinely undated tail.
    dated = [e for e in out if e.published_at is not None]
    undated = [e for e in out if e.published_at is None]
    dated.sort(key=lambda e: e.published_at if e.published_at is not None else 0, reverse=True)
    return dated + undated
