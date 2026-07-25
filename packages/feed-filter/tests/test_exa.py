"""Behavior tests for the Exa query-gather client.

Network-free: every client is an ``httpx.MockTransport``, so the success,
malformed-payload, and failure paths are exercised without touching Exa.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest

from feed_filter.config import SUMMARY_PREVIEW_CHARS
from feed_filter.exa import ExaError, search
from feed_filter.feeds import MAX_BODY_CHARS


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXA_API_KEY", "test-key")


def _payload(results: list[Any], cost: float = 0.007) -> dict[str, Any]:
    return {"results": results, "costDollars": {"total": cost}}


def _run(
    handler: Callable[[httpx.Request], httpx.Response],
    query: str = "q",
    num_results: int = 10,
) -> Any:
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return search(client, query, num_results=num_results)


def test_returns_hits_and_cost() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_payload([{"url": "https://a.example/p", "title": "T", "text": "body"}])
        )

    outcome = _run(handler)

    assert outcome.error is None
    assert [(h.url, h.title, h.text) for h in outcome.hits] == [
        ("https://a.example/p", "T", "body")
    ]
    assert outcome.cost_dollars == 0.007


def test_sends_the_query_and_the_key() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["key"] = request.headers.get("x-api-key")
        captured["body"] = httpx.Response(200, content=request.content).json()
        return httpx.Response(200, json=_payload([]))

    # A non-default numResults, so the assertion cannot pass against a hardcoded one.
    _run(handler, query="pages about walking and mapping", num_results=7)

    assert captured["key"] == "test-key"
    assert captured["body"]["query"] == "pages about walking and mapping"
    assert captured["body"]["numResults"] == 7
    assert captured["body"]["type"] == "neural"
    # Buy exactly what the CLI can emit — a query entry has no body cache behind it,
    # so text past the preview is unreachable.
    assert captured["body"]["contents"] == {"text": {"maxCharacters": SUMMARY_PREVIEW_CHARS}}


def test_missing_key_raises_rather_than_calling_out(monkeypatch: pytest.MonkeyPatch) -> None:
    """No key is a config error the caller reports once, not a per-query failure."""
    called = False

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=_payload([]))

    monkeypatch.delenv("EXA_API_KEY")
    with pytest.raises(ExaError, match="EXA_API_KEY"):
        _run(handler)
    assert not called


def test_http_error_becomes_an_outcome_error_not_an_exception() -> None:
    """A failed query must not abort the run's other queries."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream boom")

    outcome = _run(handler)

    assert outcome.hits == ()
    assert outcome.cost_dollars == 0.0
    assert outcome.error is not None


def test_transport_error_becomes_an_outcome_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    outcome = _run(handler)

    assert outcome.hits == ()
    assert outcome.error is not None


def test_non_object_payload_is_reported_not_trusted() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=["not", "an", "object"])

    outcome = _run(handler)

    assert outcome.hits == ()
    assert outcome.error is not None


def test_malformed_results_are_skipped_individually() -> None:
    """One bad row must not lose the good rows beside it."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_payload(
                [
                    "a bare string",  # not an object
                    {"title": "no url"},  # unusable without a URL
                    {"url": "https://ok.example/p", "title": None, "text": None},
                    {"url": "  https://spaced.example/p  ", "title": " T "},
                ]
            ),
        )

    outcome = _run(handler)

    assert [h.url for h in outcome.hits] == ["https://ok.example/p", "https://spaced.example/p"]
    assert outcome.hits[0].title == ""
    assert outcome.hits[0].text == ""
    assert outcome.hits[1].title == "T"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "mailto:a@b.example",
        "/relative/path",  # no document to resolve against — malformed, not relative
        "ftp://a.example/p",
        "https://[abc",  # urlsplit raises: unbalanced IPv6 bracket
        "https://a.example:99999/p",  # .port raises: out of range
        "https://a.example:port/p",  # .port raises: not a number
    ],
)
def test_non_web_urls_are_dropped(url: str) -> None:
    """These would be handed to a fetch, written into a note, and keyed in the
    seen-store, so they must never become a hit."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_payload([{"url": url}, {"url": "https://ok.example/p"}]))

    outcome = _run(handler)

    assert [h.url for h in outcome.hits] == ["https://ok.example/p"]


def test_non_list_results_yields_no_hits() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": {"not": "a list"}, "costDollars": {"total": 1}})

    outcome = _run(handler)

    assert outcome.hits == ()
    assert outcome.error is None  # a well-formed envelope with no usable rows, not a failure
    assert outcome.cost_dollars == 1.0


@pytest.mark.parametrize(
    ("token", "why"),
    [
        ("1" + "0" * 400, "float() raises OverflowError, which the CLI does not catch"),
        ("Infinity", "json.loads accepts it; emitting it produces a non-JSON token"),
        ("NaN", "likewise non-JSON once emitted"),
    ],
)
def test_unusable_cost_numbers_fall_back_to_zero(token: str, why: str) -> None:
    body = f'{{"results": [], "costDollars": {{"total": {token}}}}}'

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "application/json"}
        )

    outcome = _run(handler)

    assert outcome.cost_dollars == 0.0, why


def test_boolean_cost_is_not_a_cost() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [], "costDollars": {"total": True}})

    assert _run(handler).cost_dollars == 0.0


def test_non_numeric_cost_falls_back_to_zero() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": [], "costDollars": {"total": "expensive"}})

    outcome = _run(handler)

    assert outcome.cost_dollars == 0.0


def test_page_text_is_capped() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_payload([{"url": "https://a.example/p", "text": "x" * (MAX_BODY_CHARS + 500)}]),
        )

    outcome = _run(handler)

    assert len(outcome.hits[0].text) == MAX_BODY_CHARS


def test_missing_cost_defaults_to_zero() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"results": []})

    outcome = _run(handler)

    assert outcome.cost_dollars == 0.0
    assert outcome.error is None


def test_lone_surrogates_are_replaced_not_propagated() -> None:
    """A `\\udXXX` escape survives `json.loads` but cannot be UTF-8 encoded, so it
    would abort the run downstream. Fed as a raw body, since building the string in
    Python would not reproduce how it arrives."""
    body = (
        '{"results": [{"url": "https://a.example/p?q=\\ud800",'
        ' "title": "T\\ud800", "text": "b\\ud800"}],'
        ' "costDollars": {"total": 0.007}}'
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, content=body.encode(), headers={"content-type": "application/json"}
        )

    outcome = _run(handler)

    hit = outcome.hits[0]
    for field in (hit.url, hit.title, hit.text):
        field.encode("utf-8")  # would raise on a surviving lone surrogate
    assert hit.url.startswith("https://a.example/p")
