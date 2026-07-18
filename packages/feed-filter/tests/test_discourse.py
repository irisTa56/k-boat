"""Behavior tests for the Discourse adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from feed_filter.discourse import (
    _EMPTY_TOPIC,
    discover_topic_ids,
    latest_feed_url,
    parse_topic,
    top_feed_url,
    topic_id_from_url,
    topic_json_url,
)

FIXTURES = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# URL builders
# ---------------------------------------------------------------------------


def test_url_builders_strip_trailing_slash() -> None:
    base = "https://forum.example.com/"
    assert latest_feed_url(base) == "https://forum.example.com/latest.rss"
    assert top_feed_url(base, "daily") == "https://forum.example.com/top.rss?period=daily"
    assert top_feed_url(base, "weekly") == "https://forum.example.com/top.rss?period=weekly"
    assert topic_json_url(base, 1234) == "https://forum.example.com/t/1234.json"


def test_url_builders_no_trailing_slash() -> None:
    base = "https://forum.example.com"
    assert latest_feed_url(base) == "https://forum.example.com/latest.rss"
    assert top_feed_url(base, "daily") == "https://forum.example.com/top.rss?period=daily"
    assert topic_json_url(base, 42) == "https://forum.example.com/t/42.json"


# ---------------------------------------------------------------------------
# Topic-id extraction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, expected",
    [
        # /t/<id>
        ("https://forum.example.com/t/1234", 1234),
        # /t/<slug>/<id>
        ("https://forum.example.com/t/my-topic/1234", 1234),
        # /t/<id>/<post_number> — must return topic id, not post number
        ("https://forum.example.com/t/1234/5", 1234),
        # /t/<slug>/<id>/<post_number>
        ("https://forum.example.com/t/my-topic/1234/5", 1234),
        # slug with digits (not all-digit)
        ("https://forum.example.com/t/topic-100-votes/1234", 1234),
        # trailing slash on URL (canonical_url strips it)
        ("https://forum.example.com/t/my-topic/1234/", 1234),
    ],
)
def test_topic_id_from_url_valid(url: str, expected: int) -> None:
    assert topic_id_from_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        # not a /t/ path
        "https://forum.example.com/c/general/5",
        "https://forum.example.com/u/alice",
        "https://forum.example.com/",
        # /t/ with no numeric id
        "https://forum.example.com/t/non-numeric-only",
    ],
)
def test_topic_id_from_url_non_topic(url: str) -> None:
    assert topic_id_from_url(url) is None


# ---------------------------------------------------------------------------
# parse_topic
# ---------------------------------------------------------------------------


def test_parse_topic_from_fixture() -> None:
    body = (FIXTURES / "discourse_topic.json").read_bytes()
    topic, posts = parse_topic(body)

    assert topic.id == 1234
    assert topic.slug == "my-forum-topic"
    assert topic.title == "My Forum Topic"
    assert topic.like_count == 15

    assert len(posts) == 3

    # Post 1: 10 likes, text stripped from multi-paragraph HTML
    p1 = posts[0]
    assert p1.id == 5001
    assert p1.number == 1
    assert p1.like_count == 10
    assert "Hello world" in p1.text
    assert "This is the OP" in p1.text
    assert "<p>" not in p1.text

    # Post 2: no likes (empty actions_summary → 0)
    p2 = posts[1]
    assert p2.id == 5002
    assert p2.number == 2
    assert p2.like_count == 0

    # Post 3: action id=2 has 5 likes; action id=1 is not the like action
    p3 = posts[2]
    assert p3.id == 5003
    assert p3.number == 3
    assert p3.like_count == 5


def test_parse_topic_cooked_html_stripped_to_text() -> None:
    data = {
        "id": 1,
        "slug": "test",
        "title": "Test",
        "like_count": 0,
        "post_stream": {
            "posts": [
                {
                    "id": 10,
                    "post_number": 1,
                    "cooked": (
                        "<style>.a{color:red}</style>"
                        "<p>First paragraph.</p><p>Second paragraph.</p>"
                        "<script>var x=1;</script>"
                    ),
                    "actions_summary": [],
                }
            ]
        },
    }
    _, posts = parse_topic(json.dumps(data).encode())
    # The cooked path flattens through the shared feeds.html_to_text: block
    # boundaries keep adjacent paragraphs from fusing, tags are dropped, and
    # script/style content is removed (not flattened into the prose).
    # One substring asserts both presence AND the separating space, so a
    # regression that fused the blocks ("paragraph.Second") would fail here.
    assert "First paragraph. Second paragraph." in posts[0].text
    assert "<p>" not in posts[0].text
    assert "var x=1" not in posts[0].text
    assert ".a{color" not in posts[0].text


def test_parse_topic_absent_likes_default_to_zero() -> None:
    data = {
        "id": 2,
        "slug": "t",
        "title": "T",
        "like_count": 0,
        "post_stream": {
            "posts": [{"id": 20, "post_number": 1, "cooked": "<p>hi</p>", "actions_summary": []}]
        },
    }
    _, posts = parse_topic(json.dumps(data).encode())
    assert posts[0].like_count == 0


def test_parse_topic_lite_payload_returns_no_posts() -> None:
    """A minimal response with no post_stream must not raise."""
    data = {"id": 3, "title": "Lite", "slug": "lite", "like_count": 0}
    topic, posts = parse_topic(json.dumps(data).encode())
    assert topic.id == 3
    assert posts == []


def test_parse_topic_empty_payload_returns_empty_topic() -> None:
    _, posts = parse_topic(b"{}")
    assert posts == []


def test_parse_topic_malformed_json_returns_empty_topic() -> None:
    topic, posts = parse_topic(b"not json at all")
    assert topic == _EMPTY_TOPIC
    assert posts == []


def test_parse_topic_non_dict_json_returns_empty_topic() -> None:
    topic, posts = parse_topic(b"[1, 2, 3]")
    assert topic == _EMPTY_TOPIC
    assert posts == []


def test_parse_topic_skips_non_dict_post_elements() -> None:
    """A junk element inside the posts list is dropped, not crashed on."""
    data = {
        "id": 1,
        "post_stream": {
            "posts": [
                "junk",
                42,
                {"id": 9, "post_number": 1, "cooked": "<p>real</p>", "actions_summary": []},
            ]
        },
    }
    _, posts = parse_topic(json.dumps(data).encode())
    assert [p.id for p in posts] == [9]


@pytest.mark.parametrize("post_stream", ["oops", [1, 2], 7])
def test_parse_topic_wrong_typed_post_stream_degrades(post_stream: object) -> None:
    """``post_stream`` arriving as a non-dict must not raise.

    A bare ``or {}`` guard would call ``.get`` on a string/list and raise
    ``AttributeError``; the ``isinstance`` guard degrades to no posts instead.
    """
    topic, posts = parse_topic(json.dumps({"id": 5, "post_stream": post_stream}).encode())
    assert topic.id == 5
    assert posts == []


@pytest.mark.parametrize("raw_posts", ["oops", {"a": 1}, 7])
def test_parse_topic_wrong_typed_posts_degrades(raw_posts: object) -> None:
    """``post_stream.posts`` arriving as a non-list must degrade to no posts."""
    data = {"id": 6, "post_stream": {"posts": raw_posts}}
    _, posts = parse_topic(json.dumps(data).encode())
    assert posts == []


@pytest.mark.parametrize("actions_summary", [7, "oops", {"id": 2}, None])
def test_parse_topic_wrong_typed_actions_summary_degrades(actions_summary: object) -> None:
    """A non-list ``actions_summary`` must not raise; likes default to 0.

    A bare ``or []`` left an int ``actions_summary`` to crash ``for`` with
    ``TypeError``.
    """
    data = {
        "id": 1,
        "post_stream": {
            "posts": [
                {
                    "id": 9,
                    "post_number": 1,
                    "cooked": "<p>x</p>",
                    "actions_summary": actions_summary,
                }
            ]
        },
    }
    _, posts = parse_topic(json.dumps(data).encode())
    assert posts[0].like_count == 0


@pytest.mark.parametrize("cooked", [7, ["a"], {"k": "v"}, None])
def test_parse_topic_wrong_typed_cooked_degrades(cooked: object) -> None:
    """A non-string ``cooked`` is coerced to "" by ``_as_str`` before flattening,
    so ``html_to_text`` never sees a non-str and the post degrades to empty text."""
    data = {
        "id": 1,
        "post_stream": {
            "posts": [{"id": 9, "post_number": 1, "cooked": cooked, "actions_summary": []}]
        },
    }
    _, posts = parse_topic(json.dumps(data).encode())
    assert posts[0].text == ""


def test_parse_topic_non_int_count_degrades_to_zero() -> None:
    """A non-int like ``count`` must coerce to 0, keeping ``like_count: int``."""
    data = {
        "id": 1,
        "post_stream": {
            "posts": [
                {
                    "id": 9,
                    "post_number": 1,
                    "cooked": "",
                    "actions_summary": [{"id": 2, "count": "five"}],
                }
            ]
        },
    }
    _, posts = parse_topic(json.dumps(data).encode())
    assert posts[0].like_count == 0


def test_parse_topic_non_int_ids_degrade_to_zero() -> None:
    """Non-int id/post_number/like_count fields coerce to 0, not leak a str."""
    data = {
        "id": "x",
        "like_count": "y",
        "post_stream": {
            "posts": [{"id": "p", "post_number": "n", "cooked": "<p>x</p>", "actions_summary": []}]
        },
    }
    topic, posts = parse_topic(json.dumps(data).encode())
    assert topic.id == 0
    assert topic.like_count == 0
    assert posts[0].id == 0
    assert posts[0].number == 0


# ---------------------------------------------------------------------------
# discover_topic_ids
# ---------------------------------------------------------------------------


def test_discover_topic_ids_latest_rss_source_order() -> None:
    body = (FIXTURES / "discourse_latest.rss").read_bytes()
    ids = discover_topic_ids(body, "https://forum.example.com")
    # latest.rss is already newest-first; source order matches date order here
    assert ids == [101, 102, 103]


def test_discover_topic_ids_top_rss_preserves_rank_order() -> None:
    """Top feed ranks oldest-first in the fixture (Jun 01 > Jun 12 > Jun 14).

    With sort=True the result would be [203, 202, 201] (date descending).
    With sort=False the source order [201, 202, 203] must be preserved.
    """
    body = (FIXTURES / "discourse_top.rss").read_bytes()
    ids = discover_topic_ids(body, "https://forum.example.com")
    assert ids == [201, 202, 203]


def test_discover_topic_ids_drops_non_topic_links() -> None:
    feed = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>F</title><link>https://f.example.com</link>
  <item><title>Topic</title><link>https://f.example.com/t/my-topic/99</link></item>
  <item><title>Category</title><link>https://f.example.com/c/general/1</link></item>
</channel></rss>"""
    ids = discover_topic_ids(feed, "https://f.example.com")
    assert ids == [99]
