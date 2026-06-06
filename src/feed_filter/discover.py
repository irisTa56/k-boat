"""Zero-manual-input feed/scrape discovery (ported, reduced) — REQ-001.

Given an arbitrary site URL, ``discover`` tries four layers in order, each firing
only when the previous produced no *validated* feed candidate:

- (a) the URL is itself an RSS/Atom feed;
- (b) ``<link rel="alternate">`` feed declarations in the fetched HTML;
- (c) typical-path probing (``feed.xml``, ``rss.xml``, … at the host root and the
      URL's directory);
- (d) URL-pattern clustering of the index page's in-host ``<a href>`` links,
      emitting every surviving article-shaped cluster as a scrape candidate.

Ported from loose-feeds ``domain/discover.py`` and reduced to this project's
shape (GUD-005, CON-003):

- **Synchronous.** A sync ``httpx.Client`` threaded through ``fetch``; no
  ``asyncio``, no parallel candidate validation.
- **No SSRF guard.** ``sites.toml`` and the registration URL are trusted
  (SEC-001); the private-IP / scheme gating loose-feeds applies at every hop is
  dropped, along with the Playwright browser path and the age filter.
- **Feed type collapsed to ``"feed"``.** ``feedparser`` handles RSS and Atom
  alike and ``SiteConfig.kind`` is binary, so the rss/atom distinction
  loose-feeds tracked is intentionally discarded — every feed candidate is
  ``feed_type="feed"``, every scrape candidate ``"scrape"``.
- **Rejection is data, not an exception.** A soft failure (no article cluster /
  likely-JS page) is returned as ``DiscoveryResult.rejection`` rather than
  raised, so the sync CLI emits ``{candidates, rejection}`` directly (CON-006).
  Only a transport failure of the *initial* URL raises — ``fetch`` raises the
  typed ``FetchError``, which propagates for the CLI to map to a non-zero exit.
  Candidate-probe fetch failures are absorbed (a 404 typical-path is not a feed).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal, NamedTuple
from urllib.parse import urljoin, urlsplit

import httpx
from selectolax.parser import HTMLParser

from feed_filter.canonical import CanonicalUrl, canonical_url, resolve_link
from feed_filter.feeds import Entry, parse_feed
from feed_filter.fetch import FetchError, fetch

# rel=alternate ``type`` values (and self-feed content types) that mark a feed.
# The mapped rss/atom value is discarded — only membership matters now that the
# candidate type is collapsed to "feed" (see module docstring).
_FEED_LINK_TYPES = frozenset(
    {
        "application/rss+xml",
        "application/atom+xml",
        "application/xml",
        "text/xml",
    }
)

# REQ-001 typical RSS/Atom suffixes, joined against both the host root and the
# directory portion of the input URL. The cap bounds registration latency.
_TYPICAL_FEED_SUFFIXES: tuple[str, ...] = (
    "feed.xml",
    "rss.xml",
    "atom.xml",
    "index.xml",
    "feed",
    "rss",
)
_TYPICAL_PATH_CAP = 12

_SLUG_TOKEN = "<slug>"
_CLUSTER_MIN_SIZE = 5

RejectionReason = Literal["needs_js", "no_article_clusters"]


@dataclass(frozen=True)
class DiscoveryRejection:
    """Why discovery produced no candidate, when the cause is actionable.

    ``no_article_clusters``: the index page has dense link clusters but all of
    them look like navigation relative to the supplied URL — point at the
    article-listing page instead. ``needs_js``: no qualifying link cluster at all
    (or a non-HTML body), so the page likely renders its articles with
    JavaScript and is unsupported.
    """

    reason: RejectionReason
    message: str


@dataclass(frozen=True)
class DiscoveryCandidate:
    """One registerable source. Feed and scrape candidates never coexist in a
    single result (layer (d) fires only when no feed validated), but feed
    candidates are still ordered first per TASK-024.

    For a feed candidate ``index_url`` / ``article_url_pattern`` are empty; for a
    scrape candidate ``feed_url`` is empty and the pair drives ``scrape_index``.
    ``entry_count`` is the resolvable feed entries (feed) or clustered article
    links (scrape); ``sample_urls`` (≤5) lets the operator/model verify the pick.
    """

    feed_url: str
    feed_type: Literal["feed", "scrape"]
    index_url: str
    article_url_pattern: str
    sample_urls: tuple[str, ...]
    entry_count: int


@dataclass(frozen=True)
class DiscoveryResult:
    """Outcome of ``discover``. ``rejection`` is ``None`` on success (``candidates``
    non-empty) or carries the actionable reason when no candidate was found."""

    candidates: tuple[DiscoveryCandidate, ...]
    rejection: DiscoveryRejection | None


class _ValidatedFeed(NamedTuple):
    feed_url: str
    entry_count: int
    sample_urls: tuple[str, ...]


def _extract_alternate_links(html: str, base_url: str) -> list[str]:
    """Resolved hrefs of each ``<link rel="alternate">`` carrying a feed type."""
    try:
        tree = HTMLParser(html)
    except Exception:  # noqa: BLE001 - a malformed page is "no links", not a crash
        return []
    found: list[str] = []
    for link in tree.css('link[rel="alternate"]'):
        href = link.attributes.get("href")
        # Drop any content-type parameters (`; charset=utf-8`) before matching —
        # a parameterized but valid feed declaration must not be silently skipped.
        ltype = (link.attributes.get("type") or "").split(";", 1)[0].strip().lower()
        if not href or ltype not in _FEED_LINK_TYPES:
            continue
        found.append(urljoin(base_url, href))
    return found


def _typical_path_candidates(source_url: str) -> list[str]:
    """Typical RSS/Atom URLs to probe (REQ-001), order-preserving and capped.

    Each suffix is joined against the host root and the URL's directory (the
    whole path treated as a directory, so ``…/blog`` probes ``…/blog/rss.xml``
    rather than only ``…/rss.xml``).
    """
    parts = urlsplit(source_url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return []
    root = f"{parts.scheme}://{parts.netloc}/"
    path = parts.path or "/"
    directory = path if path.endswith("/") else path + "/"
    dir_base = f"{parts.scheme}://{parts.netloc}{directory}"

    seen: set[str] = set()
    out: list[str] = []
    for base in (root, dir_base):
        for suffix in _TYPICAL_FEED_SUFFIXES:
            url = urljoin(base, suffix)
            if url in seen:
                continue
            seen.add(url)
            out.append(url)
            if len(out) >= _TYPICAL_PATH_CAP:
                return out
    return out


def _validated_feed(feed_url: str, entries: list[Entry]) -> _ValidatedFeed:
    """Build a feed candidate preview from already-parsed entries. ``sample_urls``
    are canonical (PAT-002), matching the dedupe key the seen-store will use."""
    return _ValidatedFeed(
        feed_url=feed_url,
        entry_count=len(entries),
        sample_urls=tuple(str(e.canonical_url) for e in entries[:5]),
    )


def _probe_feed(url: str, *, client: httpx.Client) -> _ValidatedFeed | None:
    """Fetch a *candidate* URL and validate it as a feed, or return ``None``.

    A transport/HTTP failure is absorbed — a probe that 404s or times out simply
    is not a feed. This is the deliberate complement to ``discover`` letting the
    *initial*-URL ``FetchError`` propagate (CON-006): only the URL the operator
    typed surfaces a transport error; speculative probes never do. A body
    ``feedparser`` recovers no resolvable entry from is also rejected (an empty
    ``sample_urls`` would be a useless source).
    """
    try:
        result = fetch(url, client=client)
    except FetchError:
        return None
    entries = parse_feed(result.content, result.final_url)
    if not entries:
        return None
    return _validated_feed(result.final_url, entries)


def _cluster_link_patterns(html: str, base_url: str) -> list[tuple[str, list[str]]]:
    """Cluster in-host ``<a href>`` paths by replacing the final path segment with
    ``<slug>``. Returns clusters ``(key, urls)`` sorted by ``(size desc, key asc)``,
    dropping any below ``_CLUSTER_MIN_SIZE``. Query/fragment are stripped before
    clustering so tracking params don't fork a cluster; URLs keep DOM order and
    are deduplicated. Navigation-shape filtering is the caller's concern.
    """
    try:
        tree = HTMLParser(html)
    except Exception:  # noqa: BLE001
        return []
    clusters: dict[str, list[str]] = {}
    seen_urls: set[CanonicalUrl] = set()
    for link in tree.css("a"):
        absolute = resolve_link(base_url, link.attributes.get("href"), require_same_host=True)
        if absolute is None:
            continue
        parts = urlsplit(absolute)
        # Dedupe and count on the canonical URL — the exact key ``scrape_index``
        # ingests against (PAT-002) — so ``entry_count`` / ``sample_urls`` preview
        # what registration will actually scrape, not a raw-path over-count.
        # Query/fragment are dropped first, mirroring ``scrape_index``, so
        # trailing-slash and tracking-param variants of one article collapse into
        # the cluster instead of inflating it past ``_CLUSTER_MIN_SIZE``.
        canon = canonical_url(f"{parts.scheme}://{parts.netloc}{parts.path}")
        if canon in seen_urls:
            continue
        seen_urls.add(canon)
        segments = [s for s in urlsplit(canon).path.split("/") if s]
        if not segments:
            continue
        key = "/" + "/".join(segments[:-1] + [_SLUG_TOKEN])
        clusters.setdefault(key, []).append(str(canon))
    qualified = [(k, urls) for k, urls in clusters.items() if len(urls) >= _CLUSTER_MIN_SIZE]
    qualified.sort(key=lambda kv: (-len(kv[1]), kv[0]))
    return qualified


def _drop_navigation_clusters(
    clusters: list[tuple[str, list[str]]], index_url: str
) -> list[tuple[str, list[str]]]:
    """Drop clusters shaped like navigation relative to ``index_url``.

    Two structural rules, keyed on the supplied index URL's path: a cluster
    shallower than the index (top-level menu pulled in from layout) and a cluster
    at the same depth with the same prefix-except-last (same-level nav, usually
    including the index page itself). Strict descendants and deeper/cross-path
    clusters pass — the operator gates the survivors via ``sample_urls``. A root
    index URL has no narrower meaning, so every cluster passes.
    """
    index_segments = [s for s in (urlsplit(index_url).path or "").split("/") if s]
    if not index_segments:
        return clusters
    index_depth = len(index_segments)
    index_sibling_prefix = index_segments[:-1]
    kept: list[tuple[str, list[str]]] = []
    for key, urls in clusters:
        key_segments = [s for s in key.split("/") if s]
        if len(key_segments) < index_depth:
            continue
        if len(key_segments) == index_depth and key_segments[:-1] == index_sibling_prefix:
            continue
        kept.append((key, urls))
    return kept


def _regex_for_cluster_key(key: str) -> str:
    """Synthesize an anchored regex for a cluster key: each literal segment is
    escaped, each ``<slug>`` token becomes ``[^/]+``, trailing slash optional."""
    parts: list[str] = []
    for segment in key.split("/"):
        if not segment:
            continue
        parts.append("[^/]+" if segment == _SLUG_TOKEN else re.escape(segment))
    return "^/" + "/".join(parts) + "/?$"


def _scrape_candidates(
    html: str, final_url: str, source_url: str
) -> tuple[tuple[DiscoveryCandidate, ...], DiscoveryRejection | None]:
    """Run layer (d) clustering and return (candidates, rejection).

    Article clusters → candidates with descendants of the supplied URL first
    (most likely the section the operator named), then the rest preserving the
    size-desc order. Qualifying-but-all-navigation → ``no_article_clusters``. No
    qualifying cluster at all → ``needs_js``.
    """
    clusters = _cluster_link_patterns(html, final_url)
    article_clusters = _drop_navigation_clusters(clusters, source_url) if clusters else []
    if not article_clusters:
        if clusters:
            top_key, top_urls = clusters[0]
            return (), DiscoveryRejection(
                reason="no_article_clusters",
                message=(
                    f"no article-shaped cluster for {source_url} (largest navigation cluster: "
                    f"{top_key}, {len(top_urls)} links). Try the actual article-listing page."
                ),
            )
        return (), DiscoveryRejection(
            reason="needs_js",
            message=f"no qualifying article cluster on {final_url}; site likely requires JS",
        )

    user_segments = [s for s in (urlsplit(source_url).path or "").split("/") if s]
    descendants: list[tuple[str, list[str]]] = []
    others: list[tuple[str, list[str]]] = []
    for key, urls in article_clusters:
        key_segments = [s for s in key.split("/") if s]
        is_descendant = (
            len(key_segments) > len(user_segments)
            and key_segments[: len(user_segments)] == user_segments
        )
        (descendants if is_descendant else others).append((key, urls))

    candidates = tuple(
        DiscoveryCandidate(
            feed_url="",
            feed_type="scrape",
            index_url=final_url,
            article_url_pattern=_regex_for_cluster_key(key),
            sample_urls=tuple(urls[:5]),
            entry_count=len(urls),
        )
        for key, urls in descendants + others
    )
    return candidates, None


def discover(url: str, *, client: httpx.Client) -> DiscoveryResult:
    """Discover feed/scrape candidates for ``url`` (REQ-001).

    Raises ``FetchError`` if the initial URL is unreachable (CON-006); soft
    failures come back as ``DiscoveryResult.rejection``. See the module docstring
    for the layer ordering and the loose-feeds reductions applied.
    """
    fetched = fetch(url, client=client)
    final_url = fetched.final_url
    text = fetched.text
    is_html = "html" in fetched.content_type.lower()

    feeds: list[_ValidatedFeed] = []
    seen_feed_urls: set[str] = set()  # post-redirect feed URLs, to collapse dupes
    attempted: set[str] = {final_url}  # seed URLs already fetched, never re-probe

    def add(candidate: _ValidatedFeed | None) -> None:
        if candidate is not None and candidate.feed_url not in seen_feed_urls:
            seen_feed_urls.add(candidate.feed_url)
            feeds.append(candidate)

    # Layer (a): the initial URL is itself a feed. Validated from the body already
    # in hand — no redundant re-fetch of the URL we just fetched.
    self_entries = parse_feed(fetched.content, final_url)
    if self_entries:
        add(_validated_feed(final_url, self_entries))

    # Layer (b): declared alternate feed links — evaluated alongside (a) so a
    # self-feed never hides a co-declared canonical feed.
    for href in _extract_alternate_links(text, final_url):
        if href in attempted:
            continue
        attempted.add(href)
        add(_probe_feed(href, client=client))

    # Layer (c): typical-path probing — only when (a)/(b) yielded no feed.
    if not feeds:
        for probe in _typical_path_candidates(final_url):
            if probe in attempted:
                continue
            attempted.add(probe)
            add(_probe_feed(probe, client=client))

    if feeds:
        feed_candidates = tuple(
            DiscoveryCandidate(
                feed_url=v.feed_url,
                feed_type="feed",
                index_url="",
                article_url_pattern="",
                sample_urls=v.sample_urls,
                entry_count=v.entry_count,
            )
            for v in feeds
        )
        return DiscoveryResult(candidates=feed_candidates, rejection=None)

    # Layer (d): index-page clustering — only on an HTML body. A non-HTML,
    # non-feed body falls through to ``needs_js`` (the cluster algorithm is
    # meaningful only on HTML).
    if text and is_html:
        candidates, rejection = _scrape_candidates(text, final_url, url)
        return DiscoveryResult(candidates=candidates, rejection=rejection)

    return DiscoveryResult(
        candidates=(),
        rejection=DiscoveryRejection(
            reason="needs_js",
            message=f"no feed found and {final_url} is not an HTML page to scrape",
        ),
    )
