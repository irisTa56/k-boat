"""Discourse forum adapter — URL builders, JSON parsing, topic-id extraction.

Reuses ``fetch.build_client`` / ``fetch.fetch`` / ``FetchError`` for all HTTP
calls, ``feeds.parse_feed`` with ``sort=False`` for rank-ordered discovery, and
``canonical.canonical_url`` for topic-id extraction.  No state, no pipeline —
those live in ``forum_store`` and ``forum_pipeline``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from feed_filter.feeds import html_to_text, parse_feed
from kboat.canonical import canonical_url

# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------


def _base(forum_url: str) -> str:
    return forum_url.rstrip("/")


def latest_feed_url(forum_url: str) -> str:
    """latest.rss feed — Rule A discovery seed."""
    return _base(forum_url) + "/latest.rss"


def top_feed_url(forum_url: str, period: str) -> str:
    """top.rss?period=<period> feed — Rule B discovery seed."""
    return _base(forum_url) + f"/top.rss?period={period}"


def topic_json_url(forum_url: str, topic_id: int) -> str:
    """Public topic JSON endpoint for post-grain polling."""
    return _base(forum_url) + f"/t/{topic_id}.json"


# ---------------------------------------------------------------------------
# Topic-id extraction
# ---------------------------------------------------------------------------

# Matches the path component of a Discourse topic URL:
#   /t/<id>
#   /t/<slug>/<id>
# with an optional trailing /<post_number> in either form.
#
# The negative lookahead (?!\d+(?:/|$)) prevents a purely-numeric first
# segment from being consumed as a "slug", so /t/1234/5 yields 1234 (the
# topic id), not 5 (the post number).
_TOPIC_PATH_RE = re.compile(r"^/t/(?:(?!\d+(?:/|$))[^/]+/)?(\d+)(?:/\d+)?$")


def topic_id_from_url(url: str) -> int | None:
    """Return the numeric Discourse topic id from a URL, or ``None``.

    Accepts ``/t/<id>``, ``/t/<slug>/<id>``, and either form with a trailing
    post number.  Calls ``canonical_url`` for normalization (strips fragment,
    tracking params, duplicate slashes) before matching.
    """
    path = urlsplit(canonical_url(url)).path
    m = _TOPIC_PATH_RE.match(path)
    return int(m.group(1)) if m is not None else None


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForumTopic:
    """Topic-level metadata from a Discourse topic JSON response.

    ``like_count`` is the topic-level total used for the short-circuit (skip the
    JSON fetch when it is unchanged since the last poll).
    """

    id: int
    slug: str
    title: str
    like_count: int


@dataclass(frozen=True)
class ForumPost:
    """A single post from the first JSON chunk.

    ``text`` is the ``cooked`` HTML flattened to plain text via the shared
    ``feeds.html_to_text`` so the haiku judge receives prose rather than markup
    — the same flattening the feed path applies to RSS summaries, so
    Rule-A (RSS) and Rule-B (topic JSON) text are produced by one implementation.
    """

    id: int
    number: int
    like_count: int
    text: str


_EMPTY_TOPIC = ForumTopic(id=0, slug="", title="", like_count=0)


# ---------------------------------------------------------------------------
# Topic JSON parsing
# ---------------------------------------------------------------------------

# Typed coercers for untrusted JSON values. The ``/t/<id>.json``
# shape is undocumented-but-stable; rather than truthy-check each
# field (``x or 0``, which leaves a wrong-typed value to crash later — a string
# ``count`` makes ``like_count`` a ``str`` that explodes in a downstream
# ``>=`` comparison, an int ``actions_summary`` makes ``for`` raise
# ``TypeError``), every value pulled from the payload passes through one of
# these so a wrong type at *any* level degrades to the field's empty default
# instead of leaking a ``TypeError``/``AttributeError`` into ``main()`` (whose
# exception map does not catch ``TypeError``).


def _as_int(value: object) -> int:
    return value if isinstance(value, int) else 0


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


def _as_list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: object) -> dict[Any, Any]:
    return value if isinstance(value, dict) else {}


def parse_topic(json_bytes: bytes) -> tuple[ForumTopic, list[ForumPost]]:
    """Parse a Discourse ``/t/<id>.json`` body into a topic and its posts.

    Reads only ``post_stream.posts`` from the first chunk. Every
    value is funneled through a typed coercer (``_as_int``/``_as_str``/
    ``_as_list``/``_as_dict``), so a malformed or lite payload — a wrong type at
    *any* level, not just an absent key — degrades to ``(_EMPTY_TOPIC, [])`` (or
    a post with empty fields) rather than leaking a ``KeyError``/
    ``AttributeError``/``TypeError`` into ``main()``.
    """
    try:
        data = json.loads(json_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return _EMPTY_TOPIC, []

    if not isinstance(data, dict):
        return _EMPTY_TOPIC, []

    topic = ForumTopic(
        id=_as_int(data.get("id")),
        slug=_as_str(data.get("slug")),
        title=_as_str(data.get("title")),
        like_count=_as_int(data.get("like_count")),
    )

    raw_posts = _as_list(_as_dict(data.get("post_stream")).get("posts"))

    posts: list[ForumPost] = []
    for raw in raw_posts:
        if not isinstance(raw, dict):
            continue
        likes = 0
        for action in _as_list(raw.get("actions_summary")):
            if isinstance(action, dict) and action.get("id") == 2:
                likes = _as_int(action.get("count"))
                break
        cooked = _as_str(raw.get("cooked"))
        posts.append(
            ForumPost(
                id=_as_int(raw.get("id")),
                number=_as_int(raw.get("post_number")),
                like_count=likes,
                # html_to_text returns None for empty/blank (or all-markup) input;
                # the post-grain store wants "" for "no text", so coalesce.
                text=html_to_text(cooked) or "",
            )
        )

    return topic, posts


# ---------------------------------------------------------------------------
# Feed-based topic-id discovery
# ---------------------------------------------------------------------------


def discover_topic_ids(feed_bytes: bytes, base_url: str) -> list[int]:
    """Return topic ids from an RSS feed, preserving feed source order.

    Passes ``sort=False`` to ``parse_feed`` so a ``top.rss`` feed keeps its
    rank order rather than being re-sorted by date.
    Non-topic links (``topic_id_from_url`` returns ``None``) are dropped.
    """
    entries = parse_feed(feed_bytes, base_url, sort=False)
    ids: list[int] = []
    for entry in entries:
        tid = topic_id_from_url(entry.canonical_url)
        if tid is not None:
            ids.append(tid)
    return ids
